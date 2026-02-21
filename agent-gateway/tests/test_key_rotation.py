"""Tests for agent-gateway/providers/key_rotation.py.

Covers:
  - store_key_with_ttl – metadata stored, TTL computed, default TTL
  - get_key_metadata – returns stored metadata or None
  - rotate_key – last_rotated set, new TTL
  - list_expiring – filters by threshold
  - KeyMetadata.is_expired / days_until_expiry
"""
import os
import sys
import time
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_metadata(monkeypatch, tmp_path):
    """Redirect metadata file and stub ProviderKeyStore so we don't touch disk keys."""
    meta_file = tmp_path / 'key_metadata_test.json'
    monkeypatch.setenv('AGENT_GATEWAY_KEY_METADATA_PATH', str(meta_file))
    monkeypatch.setenv('AGENT_GATEWAY_KEY_DEFAULT_TTL_DAYS', '90')

    # Stub ProviderKeyStore to avoid touching real key storage
    import providers.provider_adapter as pa

    _store: dict = {}

    def fake_set_key(provider, key):
        _store[provider] = key

    def fake_get_key(provider):
        return _store.get(provider)

    monkeypatch.setattr(pa.ProviderKeyStore, 'set_key', staticmethod(fake_set_key))
    monkeypatch.setattr(pa.ProviderKeyStore, 'get_key', staticmethod(fake_get_key))

    import importlib, providers.key_rotation as kr
    importlib.reload(kr)
    yield kr


# ---------------------------------------------------------------------------
# KeyMetadata model
# ---------------------------------------------------------------------------

class TestKeyMetadata:
    def test_not_expired_when_future(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', expires_at=time.time() + 9999)
        assert meta.is_expired() is False

    def test_expired_when_past(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', expires_at=time.time() - 1)
        assert meta.is_expired() is True

    def test_not_expired_when_no_expiry(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', expires_at=None)
        assert meta.is_expired() is False

    def test_days_until_expiry_positive(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', expires_at=time.time() + 10 * 86400)
        days = meta.days_until_expiry()
        assert days is not None
        assert 9.9 <= days <= 10.1

    def test_days_until_expiry_zero_when_past(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', expires_at=time.time() - 100)
        assert meta.days_until_expiry() == 0

    def test_days_until_expiry_none_when_no_expiry(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', expires_at=None)
        assert meta.days_until_expiry() is None

    def test_to_dict_keys(self, isolated_metadata):
        from providers.key_rotation import KeyMetadata
        meta = KeyMetadata('openai', set_at=1000.0, expires_at=2000.0, last_rotated=None)
        d = meta.to_dict()
        assert set(d.keys()) == {'provider', 'set_at', 'expires_at', 'last_rotated'}


# ---------------------------------------------------------------------------
# store_key_with_ttl
# ---------------------------------------------------------------------------

class TestStoreKeyWithTtl:
    def test_returns_key_metadata(self, isolated_metadata):
        meta = isolated_metadata.store_key_with_ttl('openai', 'sk-abc', ttl_days=30)
        assert meta.provider == 'openai'
        assert meta.expires_at is not None

    def test_ttl_days_30_gives_correct_expiry(self, isolated_metadata):
        before = time.time()
        meta = isolated_metadata.store_key_with_ttl('openai', 'sk-abc', ttl_days=30)
        after = time.time()
        expected_low = before + 30 * 86400
        expected_high = after + 30 * 86400
        assert expected_low <= meta.expires_at <= expected_high

    def test_none_ttl_uses_default(self, isolated_metadata):
        meta = isolated_metadata.store_key_with_ttl('openai', 'sk-abc', ttl_days=None)
        # default TTL is 90 days
        assert meta.expires_at is not None
        days = (meta.expires_at - time.time()) / 86400
        assert 89 <= days <= 91

    def test_persists_to_disk(self, isolated_metadata, tmp_path):
        isolated_metadata.store_key_with_ttl('openai', 'sk-abc', ttl_days=30)
        meta_file = Path(os.environ['AGENT_GATEWAY_KEY_METADATA_PATH'])
        assert meta_file.exists()
        data = json.loads(meta_file.read_text())
        assert 'openai' in data

    def test_multiple_providers_stored_independently(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('openai', 'sk-a', ttl_days=30)
        isolated_metadata.store_key_with_ttl('anthropic', 'ant-b', ttl_days=60)
        m1 = isolated_metadata.get_key_metadata('openai')
        m2 = isolated_metadata.get_key_metadata('anthropic')
        assert m1 is not None
        assert m2 is not None
        assert m1.expires_at != m2.expires_at


# ---------------------------------------------------------------------------
# get_key_metadata
# ---------------------------------------------------------------------------

class TestGetKeyMetadata:
    def test_returns_none_for_unknown_provider(self, isolated_metadata):
        assert isolated_metadata.get_key_metadata('nonexistent') is None

    def test_returns_metadata_after_store(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('openai', 'sk-abc', ttl_days=10)
        meta = isolated_metadata.get_key_metadata('openai')
        assert meta is not None
        assert meta.provider == 'openai'


# ---------------------------------------------------------------------------
# rotate_key
# ---------------------------------------------------------------------------

class TestRotateKey:
    def test_sets_last_rotated(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('openai', 'sk-old', ttl_days=30)
        before = time.time()
        meta = isolated_metadata.rotate_key('openai', 'sk-new', ttl_days=30)
        assert meta.last_rotated is not None
        assert meta.last_rotated >= before

    def test_new_ttl_applied(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('openai', 'sk-old', ttl_days=30)
        meta = isolated_metadata.rotate_key('openai', 'sk-new', ttl_days=60)
        days = (meta.expires_at - time.time()) / 86400
        assert 59 <= days <= 61

    def test_rotate_without_prior_store(self, isolated_metadata):
        """rotate_key should succeed even if called before store_key_with_ttl."""
        meta = isolated_metadata.rotate_key('freshprov', 'sk-fresh', ttl_days=10)
        assert meta.provider == 'freshprov'
        assert meta.last_rotated is not None

    def test_rotate_updates_persisted_data(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('openai', 'sk-old', ttl_days=10)
        isolated_metadata.rotate_key('openai', 'sk-new', ttl_days=45)
        meta = isolated_metadata.get_key_metadata('openai')
        days = (meta.expires_at - time.time()) / 86400
        assert 44 <= days <= 46


# ---------------------------------------------------------------------------
# list_expiring
# ---------------------------------------------------------------------------

class TestListExpiring:
    def test_returns_providers_expiring_within_window(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('soon', 'sk-1', ttl_days=3)    # expiring soon
        isolated_metadata.store_key_with_ttl('later', 'sk-2', ttl_days=30)  # not yet
        expiring = isolated_metadata.list_expiring(within_days=7)
        providers = [m.provider for m in expiring]
        assert 'soon' in providers
        assert 'later' not in providers

    def test_empty_when_none_expiring(self, isolated_metadata):
        isolated_metadata.store_key_with_ttl('safe', 'sk-3', ttl_days=90)
        expiring = isolated_metadata.list_expiring(within_days=7)
        assert expiring == []

    def test_already_expired_included(self, isolated_metadata):
        """A key that's already expired is still within the expiry window."""
        isolated_metadata.store_key_with_ttl('expired', 'sk-4', ttl_days=0)
        # ttl_days=0 → expires_at=None (no expiry), so this tests None handling
        # Use a manual insert instead
        import json
        from pathlib import Path
        meta_file = Path(os.environ['AGENT_GATEWAY_KEY_METADATA_PATH'])
        data = {}
        if meta_file.exists():
            data = json.loads(meta_file.read_text())
        data['oldprov'] = {
            'provider': 'oldprov',
            'set_at': time.time() - 200 * 86400,
            'expires_at': time.time() - 1,  # already expired
            'last_rotated': None,
        }
        meta_file.write_text(json.dumps(data))
        expiring = isolated_metadata.list_expiring(within_days=7)
        providers = [m.provider for m in expiring]
        assert 'oldprov' in providers
