"""Simple sliding-window rate limiter for the Agent Gateway.

Each client is identified by their IP address (request.client.host).
The limiter is intentionally in-process (no Redis dependency) — suitable
for single-node deployments.  For multi-node setups replace the in-memory
dict with a shared backend (Redis INCR + EXPIRE or similar).

Environment variables
---------------------
AGENT_GATEWAY_RATE_LIMIT_REQUESTS
    Maximum number of requests allowed in the window.  Default: 60.
AGENT_GATEWAY_RATE_LIMIT_WINDOW
    Sliding window size in seconds.  Default: 60.
AGENT_GATEWAY_RATE_LIMIT_BURST
    Burst cap — requests allowed to exceed the per-window limit for a
    single second before being rejected.  Default: 10.

Usage in FastAPI
----------------
    from rate_limit import rate_limiter
    from fastapi import Depends

    @app.post("/tools/call")
    def tool_call(call: ToolCall, _rl=Depends(rate_limiter)):
        ...
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict

from fastapi import HTTPException, Request


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_MAX_REQUESTS: int = int(os.environ.get('AGENT_GATEWAY_RATE_LIMIT_REQUESTS', '60'))
_WINDOW_SECONDS: float = float(os.environ.get('AGENT_GATEWAY_RATE_LIMIT_WINDOW', '60'))
_BURST: int = int(os.environ.get('AGENT_GATEWAY_RATE_LIMIT_BURST', '10'))

# Whether rate limiting is active.  Set to "0" or "false" to disable entirely.
_ENABLED: bool = os.environ.get('AGENT_GATEWAY_RATE_LIMIT_ENABLED', '1').lower() not in ('0', 'false', 'no')

# Per-user rate limit (applied on top of per-IP limits for authenticated callers).
# Defaults to the same REQUEST/WINDOW values unless explicitly overridden.
_USER_MAX_REQUESTS: int = int(
    os.environ.get('AGENT_GATEWAY_USER_RATE_LIMIT_REQUESTS', str(_MAX_REQUESTS))
)
_USER_WINDOW_SECONDS: float = float(
    os.environ.get('AGENT_GATEWAY_USER_RATE_LIMIT_WINDOW', str(_WINDOW_SECONDS))
)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
# Maps client_key -> deque of timestamps (float, monotonic clock)
_windows: Dict[str, Deque[float]] = {}
# Maps username -> deque of timestamps for per-user limits
_user_windows: Dict[str, Deque[float]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _client_key(request: Request) -> str:
    """Return a stable key for the requesting client (IP or forwarded IP)."""
    forwarded_for = request.headers.get('x-forwarded-for')
    if forwarded_for:
        # Take the left-most IP (original client in proxy chains)
        return forwarded_for.split(',')[0].strip()
    if request.client:
        return request.client.host
    return 'unknown'


def check_rate_limit(request: Request) -> None:
    """Raise 429 if the client has exceeded the configured rate limit.

    Call directly or use as a FastAPI dependency (see module docstring).
    """
    if not _ENABLED:
        return

    key = _client_key(request)
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS

    with _lock:
        dq: Deque[float] = _windows.setdefault(key, deque())

        # Prune timestamps outside the sliding window
        while dq and dq[0] < cutoff:
            dq.popleft()

        current_count = len(dq)
        effective_limit = _MAX_REQUESTS + _BURST

        if current_count >= effective_limit:
            retry_after = int(_WINDOW_SECONDS - (now - dq[0])) + 1
            raise HTTPException(
                status_code=429,
                detail={
                    'error': 'rate_limit_exceeded',
                    'limit': _MAX_REQUESTS,
                    'window_seconds': int(_WINDOW_SECONDS),
                    'retry_after_seconds': max(retry_after, 1),
                },
                headers={'Retry-After': str(max(retry_after, 1))},
            )

        dq.append(now)


def rate_limiter(request: Request) -> None:
    """FastAPI dependency — raises 429 when the rate limit is breached."""
    check_rate_limit(request)


def reset_client(key: str) -> None:
    """Clear rate-limit state for a specific client.  Useful in tests."""
    with _lock:
        _windows.pop(key, None)


def reset_all() -> None:
    """Clear all rate-limit state.  Useful in tests."""
    with _lock:
        _windows.clear()


def reset_user(username: str) -> None:
    """Clear per-user rate-limit state for a specific user.  Useful in tests."""
    with _lock:
        _user_windows.pop(username, None)


def reset_all_users() -> None:
    """Clear all per-user rate-limit state.  Useful in tests."""
    with _lock:
        _user_windows.clear()


def check_user_rate_limit(username: str) -> None:
    """Raise 429 if *username* has exceeded the per-user rate limit.

    This is called explicitly from authenticated endpoints (e.g. /tools/call,
    /chat/complete) after the requesting user has been identified.  It uses a
    separate sliding window keyed by username so that one power-user cannot
    exhaust the shared per-IP quota.

    Configure via environment variables:
      AGENT_GATEWAY_USER_RATE_LIMIT_REQUESTS  (default: same as RATE_LIMIT_REQUESTS)
      AGENT_GATEWAY_USER_RATE_LIMIT_WINDOW    (default: same as RATE_LIMIT_WINDOW)
    """
    if not _ENABLED:
        return

    now = time.monotonic()
    cutoff = now - _USER_WINDOW_SECONDS

    with _lock:
        dq: Deque[float] = _user_windows.setdefault(username, deque())

        while dq and dq[0] < cutoff:
            dq.popleft()

        current_count = len(dq)

        if current_count >= _USER_MAX_REQUESTS:
            retry_after = int(_USER_WINDOW_SECONDS - (now - dq[0])) + 1
            raise HTTPException(
                status_code=429,
                detail={
                    'error': 'user_rate_limit_exceeded',
                    'user': username,
                    'limit': _USER_MAX_REQUESTS,
                    'window_seconds': int(_USER_WINDOW_SECONDS),
                    'retry_after_seconds': max(retry_after, 1),
                },
                headers={'Retry-After': str(max(retry_after, 1))},
            )

        dq.append(now)


def current_usage(request: Request) -> Dict[str, Any]:
    """Return the current request count and remaining quota for a client."""
    key = _client_key(request)
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS

    with _lock:
        dq = _windows.get(key, deque())
        count = sum(1 for ts in dq if ts >= cutoff)

    return {
        'client': key,
        'requests_in_window': count,
        'limit': _MAX_REQUESTS,
        'burst': _BURST,
        'window_seconds': int(_WINDOW_SECONDS),
        'remaining': max(0, _MAX_REQUESTS - count),
    }


# ---------------------------------------------------------------------------
# Runtime configuration access & mutation
# ---------------------------------------------------------------------------

def get_config() -> Dict[str, Any]:
    """Return the current rate-limit configuration (IP-level and user-level)."""
    global _MAX_REQUESTS, _WINDOW_SECONDS, _BURST, _ENABLED  # noqa: F821
    global _USER_MAX_REQUESTS, _USER_WINDOW_SECONDS           # noqa: F821
    return {
        'enabled': _ENABLED,
        'max_requests': _MAX_REQUESTS,
        'window_seconds': _WINDOW_SECONDS,
        'burst': _BURST,
        'user_max_requests': _USER_MAX_REQUESTS,
        'user_window_seconds': _USER_WINDOW_SECONDS,
    }


def update_config(
    *,
    max_requests: int | None = None,
    window_seconds: float | None = None,
    burst: int | None = None,
    enabled: bool | None = None,
    user_max_requests: int | None = None,
    user_window_seconds: float | None = None,
) -> Dict[str, Any]:
    """Update runtime rate-limit settings without restarting the process.

    Omit a parameter (or pass None) to leave that setting unchanged.
    Returns the resulting configuration.
    """
    global _MAX_REQUESTS, _WINDOW_SECONDS, _BURST, _ENABLED
    global _USER_MAX_REQUESTS, _USER_WINDOW_SECONDS

    with _lock:
        if max_requests is not None:
            if max_requests < 1:
                raise ValueError('max_requests must be >= 1')
            _MAX_REQUESTS = max_requests
        if window_seconds is not None:
            if window_seconds <= 0:
                raise ValueError('window_seconds must be > 0')
            _WINDOW_SECONDS = window_seconds
        if burst is not None:
            if burst < 0:
                raise ValueError('burst must be >= 0')
            _BURST = burst
        if enabled is not None:
            _ENABLED = bool(enabled)
        if user_max_requests is not None:
            if user_max_requests < 1:
                raise ValueError('user_max_requests must be >= 1')
            _USER_MAX_REQUESTS = user_max_requests
        if user_window_seconds is not None:
            if user_window_seconds <= 0:
                raise ValueError('user_window_seconds must be > 0')
            _USER_WINDOW_SECONDS = user_window_seconds

    return get_config()


def usage_snapshot() -> Dict[str, Any]:
    """Return a summary of all active client windows (non-empty queues)."""
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    clients = []
    with _lock:
        for key, dq in _windows.items():
            count = sum(1 for ts in dq if ts >= cutoff)
            if count > 0:
                clients.append({
                    'client': key,
                    'requests_in_window': count,
                    'remaining': max(0, _MAX_REQUESTS - count),
                })
    return {'clients': clients, 'total_tracked': len(clients)}
