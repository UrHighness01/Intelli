"""Provider API key rotation and TTL management.

Tracks metadata (set_at, expires_at, last_rotated) for provider keys stored
via ProviderKeyStore.  Metadata is persisted in a sidecar JSON file so expiry
is preserved across gateway restarts.

Usage
-----
    from providers.key_rotation import KeyMetadata, store_key_with_ttl, get_key_metadata

    # Store a key with a 90-day TTL
    store_key_with_ttl('openai', 'sk-abc123', ttl_days=90)

    meta = get_key_metadata('openai')
    if meta.is_expired():
        # alert operator or auto-rotate
        ...

Environment variables
---------------------
AGENT_GATEWAY_KEY_METADATA_PATH
    Path to the JSON metadata store.  Default: agent-gateway/key_metadata.json
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional
import os

from providers.provider_adapter import ProviderKeyStore

# ---------------------------------------------------------------------------
# Paths & defaults
# ---------------------------------------------------------------------------

_METADATA_PATH = Path(
    os.environ.get(
        'AGENT_GATEWAY_KEY_METADATA_PATH',
        str(Path(__file__).parent.parent / 'key_metadata.json'),
    )
)

_DEFAULT_TTL_DAYS: int = int(os.environ.get('AGENT_GATEWAY_KEY_DEFAULT_TTL_DAYS', '90'))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class KeyMetadata:
    provider: str
    set_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None   # None = no expiry
    last_rotated: Optional[float] = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def days_until_expiry(self) -> Optional[float]:
        if self.expires_at is None:
            return None
        remaining = self.expires_at - time.time()
        return max(remaining / 86400, 0)

    def to_dict(self) -> dict:
        return {
            'provider': self.provider,
            'set_at': self.set_at,
            'expires_at': self.expires_at,
            'last_rotated': self.last_rotated,
        }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_all() -> Dict[str, KeyMetadata]:
    try:
        raw: Dict[str, dict] = json.loads(_METADATA_PATH.read_text(encoding='utf-8'))
        return {
            p: KeyMetadata(
                provider=p,
                set_at=v.get('set_at', 0.0),
                expires_at=v.get('expires_at'),
                last_rotated=v.get('last_rotated'),
            )
            for p, v in raw.items()
        }
    except Exception:
        return {}


def _save_all(meta: Dict[str, KeyMetadata]) -> None:
    try:
        _METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        _METADATA_PATH.write_text(
            json.dumps({p: m.to_dict() for p, m in meta.items()}, indent=2),
            encoding='utf-8',
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store_key_with_ttl(provider: str, key: str, ttl_days: Optional[int] = None) -> KeyMetadata:
    """Store an API key and record its metadata (set time + optional TTL).

    Parameters
    ----------
    provider:   Provider name (e.g. 'openai').
    key:        The API key value.
    ttl_days:   Days until the key expires.  None means no expiry.
                Defaults to AGENT_GATEWAY_KEY_DEFAULT_TTL_DAYS.
    """
    if ttl_days is None:
        ttl_days = _DEFAULT_TTL_DAYS

    ProviderKeyStore.set_key(provider, key)

    all_meta = _load_all()
    now = time.time()
    expires_at = now + ttl_days * 86400 if ttl_days else None
    all_meta[provider] = KeyMetadata(provider=provider, set_at=now, expires_at=expires_at)
    _save_all(all_meta)
    return all_meta[provider]


def get_key_metadata(provider: str) -> Optional[KeyMetadata]:
    """Return metadata for a provider's stored key, or None if not found."""
    return _load_all().get(provider)


def rotate_key(provider: str, new_key: str, ttl_days: Optional[int] = None) -> KeyMetadata:
    """Replace the current key with *new_key* and update rotation metadata.

    The 'last_rotated' timestamp is set to now and a new TTL clock starts.
    """
    if ttl_days is None:
        ttl_days = _DEFAULT_TTL_DAYS

    ProviderKeyStore.set_key(provider, new_key)

    all_meta = _load_all()
    now = time.time()
    expires_at = now + ttl_days * 86400 if ttl_days else None
    old = all_meta.get(provider)
    all_meta[provider] = KeyMetadata(
        provider=provider,
        set_at=now,
        expires_at=expires_at,
        last_rotated=now,
    )
    _save_all(all_meta)
    return all_meta[provider]


def list_expiring(within_days: float = 7.0):
    """Return a list of providers whose keys expire within *within_days* days."""
    threshold = time.time() + within_days * 86400
    results = []
    for provider, meta in _load_all().items():
        if meta.expires_at is not None and meta.expires_at <= threshold:
            results.append(meta)
    return results
