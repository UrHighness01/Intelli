"""approval_gate.py — Blocking approval gate for agent tool calls.

When an agent tool is marked with a requires-approval flag, tool_runner
calls ``register()`` to create a pending approval entry and then
``wait_for_decision()`` to block the current thread until the user
approves or denies the action (or the timeout expires).

REST exposure (wired in app.py):
  GET  /agent/approvals                   -> list_pending()
  POST /agent/approvals/{id}/approve      -> approve(id)
  POST /agent/approvals/{id}/deny         -> deny(id)
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

# How long (seconds) to block before auto-denying unanswered requests
DEFAULT_TIMEOUT = float(os.environ.get('INTELLI_APPROVAL_TIMEOUT', '60'))

_LOCK: threading.Lock = threading.Lock()
_PENDING: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register(
    tool: str,
    args: dict[str, Any],
    session_id: str = '',
) -> str:
    """Register a pending approval.

    Returns the approval ``id`` immediately (non-blocking).
    The caller should then call ``wait_for_decision(id)`` to block.
    """
    aid = _make_id()
    ev = threading.Event()
    with _LOCK:
        _PENDING[aid] = {
            'id':         aid,
            'tool':       tool,
            'args':       args,
            'session_id': session_id,
            'ts':         time.time(),
            '_event':     ev,
            '_approved':  False,
        }
    return aid


def wait_for_decision(aid: str, timeout: float | None = None) -> bool:
    """Block until the user approves/denies, or the timeout expires.

    Returns ``True`` if approved, ``False`` otherwise.
    The entry is removed from the pending registry on return.
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT

    with _LOCK:
        rec = _PENDING.get(aid)
    if rec is None:
        return False

    rec['_event'].wait(timeout=timeout)

    with _LOCK:
        finished = _PENDING.pop(aid, {})

    return finished.get('_approved', False)


def approve(aid: str) -> bool:
    """Approve a pending tool call.  Returns False if id is not found."""
    with _LOCK:
        rec = _PENDING.get(aid)
    if rec is None:
        return False
    rec['_approved'] = True
    rec['_event'].set()
    return True


def deny(aid: str) -> bool:
    """Deny a pending tool call.  Returns False if id is not found."""
    with _LOCK:
        rec = _PENDING.get(aid)
    if rec is None:
        return False
    rec['_approved'] = False
    rec['_event'].set()
    return True


def list_pending(session_id: str = '') -> list[dict]:
    """Return public view of all pending approvals, optionally filtered."""
    now = time.time()
    with _LOCK:
        rows = list(_PENDING.values())
    out = []
    for r in rows:
        if session_id and r.get('session_id', '') != session_id:
            continue
        out.append({
            'id':         r['id'],
            'tool':       r['tool'],
            'args':       r['args'],
            'session_id': r.get('session_id', ''),
            'ts':         r['ts'],
            'expires_in': round(max(0.0, r['ts'] + DEFAULT_TIMEOUT - now), 1),
        })
    return sorted(out, key=lambda x: x['ts'])


def is_pending(aid: str) -> bool:
    with _LOCK:
        return aid in _PENDING
