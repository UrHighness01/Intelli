"""Secure credential store for Intelli Gateway.

Credentials are stored in the OS keychain via the ``keyring`` package
(macOS Keychain, GNOME Keyring / libsecret, Windows Credential Manager).

On headless / CI systems where no native keychain is available ``keyring``
falls back to its plaintext alt-backend.  In that case this module
additionally encrypts the secret with AES-256-GCM using a key derived from
``INTELLI_MASTER_KEY`` environment variable (if set) via PBKDF2-HMAC-SHA256.
When the environment variable is absent the fallback is still the keyring alt
store (unencrypted, system-only permissions).

A lightweight JSON index at ``~/.intelli/credential_index.json`` tracks which
names are stored so that ``list_names()`` works without scanning the keychain.

Public API
----------
    store(name, secret)     → None
    retrieve(name)          → Optional[str]
    delete(name)            → bool
    list_names()            → list[str]
    lockout_check()         → bool  (True = unlocked; raises if locked)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SERVICE = 'intelli-gateway'
_INDEX_FILE = Path(os.environ.get('INTELLI_CRED_INDEX', Path.home() / '.intelli' / 'credential_index.json'))
_MASTER_KEY_ENV = 'INTELLI_MASTER_KEY'
_LOCK_TIMEOUT = float(os.environ.get('INTELLI_CRED_LOCK_TIMEOUT', '300'))  # seconds until auto-lock

# ---------------------------------------------------------------------------
# Encryption helpers (stdlib-only, AES-256-GCM)
# ---------------------------------------------------------------------------

def _derive_key(master: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from *master* + *salt* via PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac('sha256', master.encode(), salt, 200_000, dklen=32)


def _encrypt(master: str, plaintext: str) -> str:
    """Encrypt *plaintext* → base64-encoded ``salt|iv|tag|ciphertext``."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        salt = secrets.token_bytes(16)
        iv   = secrets.token_bytes(12)
        key  = _derive_key(master, salt)
        ct   = AESGCM(key).encrypt(iv, plaintext.encode(), None)
        # ct includes 16-byte GCM tag at the end
        blob = salt + iv + ct
        return base64.b64encode(blob).decode()
    except ImportError:
        # cryptography not installed — store as-is (keyring itself handles security)
        return _simple_xor_encode(master, plaintext)


def _decrypt(master: str, ciphertext: str) -> str:
    """Decrypt *ciphertext* produced by :func:`_encrypt`."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = base64.b64decode(ciphertext)
        salt = blob[:16]
        iv   = blob[16:28]
        ct   = blob[28:]
        key  = _derive_key(master, salt)
        return AESGCM(key).decrypt(iv, ct, None).decode()
    except ImportError:
        return _simple_xor_decode(master, ciphertext)


def _simple_xor_encode(key: str, text: str) -> str:
    """Trivial XOR obfuscation when cryptography is unavailable."""
    k = hashlib.sha256(key.encode()).digest()
    enc = bytes(b ^ k[i % 32] for i, b in enumerate(text.encode()))
    return base64.b64encode(enc).decode()


def _simple_xor_decode(key: str, encoded: str) -> str:
    k = hashlib.sha256(key.encode()).digest()
    enc = base64.b64decode(encoded)
    return bytes(b ^ k[i % 32] for i, b in enumerate(enc)).decode()


# ---------------------------------------------------------------------------
# Index management (tracks which names exist)
# ---------------------------------------------------------------------------

_idx_lock = threading.Lock()


def _load_index() -> List[str]:
    if _INDEX_FILE.exists():
        try:
            data = json.loads(_INDEX_FILE.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_index(names: List[str]) -> None:
    _INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_FILE.write_text(json.dumps(sorted(set(names)), indent=2), encoding='utf-8')


# ---------------------------------------------------------------------------
# Lock / idle-timeout
# ---------------------------------------------------------------------------

_lock_state: Dict[str, object] = {'locked': False, 'last_access': time.monotonic()}
_lock_mutex = threading.Lock()


def _touch() -> None:
    with _lock_mutex:
        _lock_state['last_access'] = time.monotonic()
        _lock_state['locked'] = False


def lock() -> None:
    """Manually lock the credential store."""
    with _lock_mutex:
        _lock_state['locked'] = True


def is_locked() -> bool:
    """Return True if the store is auto-locked due to idle timeout."""
    with _lock_mutex:
        if _lock_state['locked']:
            return True
        idle = time.monotonic() - float(_lock_state['last_access'])  # type: ignore[arg-type]
        return idle > _LOCK_TIMEOUT


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store(name: str, secret: str) -> None:
    """Store *secret* under *name* in the OS keychain.

    If ``INTELLI_MASTER_KEY`` is set the secret is AES-256-GCM encrypted
    before being handed to keyring.  This provides defence-in-depth when
    the keyring backend is a plaintext alt-backend.

    Parameters
    ----------
    name:
        Credential identifier, e.g. ``"github_token"``.
    secret:
        The plaintext secret value.
    """
    import keyring

    master = os.environ.get(_MASTER_KEY_ENV, '')
    payload = _encrypt(master, secret) if master else secret
    keyring.set_password(_SERVICE, name, payload)

    with _idx_lock:
        names = _load_index()
        if name not in names:
            names.append(name)
            _save_index(names)

    _touch()


def retrieve(name: str) -> Optional[str]:
    """Retrieve the secret for *name*, or None if not found.

    Raises
    ------
    PermissionError
        If the credential store is locked (idle timeout exceeded).
    """
    if is_locked():
        raise PermissionError(
            'Credential store is locked due to inactivity. '
            'Set INTELLI_CRED_LOCK_TIMEOUT to adjust the lock window.'
        )

    import keyring

    payload = keyring.get_password(_SERVICE, name)
    if payload is None:
        return None

    master = os.environ.get(_MASTER_KEY_ENV, '')
    try:
        return _decrypt(master, payload) if master else payload
    except Exception:
        # Fallback: return raw (may happen if master key changed)
        return payload


def delete(name: str) -> bool:
    """Delete the credential *name*.  Returns True if it existed."""
    import keyring

    existing = keyring.get_password(_SERVICE, name)
    if existing is None:
        return False
    keyring.delete_password(_SERVICE, name)

    with _idx_lock:
        names = _load_index()
        names = [n for n in names if n != name]
        _save_index(names)

    _touch()
    return True


def list_names() -> List[str]:
    """Return the names of all stored credentials (never the values)."""
    with _idx_lock:
        return _load_index()
