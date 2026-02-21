"""Approval webhook registry for the Agent Gateway.

Allows operators to register HTTP callback URLs that are notified when
approval events occur (approval.created, approval.approved, approval.rejected).

Webhooks are persisted to WEBHOOKS_FILE so they survive restarts.

Environment variables
---------------------
AGENT_GATEWAY_WEBHOOKS_FILE
    Path to the JSON file storing registered webhooks.
    Default: webhooks.json in the same directory as this module.
AGENT_GATEWAY_WEBHOOK_TIMEOUT
    HTTP timeout in seconds for outbound webhook calls.  Default: 5.

Webhook delivery
----------------
Outbound POST requests are sent with:
    Content-Type: application/json
    X-Gateway-Event: <event_name>
    X-Gateway-Hook-ID: <webhook_id>
    X-Intelli-Signature-256: sha256=<hmac-sha256>  (only when a secret is set)

If delivery fails (network error, timeout, non-2xx), the failure is logged
but does NOT block the gateway.  Delivery is best-effort, fire-and-forget
from an executor thread.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
WEBHOOKS_FILE: Path = Path(
    os.environ.get('AGENT_GATEWAY_WEBHOOKS_FILE', str(_HERE / 'webhooks.json'))
)
_TIMEOUT: float = float(os.environ.get('AGENT_GATEWAY_WEBHOOK_TIMEOUT', '5'))

VALID_EVENTS = frozenset({
    'approval.created',
    'approval.approved',
    'approval.rejected',
})

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_hooks: Dict[str, Dict[str, Any]] = {}   # id -> {id, url, events, created_at}
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='webhook')
_loaded = False

# Per-hook delivery log (in-memory, not persisted across restarts)
_LOG_MAX = 100   # entries per hook
_delivery_log: Dict[str, Deque[Dict[str, Any]]] = {}  # hook_id -> deque


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if WEBHOOKS_FILE.exists():
        try:
            data = json.loads(WEBHOOKS_FILE.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                _hooks.update(data)
        except Exception:
            pass  # corrupted file — start fresh


def _save() -> None:
    WEBHOOKS_FILE.write_text(json.dumps(_hooks, indent=2), encoding='utf-8')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_webhook(url: str, events: Optional[List[str]] = None, secret: str = '') -> Dict[str, Any]:
    """Register a new webhook.

    Parameters
    ----------
    url:
        Full HTTP/HTTPS URL to POST events to.
    events:
        List of event names to subscribe to.  Defaults to all events.
        Valid values: 'approval.created', 'approval.approved', 'approval.rejected'.
    secret:
        Optional HMAC signing secret.  When non-empty, each delivery includes
        an ``X-Intelli-Signature-256: sha256=<hex>`` header so receivers can
        verify payload authenticity.

    Returns the created webhook object.
    """
    if not url.startswith(('http://', 'https://')):
        raise ValueError('url must start with http:// or https://')

    if events is None:
        events = list(VALID_EVENTS)
    else:
        unknown = set(events) - VALID_EVENTS
        if unknown:
            raise ValueError(f'unknown events: {unknown}; valid: {VALID_EVENTS}')

    hook_id = str(uuid.uuid4())
    hook = {
        'id': hook_id,
        'url': url,
        'events': sorted(events),
        'secret': secret,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    with _lock:
        _load()
        _hooks[hook_id] = hook
        _save()
    return hook


def list_webhooks() -> List[Dict[str, Any]]:
    """Return all registered webhooks."""
    with _lock:
        _load()
        return list(_hooks.values())


def get_webhook(hook_id: str) -> Optional[Dict[str, Any]]:
    """Return a single webhook record or None if not found."""
    with _lock:
        _load()
        return _hooks.get(hook_id)


def delete_webhook(hook_id: str) -> bool:
    """Delete a webhook.  Returns True if it existed."""
    with _lock:
        _load()
        if hook_id not in _hooks:
            return False
        del _hooks[hook_id]
        _save()
        return True


def fire_webhooks(event: str, payload: Dict[str, Any]) -> None:
    """Dispatch *event* to all subscribers asynchronously.

    This is fire-and-forget: failures are silently dropped so that a flaky
    external endpoint can never block or slow the gateway.
    """
    with _lock:
        _load()
        targets = [h for h in _hooks.values() if event in h['events']]

    body = json.dumps({'event': event, 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **payload}).encode()

    for hook in targets:
        _executor.submit(_deliver, hook['id'], hook['url'], event, body, hook.get('secret', ''))


def get_deliveries(hook_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent delivery records for *hook_id*, newest-first.

    Parameters
    ----------
    hook_id:
        The webhook UUID to query.
    limit:
        Maximum number of records to return (capped at :data:`_LOG_MAX`).

    Returns an empty list if the hook has no delivery history yet.
    """
    limit = min(max(limit, 1), _LOG_MAX)
    with _lock:
        log = _delivery_log.get(hook_id)
    if log is None:
        return []
    return list(log)[:limit]


def _deliver(hook_id: str, url: str, event: str, body: bytes, secret: str = '') -> None:
    """Attempt a single webhook delivery (runs in thread pool)."""
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    status_code: Optional[int] = None
    error: Optional[str] = None
    ok = False

    try:
        # Use urllib so there is no extra dependency beyond stdlib
        import urllib.request
        headers: Dict[str, str] = {
            'Content-Type': 'application/json',
            'X-Gateway-Event': event,
            'X-Gateway-Hook-ID': hook_id,
        }
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers['X-Intelli-Signature-256'] = f'sha256={sig}'
        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            status_code = int(resp.status or 0)
            ok = 200 <= status_code < 300
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)

    # Record outcome in per-hook deque (creates it on first delivery if needed)
    record: Dict[str, Any] = {
        'timestamp': ts,
        'event': event,
        'status': 'ok' if ok else 'error',
        'status_code': status_code,
        'error': error,
    }
    with _lock:
        if hook_id not in _delivery_log:
            _delivery_log[hook_id] = deque(maxlen=_LOG_MAX)
        _delivery_log[hook_id].appendleft(record)   # newest first
