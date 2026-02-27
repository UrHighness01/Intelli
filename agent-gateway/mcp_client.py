"""
mcp_client.py — MCP (Model Context Protocol) client for Intelli Agent Gateway.

Manages MCP server subprocesses (stdio transport), discovers their tools, and
registers them in the tool_runner._REGISTRY so the agent can call them via the
normal TOOL_CALL: protocol.

Config file: ~/.intelli/mcp_servers.json
Format:
  [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home"],
      "env": {}
    }
  ]

Hot-reload: call reload() to apply config changes without restarting the gateway.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(os.environ.get(
    'INTELLI_MCP_CONFIG',
    Path.home() / '.intelli' / 'mcp_servers.json',
))
_CALL_TIMEOUT = float(os.environ.get('INTELLI_MCP_TIMEOUT', '30'))

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_servers: dict[str, '_MCPServer'] = {}   # name -> MCPServer


# ---------------------------------------------------------------------------
# MCP Server process wrapper
# ---------------------------------------------------------------------------

class _MCPServer:
    """Manages a single MCP server subprocess (stdio transport)."""

    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self._proc: subprocess.Popen | None = None
        self._rlock = threading.Lock()          # for stdin write
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._reader: threading.Thread | None = None
        self._next_id = 1
        self._tools: list[dict] = []            # MCP tool descriptors
        self.status = 'stopped'                 # stopped | starting | ready | error
        self.error: str = ''
        self.started_at: float = 0.0

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> bool:
        """Launch the subprocess and perform MCP initialization."""
        if self._proc and self._proc.poll() is None:
            return True  # already running
        self.status = 'starting'
        self.error = ''
        try:
            merged_env = {**os.environ, **self.env}
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                text=False,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            self.status = 'error'
            self.error = f'command not found: {self.command!r}'
            log.error('[MCP:%s] %s', self.name, self.error)
            return False
        except Exception as exc:
            self.status = 'error'
            self.error = str(exc)
            log.error('[MCP:%s] start failed: %s', self.name, exc)
            return False

        # Start background reader thread
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name=f'mcp-{self.name}-reader'
        )
        self._reader.start()

        # MCP initialization handshake
        try:
            resp = self._rpc('initialize', {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'intelli-gateway', 'version': '1.0'},
            })
            if resp is None or 'error' in resp:
                raise RuntimeError(resp.get('error', {}).get('message', 'init failed') if resp else 'timeout')
            # Send initialized notification (no response expected)
            self._notify('notifications/initialized', {})
            self.started_at = time.time()
        except Exception as exc:
            self.status = 'error'
            self.error = f'init failed: {exc}'
            log.error('[MCP:%s] %s', self.name, self.error)
            return False

        # Discover tools
        self._tools = self._list_tools()
        self.status = 'ready'
        log.info('[MCP:%s] ready — %d tool(s)', self.name, len(self._tools))
        return True

    def stop(self):
        """Terminate the subprocess."""
        self.status = 'stopped'
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self._tools = []

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # --- tool management ----------------------------------------------------

    def _list_tools(self) -> list[dict]:
        resp = self._rpc('tools/list', {})
        if not resp or 'error' in resp:
            log.warning('[MCP:%s] tools/list failed: %s', self.name, resp)
            return []
        tools = resp.get('result', {}).get('tools', [])
        log.debug('[MCP:%s] discovered tools: %s', self.name, [t.get('name') for t in tools])
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call an MCP tool and return the result content."""
        resp = self._rpc('tools/call', {'name': tool_name, 'arguments': arguments})
        if resp is None:
            return '[ERROR] MCP call timed out'
        if 'error' in resp:
            msg = resp['error'].get('message', str(resp['error']))
            return f'[ERROR] MCP error: {msg}'
        content_list = resp.get('result', {}).get('content', [])
        # Flatten content blocks to a string
        parts = []
        for block in content_list:
            btype = block.get('type', '')
            if btype == 'text':
                parts.append(block.get('text', ''))
            elif btype == 'image':
                parts.append(f'[image: {block.get("mimeType","?")}]')
            elif btype == 'resource':
                uri = block.get('resource', {}).get('uri', '?')
                parts.append(f'[resource: {uri}]')
        return '\n'.join(parts) if parts else '(empty response)'

    # --- JSON-RPC transport -------------------------------------------------

    def _next_rpc_id(self) -> int:
        with self._rlock:
            rid = self._next_id
            self._next_id += 1
            return rid

    def _rpc(self, method: str, params: dict, timeout: float = _CALL_TIMEOUT) -> dict | None:
        """Send a JSON-RPC request and wait for the response."""
        rid = self._next_rpc_id()
        msg = {'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}
        event = threading.Event()
        self._pending[rid] = event
        self._send(msg)
        if not event.wait(timeout=timeout):
            self._pending.pop(rid, None)
            log.warning('[MCP:%s] RPC %s timed out (id=%d)', self.name, method, rid)
            return None
        return self._results.pop(rid, None)

    def _notify(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        msg = {'jsonrpc': '2.0', 'method': method, 'params': params}
        self._send(msg)

    def _send(self, msg: dict):
        if not self._proc or not self._proc.stdin:
            return
        data = (json.dumps(msg) + '\n').encode()
        try:
            with self._rlock:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
        except Exception as exc:
            log.warning('[MCP:%s] send error: %s', self.name, exc)

    def _read_loop(self):
        """Background thread: read stdout and dispatch JSON-RPC responses."""
        assert self._proc and self._proc.stdout
        while True:
            try:
                line = self._proc.stdout.readline()
            except Exception:
                break
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            rid = msg.get('id')
            if rid is not None and rid in self._pending:
                self._results[rid] = msg
                ev = self._pending.pop(rid, None)
                if ev:
                    ev.set()
            # Ignore server-side notifications

    # --- public info --------------------------------------------------------

    def public_info(self) -> dict:
        return {
            'name': self.name,
            'command': self.command,
            'args': self.args,
            'status': self.status,
            'error': self.error,
            'tool_count': len(self._tools),
            'tools': [t.get('name') for t in self._tools],
            'started_at': self.started_at or None,
            'uptime_s': round(time.time() - self.started_at, 1) if self.started_at else None,
        }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def _register_server_tools(srv: '_MCPServer') -> int:
    """Register MCP tools from a server into tool_runner._REGISTRY."""
    from tools.tool_runner import register_tool

    count = 0
    for mcp_tool in srv._tools:
        tool_name = mcp_tool.get('name', '')
        if not tool_name:
            continue
        prefixed = f'{srv.name}__{tool_name}'       # e.g. "filesystem__read_file"
        description = mcp_tool.get('description', f'MCP tool from server {srv.name!r}')

        # Build args schema from MCP inputSchema
        input_schema = mcp_tool.get('inputSchema', {})
        properties = input_schema.get('properties', {})
        required_fields = set(input_schema.get('required', []))
        args_spec: dict[str, dict] = {}
        for prop_name, prop in properties.items():
            args_spec[prop_name] = {
                'type': prop.get('type', 'string'),
                'description': prop.get('description', ''),
                'required': prop_name in required_fields,
            }

        # Capture loop variables
        _srv = srv
        _tool_name = tool_name

        def _fn(_s=_srv, _t=_tool_name, **kwargs) -> str:
            return _s.call_tool(_t, kwargs)

        _fn.__name__ = prefixed
        register_tool(prefixed, _fn, description, args_spec)
        count += 1

    return count


def _unregister_server_tools(srv_name: str) -> None:
    """Remove all tools registered for a server from _REGISTRY."""
    from tools import tool_runner
    prefix = f'{srv_name}__'
    to_remove = [k for k in list(tool_runner._REGISTRY) if k.startswith(prefix)]
    for k in to_remove:
        del tool_runner._REGISTRY[k]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config() -> list[dict]:
    """Read and return mcp_servers.json config (creates empty file if absent)."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _CONFIG_PATH.exists():
        _CONFIG_PATH.write_text('[]')
        return []
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception as exc:
        log.error('Failed to load MCP config: %s', exc)
        return []


def save_config(servers: list[dict]) -> None:
    """Persist server config to mcp_servers.json."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(servers, indent=2))


def start_all() -> None:
    """Start all configured MCP servers and register their tools."""
    cfg = load_config()
    for entry in cfg:
        _start_server(entry)


def _start_server(entry: dict) -> '_MCPServer':
    name = entry['name']
    with _lock:
        if name in _servers:
            old = _servers[name]
            old.stop()
            _unregister_server_tools(name)
        srv = _MCPServer(
            name=name,
            command=entry['command'],
            args=entry.get('args', []),
            env=entry.get('env', {}),
        )
        _servers[name] = srv

    srv.start()
    if srv.status == 'ready':
        n = _register_server_tools(srv)
        log.info('[MCP] registered %d tool(s) for server %r', n, name)
    return srv


def stop_server(name: str) -> bool:
    with _lock:
        srv = _servers.pop(name, None)
    if not srv:
        return False
    srv.stop()
    _unregister_server_tools(name)
    return True


def restart_server(name: str) -> bool:
    with _lock:
        srv = _servers.get(name)
    if not srv:
        return False
    _unregister_server_tools(name)
    ok = srv.restart()
    if ok:
        _register_server_tools(srv)
    return ok


def reload() -> dict:
    """Hot-reload: read config, stop removed servers, start new/changed ones."""
    cfg = load_config()
    cfg_names = {e['name'] for e in cfg}

    # Stop servers no longer in config
    with _lock:
        for name in list(_servers):
            if name not in cfg_names:
                srv = _servers.pop(name)
                srv.stop()
                _unregister_server_tools(name)

    # Start/restart servers in config
    started: list[str] = []
    errors: list[str] = []
    for entry in cfg:
        srv = _start_server(entry)
        if srv.status == 'ready':
            started.append(srv.name)
        else:
            errors.append(f"{srv.name}: {srv.error}")

    return {'started': started, 'errors': errors}


def list_servers() -> list[dict]:
    with _lock:
        return [srv.public_info() for srv in _servers.values()]


def get_server(name: str) -> dict | None:
    with _lock:
        srv = _servers.get(name)
    return srv.public_info() if srv else None


def add_server(entry: dict) -> '_MCPServer':
    """Add a server to config and start it immediately."""
    cfg = load_config()
    # Replace if name already exists
    cfg = [e for e in cfg if e['name'] != entry['name']]
    cfg.append(entry)
    save_config(cfg)
    return _start_server(entry)


def remove_server(name: str) -> bool:
    """Stop and remove a server from config."""
    cfg = load_config()
    cfg = [e for e in cfg if e['name'] != name]
    save_config(cfg)
    return stop_server(name)
