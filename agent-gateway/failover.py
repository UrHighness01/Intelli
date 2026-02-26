"""failover.py — Model Failover & Provider Rotation for Intelli Agent Gateway

When the primary provider returns a rate-limit (429), server error (5xx), or
connectivity failure, this module tries the next provider in the configured
fallback chain — transparently, without the user needing to do anything.

Cooldown tracking
-----------------
Each provider that fails is placed on cooldown for an exponentially growing
window (starting at 30 s, capped at 10 min).  A background thread is NOT
needed: cooldown expiry is checked lazily on each request.

Usage
-----
    from failover import chat_with_failover, get_chain, set_chain

    result = chat_with_failover(
        primary_provider='openai',
        primary_model='gpt-4o',
        messages=[{'role': 'user', 'content': 'Hello'}],
        temperature=0.7,
    )
    # result has extra keys: failover_used (bool), actual_provider (str),
    # actual_model (str)
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default fallback models per provider
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: Dict[str, str] = {
    'openai':         'gpt-4o-mini',
    'anthropic':      'claude-3-5-haiku-20241022',
    'openrouter':     'openai/gpt-4o-mini',
    'github_copilot': 'gpt-4o',
    'ollama':         'llama3',
}

# ---------------------------------------------------------------------------
# Cooldown state
# ---------------------------------------------------------------------------

_lock = threading.Lock()

# provider_name -> (cooldown_expires_at, current_backoff_seconds)
_cooldowns: Dict[str, Tuple[float, float]] = {}

_COOLDOWN_BASE   = 30.0    # seconds for first failure
_COOLDOWN_FACTOR = 2.0     # exponential growth factor
_COOLDOWN_MAX    = 600.0   # 10 minutes ceiling


def _is_on_cooldown(provider: str) -> bool:
    with _lock:
        if provider not in _cooldowns:
            return False
        expires, _ = _cooldowns[provider]
        return time.monotonic() < expires


def _record_failure(provider: str) -> None:
    with _lock:
        _, prev_backoff = _cooldowns.get(provider, (0.0, _COOLDOWN_BASE / _COOLDOWN_FACTOR))
        backoff = min(prev_backoff * _COOLDOWN_FACTOR, _COOLDOWN_MAX)
        expires = time.monotonic() + backoff
        _cooldowns[provider] = (expires, backoff)
    logger.warning('failover: %s on cooldown for %.0f s', provider, backoff)


def _clear_cooldown(provider: str) -> None:
    with _lock:
        _cooldowns.pop(provider, None)


def cooldown_status() -> List[Dict[str, Any]]:
    """Return current cooldown state (for admin/health endpoints)."""
    now = time.monotonic()
    with _lock:
        return [
            {
                'provider':    p,
                'expires_in':  max(0.0, exp - now),
                'backoff':     bo,
            }
            for p, (exp, bo) in _cooldowns.items()
        ]


# ---------------------------------------------------------------------------
# Chain configuration
# ---------------------------------------------------------------------------

# The chain is a list of (provider, model | None) tuples.
# None means "use _DEFAULT_MODELS" for that provider.
_chain: List[Tuple[str, Optional[str]]] = [
    ('openai',    None),
    ('anthropic', None),
    ('ollama',    None),
]
_chain_lock = threading.Lock()


def get_chain() -> List[Dict[str, Optional[str]]]:
    with _chain_lock:
        return [{'provider': p, 'model': m} for p, m in _chain]


def set_chain(entries: List[Dict[str, Optional[str]]]) -> None:
    """Replace the failover chain.  Each entry: {provider, model?}."""
    with _chain_lock:
        global _chain
        _chain = [(e['provider'], e.get('model')) for e in entries]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_RATE_LIMIT_MARKERS = ('429', 'rate limit', 'rate_limit', 'too many requests', 'quota')
_SERVER_ERR_MARKERS = ('500', '502', '503', '504', 'connection error', 'timeout',
                       'connecterror', 'connectionerror', 'read timeout',
                       'service unavailable', 'internal server error')


def _is_retriable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _RATE_LIMIT_MARKERS + _SERVER_ERR_MARKERS)


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _RATE_LIMIT_MARKERS)


# ---------------------------------------------------------------------------
# Core failover call
# ---------------------------------------------------------------------------

def chat_with_failover(
    primary_provider: str,
    primary_model:    Optional[str],
    messages:         list,
    temperature:      float  = 0.7,
    max_tokens:       Optional[int] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Call chat_complete with automatic failover on rate-limit / server errors.

    Returns the normal adapter result dict, extended with:
        failover_used     (bool)  – True if the primary failed
        actual_provider   (str)   – which provider actually responded
        actual_model      (str)   – which model was used
        failover_reason   (str)   – error that triggered failover (if used)
    """
    from providers.adapters import get_adapter  # local import to avoid circular

    # Build the ordered attempt list: primary first, then chain entries
    attempts: List[Tuple[str, Optional[str]]] = [(primary_provider, primary_model)]
    with _chain_lock:
        for p, m in _chain:
            if p != primary_provider:
                attempts.append((p, m))

    last_exc: Optional[Exception] = None
    failover_triggered = False
    failover_reason    = ''

    for idx, (provider, model) in enumerate(attempts):
        if _is_on_cooldown(provider):
            logger.info('failover: skipping %s (on cooldown)', provider)
            continue

        try:
            adapter = get_adapter(provider)
        except KeyError:
            continue

        if not adapter.is_available():
            continue

        call_kwargs = dict(kwargs)
        resolved_model = model or _DEFAULT_MODELS.get(provider, '')
        if idx == 0 and primary_model:
            resolved_model = primary_model
        if resolved_model:
            call_kwargs['model'] = resolved_model

        try:
            result = adapter.chat_complete(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **call_kwargs,
            )
            # Success — clear any previous cooldown for this provider
            _clear_cooldown(provider)
            result.setdefault('provider', provider)
            result['failover_used']   = failover_triggered
            result['actual_provider'] = provider
            result['actual_model']    = result.get('model', resolved_model)
            if failover_triggered:
                result['failover_reason'] = failover_reason
                logger.info('failover: recovered via %s/%s', provider, resolved_model)
            return result

        except Exception as exc:
            last_exc = exc
            if _is_retriable(exc):
                _record_failure(provider)
                if idx == 0:
                    failover_triggered = True
                    failover_reason    = str(exc)[:200]
                    lbl = 'rate-limited' if _is_rate_limit(exc) else 'errored'
                    logger.warning('failover: primary %s %s — trying next in chain', provider, lbl)
            else:
                # Non-retriable error on primary → don't try failover, just raise
                if idx == 0:
                    raise
                logger.info('failover: non-retriable error on %s: %s', provider, exc)

    # All providers exhausted
    raise RuntimeError(
        f'All providers in failover chain exhausted. Last error: {last_exc}'
    )


# ---------------------------------------------------------------------------
# FailoverAdapter — drop-in replacement for regular adapters
# ---------------------------------------------------------------------------

class FailoverAdapter:
    """Wraps chat_with_failover behind the standard adapter interface.

    Can be passed directly to run_tool_loop or anywhere an adapter is expected.
    Stores last failover metadata so callers can surface it in responses.
    """

    def __init__(self, primary_provider: str, primary_model: Optional[str] = None):
        self._primary_provider = primary_provider
        self._primary_model    = primary_model
        self.last_result_meta: Dict[str, Any] = {}

    def is_available(self) -> bool:
        from providers.adapters import get_adapter
        # Available if primary is up *or* any chain member is available & not on cooldown
        for p, _ in [(self._primary_provider, None)] + list(_chain):
            if _is_on_cooldown(p):
                continue
            try:
                if get_adapter(p).is_available():
                    return True
            except KeyError:
                continue
        return False

    def chat_complete(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        # model kwarg may override primary_model (e.g. from tool_runner)
        model = kwargs.pop('model', self._primary_model)
        result = chat_with_failover(
            primary_provider=self._primary_provider,
            primary_model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        # Stash metadata so the calling endpoint can read it
        self.last_result_meta = {
            'failover_used':   result.get('failover_used', False),
            'actual_provider': result.get('actual_provider', self._primary_provider),
            'actual_model':    result.get('actual_model', model or ''),
            'failover_reason': result.get('failover_reason', ''),
        }
        return result
