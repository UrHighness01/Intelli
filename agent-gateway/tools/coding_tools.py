"""coding_tools.py — File system + shell tools for the Intelli Coding Agent.

All file operations are sandboxed to ``_CODE_ROOT`` (configurable via
``INTELLI_CODE_DIR`` env var, defaults to ``~/intelli-workspace``).

Shell execution is synchronous with a hard timeout and output cap.
Set ``INTELLI_SHELL_DISABLED=1`` to prevent any shell commands.

Tools
-----
  file_read    — read a file (text or binary →  base64)
  file_write   — write / create a file
  file_patch   — apply a unified diff to an existing file
  file_delete  — delete a file or empty directory
  file_list    — list files in a directory (tree, depth-limited)
  shell_exec   — run a shell command in the workspace root
"""

from __future__ import annotations

import base64
import difflib
import os
import pathlib
import subprocess
import re
import traceback
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_DIR  = pathlib.Path.home() / 'intelli-workspace'
_CODE_ROOT    = pathlib.Path(
    os.environ.get('INTELLI_CODE_DIR', str(_DEFAULT_DIR))
).expanduser().resolve()

_MAX_READ     = 40_000   # chars
_MAX_OUTPUT   = 8_000    # chars for shell stdout+stderr
_MAX_WRITE    = 200_000  # bytes
_SHELL_TIMEOUT_DEFAULT = 30  # seconds
_SHELL_TIMEOUT_MAX     = 120
_MAX_TREE_DEPTH        = 4
_MAX_TREE_ENTRIES      = 200
_SHELL_DISABLED        = os.environ.get('INTELLI_SHELL_DISABLED', '').lower() in ('1', 'true', 'yes')
_SANDBOX_MODE          = os.environ.get('INTELLI_SANDBOX_MODE', '').lower()  # '' | 'docker'

# Ensure workspace exists
_CODE_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------

def _safe_path(rel_path: str) -> pathlib.Path:
    """Resolve rel_path relative to _CODE_ROOT, raising PermissionError if it
    would escape the sandbox."""
    # Strip leading slashes / drive letters so users can't force absolute paths
    cleaned = re.sub(r'^[/\\]+', '', rel_path.replace('\\', '/'))
    resolved = (_CODE_ROOT / cleaned).resolve()
    try:
        resolved.relative_to(_CODE_ROOT)
    except ValueError:
        raise PermissionError(
            f'Path {rel_path!r} escapes the coding workspace at {_CODE_ROOT}. '
            f'Use relative paths only.'
        )
    return resolved


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def file_read(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read a file from the coding workspace.

    Args:
        path: Relative path inside the workspace.
        start_line: 1-based first line to return (0 = from beginning).
        end_line: 1-based last line to return (0 = until end).
    """
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return f'[ERROR] {e}'

    if not p.exists():
        return f'[ERROR] File not found: {path!r}\nWorkspace root: {_CODE_ROOT}'
    if not p.is_file():
        return f'[ERROR] {path!r} is not a file'

    size = p.stat().st_size
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception:
        # Fallback: return base64 for binary files
        raw = p.read_bytes()[:_MAX_READ]
        b64 = base64.b64encode(raw).decode()
        return f'[binary file, {size} bytes, base64]\n{b64}'

    lines = text.splitlines(keepends=True)
    total = len(lines)

    # Slice if requested
    if start_line > 0 or end_line > 0:
        s = max(0, start_line - 1)
        e = end_line if end_line > 0 else total
        lines = lines[s:e]
        text = ''.join(lines)
        header = f'[lines {start_line}-{min(end_line, total)}/{total}] {path}\n'
    else:
        header = f'[{total} lines, {size} bytes] {path}\n'

    if len(text) > _MAX_READ:
        text = text[:_MAX_READ]
        header = header.rstrip('\n') + ' (truncated)\n'

    return header + text


def file_write(path: str, content: str) -> str:
    """Create or overwrite a file in the coding workspace.

    Args:
        path: Relative path (parent directories are created automatically).
        content: Full UTF-8 file content.
    """
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return f'[ERROR] {e}'

    if len(content.encode('utf-8')) > _MAX_WRITE:
        return f'[ERROR] Content too large ({len(content)} chars). Max {_MAX_WRITE} bytes.'

    existed = p.exists()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
    except Exception as exc:
        return f'[ERROR] Write failed: {exc}'

    verb = 'Updated' if existed else 'Created'
    return f'{verb} {path} ({len(content.encode())} bytes, {content.count(chr(10))+1} lines).'


def file_patch(path: str, diff: str) -> str:
    """Apply a unified diff to a file in the coding workspace.

    The diff should be in standard unified format (output of `diff -u` or
    `git diff`). Lines starting with `+` are added, `-` removed.

    Args:
        path: Relative path of the file to patch.
        diff: Unified diff text.
    """
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return f'[ERROR] {e}'

    if not p.exists():
        return f'[ERROR] Cannot patch non-existent file {path!r}. Use file_write to create it first.'

    try:
        original = p.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
    except Exception as exc:
        return f'[ERROR] Cannot read {path}: {exc}'

    # Parse unified diff into [(operation, line_content), ...]
    diff_lines = diff.splitlines(keepends=True)
    result_lines = list(original)  # default: keep original

    try:
        result_lines = _apply_unified_diff(original, diff_lines)
    except Exception as exc:
        return (
            f'[ERROR] Patch failed: {exc}\n'
            f'Tip: use file_write to overwrite the whole file instead.'
        )

    patched = ''.join(result_lines)
    try:
        p.write_text(patched, encoding='utf-8')
    except Exception as exc:
        return f'[ERROR] Write failed after patching: {exc}'

    added   = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
    removed = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))
    return f'Patched {path}: +{added} lines, -{removed} lines.'


def _apply_unified_diff(original: list[str], diff_lines: list[str]) -> list[str]:
    """Pure-Python unified diff applicator (handles @@ hunks)."""
    result = []
    orig_idx = 0   # 0-based index into original

    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith('@@'):
            # Parse hunk header: @@ -old_start,old_len +new_start,new_len @@
            m = re.match(r'@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@', line)
            if not m:
                i += 1
                continue
            old_start = int(m.group(1)) - 1  # convert to 0-based
            # Copy unchanged lines before this hunk
            while orig_idx < old_start:
                result.append(original[orig_idx])
                orig_idx += 1
            i += 1
            # Apply hunk lines
            while i < len(diff_lines) and not diff_lines[i].startswith('@@'):
                hl = diff_lines[i]
                if hl.startswith('+'):
                    result.append(hl[1:])
                elif hl.startswith('-'):
                    orig_idx += 1  # skip original line
                else:  # context line (space or \)
                    if not hl.startswith('\\'):
                        result.append(original[orig_idx])
                        orig_idx += 1
                i += 1
        else:
            i += 1

    # Append remaining original lines after last hunk
    result.extend(original[orig_idx:])
    return result


def file_delete(path: str) -> str:
    """Delete a file or empty directory from the coding workspace.

    Args:
        path: Relative path to delete.
    """
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return f'[ERROR] {e}'

    if not p.exists():
        return f'[ERROR] Not found: {path!r}'
    try:
        if p.is_dir():
            p.rmdir()  # only succeeds if empty
        else:
            p.unlink()
        return f'Deleted {path}.'
    except Exception as exc:
        return f'[ERROR] Delete failed: {exc}'


def file_list(path: str = '', depth: int = 3) -> str:
    """List files and directories in the coding workspace.

    Args:
        path: Relative subdirectory to list (empty = workspace root).
        depth: Max directory depth (1-4, default 3).
    """
    try:
        root = _safe_path(path) if path else _CODE_ROOT
    except PermissionError as e:
        return f'[ERROR] {e}'

    if not root.exists():
        return f'[ERROR] Directory not found: {path!r}'
    if not root.is_dir():
        return f'[ERROR] {path!r} is not a directory'

    depth = max(1, min(depth, _MAX_TREE_DEPTH))
    lines: list[str] = [f'Workspace: {_CODE_ROOT}', f'Listing: {root}\n']
    count = 0

    def _walk(d: pathlib.Path, prefix: str, current_depth: int):
        nonlocal count
        if current_depth > depth or count > _MAX_TREE_ENTRIES:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        for i, entry in enumerate(entries):
            if count > _MAX_TREE_ENTRIES:
                lines.append(f'{prefix}… (truncated)')
                return
            connector = '└── ' if i == len(entries) - 1 else '├── '
            if entry.is_dir():
                lines.append(f'{prefix}{connector}{entry.name}/')
                count += 1
                child_prefix = prefix + ('    ' if i == len(entries) - 1 else '│   ')
                _walk(entry, child_prefix, current_depth + 1)
            else:
                size = entry.stat().st_size
                size_str = f' ({size:,} B)' if size < 10_000 else f' ({size//1024} KB)'
                lines.append(f'{prefix}{connector}{entry.name}{size_str}')
                count += 1

    _walk(root, '', 1)
    lines.append(f'\n{count} entries (depth={depth})')
    return '\n'.join(lines)


def shell_exec(cmd: str, timeout: int = _SHELL_TIMEOUT_DEFAULT) -> str:
    """Execute a shell command in the coding workspace root.

    Args:
        cmd: Shell command to run (executed via /bin/sh -c).
        timeout: Max seconds to wait (default 30, max 120).

    Returns the combined stdout + stderr output, truncated to 8 000 chars.
    Set INTELLI_SHELL_DISABLED=1 to block all shell execution.
    """
    if _SHELL_DISABLED:
        return '[ERROR] Shell execution is disabled (INTELLI_SHELL_DISABLED=1).'

    timeout = max(1, min(int(timeout), _SHELL_TIMEOUT_MAX))

    # Route through Docker sandbox when INTELLI_SANDBOX_MODE=docker
    if _SANDBOX_MODE == 'docker':
        try:
            import sys as _sys
            _gw_dir = str(_CODE_ROOT.parent)
            if _gw_dir not in _sys.path:
                _sys.path.insert(0, _gw_dir)
            from sandbox.docker_runner import DockerSandboxRunner  # type: ignore
            _runner = DockerSandboxRunner()
            _res = _runner.execute('shell', {
                'cmd': cmd,
                'timeout': timeout,
                'cwd': '/workspace',
                '_workspace_dir': str(_CODE_ROOT),
            })
            _out   = _res.get('output', '')
            _code  = _res.get('exit_code', 0)
            if len(_out) > _MAX_OUTPUT:
                _out = _out[:_MAX_OUTPUT] + f'\n… (truncated, {len(_out)} chars total)'
            return f'[exit {_code}] $ {cmd}\n{_out}'
        except Exception as _exc:
            return f'[ERROR] Docker sandbox failed: {_exc}'

    # Very basic dangerous-command check
    _BLOCKED = ('rm -rf /', 'mkfs', 'dd if=/dev/zero', ':(){ :|:& };:')
    for blocked in _BLOCKED:
        if blocked in cmd:
            return f'[BLOCKED] Command contains prohibited pattern: {blocked!r}'

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(_CODE_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = proc.stdout or ''
        err = proc.stderr or ''
        combined = out + (f'\n[stderr]\n{err}' if err.strip() else '')
        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT] + f'\n… (truncated, {len(combined)} chars total)'
        header = f'[exit {proc.returncode}] $ {cmd}\n'
        return header + combined
    except subprocess.TimeoutExpired:
        return f'[TIMEOUT] Command exceeded {timeout}s: {cmd!r}'
    except Exception as exc:
        return f'[ERROR] shell_exec failed: {exc}\n{traceback.format_exc(limit=2)}'


# ---------------------------------------------------------------------------
# Tool registry for tool_runner.py
# ---------------------------------------------------------------------------

CODING_TOOLS: Dict[str, Any] = {
    'file_read': {
        'fn': file_read,
        'description': (
            'Read a file from the coding workspace. '
            'Returns the file content as plain text. '
            'Use start_line/end_line to read a specific range.'
        ),
        'args': {
            'path':       {'type': 'string',  'required': True,  'description': 'Relative path inside the workspace'},
            'start_line': {'type': 'integer', 'required': False, 'description': '1-based first line (default: beginning)'},
            'end_line':   {'type': 'integer', 'required': False, 'description': '1-based last line (default: end of file)'},
        },
    },
    'file_write': {
        'fn': file_write,
        'description': (
            'Create or overwrite a file in the coding workspace. '
            'Parent directories are created automatically. '
            'Always provide the COMPLETE file content, not just a fragment.'
        ),
        'args': {
            'path':    {'type': 'string', 'required': True, 'description': 'Relative path inside the workspace'},
            'content': {'type': 'string', 'required': True, 'description': 'Full UTF-8 file content to write'},
        },
    },
    'file_patch': {
        'fn': file_patch,
        'description': (
            'Apply a unified diff (patch) to an existing file in the workspace. '
            'Use this when you want to modify only specific lines. '
            'If patching fails, fall back to file_write with the complete new content.'
        ),
        'args': {
            'path': {'type': 'string', 'required': True, 'description': 'Relative path of the file to patch'},
            'diff': {'type': 'string', 'required': True, 'description': 'Unified diff text (standard `diff -u` format)'},
        },
    },
    'file_delete': {
        'fn': file_delete,
        'description': 'Delete a file or empty directory from the coding workspace.',
        'args': {
            'path': {'type': 'string', 'required': True, 'description': 'Relative path to delete'},
        },
    },
    'file_list': {
        'fn': file_list,
        'description': (
            'List files and directories in the coding workspace as a tree. '
            'Call with no arguments to see the full workspace structure.'
        ),
        'args': {
            'path':  {'type': 'string',  'required': False, 'description': 'Subdirectory to list (default: workspace root)'},
            'depth': {'type': 'integer', 'required': False, 'description': 'Max depth to show (1-4, default 3)'},
        },
    },
    'shell_exec': {
        'fn': shell_exec,
        'description': (
            'Run a shell command in the coding workspace directory. '
            'Use for: running tests (pytest, npm test), linters, git operations, '
            'installing packages, building projects, or any CLI task. '
            'Output is capped at 8 000 chars. Disabled if INTELLI_SHELL_DISABLED=1.'
        ),
        'args': {
            'cmd':     {'type': 'string',  'required': True,  'description': 'Shell command to execute'},
            'timeout': {'type': 'integer', 'required': False, 'description': 'Max seconds to wait (default 30, max 120)'},
        },
    },
}


def code_root() -> str:
    """Return the current coding workspace root path."""
    return str(_CODE_ROOT)
