"""Chat session persistence.

Saves every chat turn to JSONL files so conversations survive gateway restarts.
Provides list, retrieve, search, and delete operations for the sessions API.

Storage layout:
  ~/.intelli/sessions/index.json           – lightweight index (id, preview, ts)
  ~/.intelli/sessions/<session_id>.jsonl   – one JSON line per message

Environment variables:
  INTELLI_SESSIONS_DIR  – override the default storage directory.
  INTELLI_MAX_SESSIONS  – max sessions to keep before pruning oldest (default 500).
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import uuid
from typing import Optional

# ---------------------------------------------------------------------------
# Storage location
# ---------------------------------------------------------------------------
_SESSIONS_DIR: pathlib.Path = pathlib.Path(
    os.environ.get(
        'INTELLI_SESSIONS_DIR',
        str(pathlib.Path.home() / '.intelli' / 'sessions'),
    )
)
_INDEX_FILE = _SESSIONS_DIR / 'index.json'
_MAX_SESSIONS = int(os.environ.get('INTELLI_MAX_SESSIONS', '500'))
_MAX_PREVIEW  = 140  # chars shown in session list


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _ensure_dir() -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


import re as _re


def _session_path(session_id: str) -> pathlib.Path:
    """Return the .jsonl path for *session_id* after verifying it stays within _SESSIONS_DIR.

    Uses os.path.realpath (PathNormalization) + startswith (SafeAccessCheck) so
    CodeQL's two-state path-injection model sees both required steps.
    """
    safe_name = _re.sub(r'[^a-zA-Z0-9_-]', '', session_id) + '.jsonl'
    joined = os.path.realpath(os.path.join(str(_SESSIONS_DIR), safe_name))
    base = os.path.realpath(str(_SESSIONS_DIR))
    if not joined.startswith(base + os.sep):
        raise PermissionError('Invalid session ID — path traversal detected')
    return pathlib.Path(joined)


def _load_index() -> list[dict]:
    if not _INDEX_FILE.exists():
        return []
    try:
        return json.loads(_INDEX_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []


def _save_index(index: list[dict]) -> None:
    _INDEX_FILE.write_text(json.dumps(index, indent=2), encoding='utf-8')


def _prune_index(index: list[dict]) -> list[dict]:
    """Remove oldest sessions if over the limit, deleting their JSONL files too."""
    if len(index) <= _MAX_SESSIONS:
        return index
    index.sort(key=lambda x: x.get('last_ts', 0))
    to_del = index[: len(index) - _MAX_SESSIONS]
    for old in to_del:
        try:
            _session_path(old['id']).unlink(missing_ok=True)
        except Exception:
            pass
    return index[len(index) - _MAX_SESSIONS :]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def new_session_id() -> str:
    """Generate a new unique session ID."""
    return str(uuid.uuid4())


def save_message(
    session_id: str,
    role: str,
    content: str,
    meta: Optional[dict] = None,
) -> None:
    """Append a single message to the session file and update the index.

    Args:
        session_id: Unique session identifier (UUID recommended).
        role:       'user' | 'assistant' | 'system'
        content:    Message text.
        meta:       Optional extra fields (provider, model, tokens, …).
    """
    _ensure_dir()
    entry: dict = {'role': role, 'content': content, 'ts': time.time()}
    if meta:
        entry.update(meta)

    path = _session_path(session_id)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + '\n')

    # ---------- update index ----------
    index = _load_index()
    preview = content[: _MAX_PREVIEW].replace('\n', ' ')
    found: Optional[dict] = None
    for item in index:
        if item.get('id') == session_id:
            found = item
            break

    if found:
        found['last_ts'] = time.time()
        found['msg_count'] = found.get('msg_count', 0) + 1
        if role == 'user':
            found['preview'] = preview
    else:
        index.append(
            {
                'id':         session_id,
                'created_at': time.time(),
                'last_ts':    time.time(),
                'preview':    preview if role == 'user' else '…',
                'msg_count':  1,
            }
        )

    index = _prune_index(index)
    index.sort(key=lambda x: x.get('last_ts', 0), reverse=True)
    _save_index(index)


def list_sessions(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return session summaries sorted by most-recently-active."""
    index = _load_index()
    return index[offset : offset + limit]


def get_session(session_id: str) -> list[dict]:
    """Return all messages in a session in chronological order."""
    path = _session_path(session_id)
    if not path.exists():
        return []
    messages: list[dict] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except Exception:
            pass
    return messages


def delete_session(session_id: str) -> bool:
    """Delete a session file and remove it from the index."""
    path = _session_path(session_id)
    if not path.exists():
        return False
    path.unlink()
    index = [x for x in _load_index() if x.get('id') != session_id]
    _save_index(index)
    return True


def search_sessions(query: str, limit: int = 20) -> list[dict]:
    """Return session summaries whose preview or content contains *query*.

    First searches index previews (fast), then falls back to full-text scan.
    """
    q = query.lower()
    index = _load_index()
    results: list[dict] = []
    checked: set[str] = set()

    # Fast: preview index scan
    for item in index:
        if q in item.get('preview', '').lower():
            results.append(item)
            checked.add(item['id'])
        if len(results) >= limit:
            return results

    # Slow: full-text scan of remaining JSONL files
    for item in index:
        if item['id'] in checked:
            continue
        for msg in get_session(item['id']):
            if q in msg.get('content', '').lower():
                results.append(item)
                break
        if len(results) >= limit:
            break

    return results


def session_stats(session_id: str) -> dict:
    """Return basic stats about a session (message counts, token estimates)."""
    msgs = get_session(session_id)
    user_msgs = [m for m in msgs if m.get('role') == 'user']
    asst_msgs = [m for m in msgs if m.get('role') == 'assistant']
    total_chars = sum(len(m.get('content', '')) for m in msgs)
    return {
        'session_id':  session_id,
        'total_msgs':  len(msgs),
        'user_msgs':   len(user_msgs),
        'asst_msgs':   len(asst_msgs),
        'total_chars': total_chars,
        'est_tokens':  total_chars // 4,  # rough estimate
    }
