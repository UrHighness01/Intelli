"""Vault integration scaffold for provider keys.

Provides a VaultKeyStore that reads/writes secrets from HashiCorp Vault
(KV v2) using the hvac library.  Falls back to ProviderKeyStore (keyring +
env) when Vault is unavailable or not configured.

Environment variables:
  VAULT_ADDR       – Vault server URL, e.g. http://127.0.0.1:8200
  VAULT_TOKEN      – Auth token (dev mode or AppRole secret-id)
  VAULT_NAMESPACE  – Enterprise namespace (optional)
  VAULT_KV_MOUNT   – KV mount path, default "secret"
  VAULT_KV_PREFIX  – Secret prefix path, default "intelli/providers"
"""
from __future__ import annotations

import os
from typing import Optional

try:
    import hvac  # type: ignore
    _HAS_HVAC = True
except ImportError:
    hvac = None  # type: ignore
    _HAS_HVAC = False

from providers.provider_adapter import ProviderKeyStore


class VaultKeyStore:
    """Read/write provider API keys using HashiCorp Vault KV v2.

    Falls back to the standard ProviderKeyStore chain (keyring -> env -> file)
    when Vault is not configured or hvac is not installed.
    """

    def __init__(self):
        self._addr = os.environ.get('VAULT_ADDR')
        self._token = os.environ.get('VAULT_TOKEN')
        self._namespace = os.environ.get('VAULT_NAMESPACE')
        self._mount = os.environ.get('VAULT_KV_MOUNT', 'secret')
        self._prefix = os.environ.get('VAULT_KV_PREFIX', 'intelli/providers')
        self._client: Optional[object] = None

        if _HAS_HVAC and self._addr and self._token:
            try:
                client = hvac.Client(  # type: ignore[union-attr]
                    url=self._addr,
                    token=self._token,
                    namespace=self._namespace,
                )
                if client.is_authenticated():
                    self._client = client
            except Exception:
                pass

    def _secret_path(self, provider: str) -> str:
        return f"{self._prefix}/{provider}"

    def set_key(self, provider: str, key: str) -> bool:
        """Write a provider API key to Vault.  Returns True on success."""
        if self._client is not None:
            try:
                self._client.secrets.kv.v2.create_or_update_secret(  # type: ignore
                    path=self._secret_path(provider),
                    secret={'api_key': key},
                    mount_point=self._mount,
                )
                return True
            except Exception:
                pass
        # fallback
        ProviderKeyStore.set_key(provider, key)
        return True

    def get_key(self, provider: str) -> Optional[str]:
        """Read a provider API key from Vault with fallback chain."""
        if self._client is not None:
            try:
                resp = self._client.secrets.kv.v2.read_secret_version(  # type: ignore
                    path=self._secret_path(provider),
                    mount_point=self._mount,
                )
                val = resp['data']['data'].get('api_key')
                if val:
                    return val
            except Exception:
                pass
        # fallback
        return ProviderKeyStore.get_key(provider)

    @property
    def is_vault_available(self) -> bool:
        return self._client is not None


# Module-level singleton — import this in app.py and providers
_store: Optional[VaultKeyStore] = None


def get_store() -> VaultKeyStore:
    global _store
    if _store is None:
        _store = VaultKeyStore()
    return _store
