"""Tests for GET /admin/providers/{provider}/health.

The endpoint checks:
- Whether the admin token is present (401 without, 403 for non-admin)
- Returns 400 for unknown providers
- Returns {'status': 'no_key', 'configured': False} when no key stored
- Returns {'status': 'ok', 'configured': True, 'available': True} when key present and adapter available
- Returns {'status': 'unavailable', 'configured': True, 'available': False} when key present but adapter unavailable
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import app


ADMIN_TOKEN = 'test-secret'


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


class TestProviderHealthEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/admin/providers/openai/health')
        assert r.status_code == 401

    def test_requires_admin_role(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: False)
        r = client.get('/admin/providers/openai/health', headers=_auth())
        assert r.status_code == 403

    def test_unknown_provider_returns_400(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.get('/admin/providers/nonexistent_xyz/health', headers=_auth())
        assert r.status_code == 400

    def test_no_key_returns_no_key_status(self, client, monkeypatch):
        import auth as _auth_mod
        from providers.provider_adapter import ProviderKeyStore

        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        monkeypatch.setattr(ProviderKeyStore, 'get_key', staticmethod(lambda provider: ''))

        r = client.get('/admin/providers/openai/health', headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'no_key'
        assert body['configured'] is False

    def test_key_present_adapter_available(self, client, monkeypatch):
        import auth as _auth_mod
        from providers.provider_adapter import ProviderKeyStore
        import app as _app_mod

        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        monkeypatch.setattr(ProviderKeyStore, 'get_key', staticmethod(lambda provider: 'sk-fake-key'))

        class _MockAdapter:
            def is_available(self):
                return True

        monkeypatch.setattr(_app_mod, 'get_adapter', lambda provider: _MockAdapter())

        r = client.get('/admin/providers/openai/health', headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'ok'
        assert body['configured'] is True
        assert body['available'] is True

    def test_key_present_adapter_unavailable(self, client, monkeypatch):
        import auth as _auth_mod
        from providers.provider_adapter import ProviderKeyStore
        import app as _app_mod

        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        monkeypatch.setattr(ProviderKeyStore, 'get_key', staticmethod(lambda provider: 'sk-fake-key'))

        class _UnavailableAdapter:
            def is_available(self):
                return False

        monkeypatch.setattr(_app_mod, 'get_adapter', lambda provider: _UnavailableAdapter())

        r = client.get('/admin/providers/openai/health', headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'unavailable'
        assert body['configured'] is True
        assert body['available'] is False

    @pytest.mark.parametrize('provider', ['openai', 'anthropic', 'openrouter', 'ollama'])
    def test_all_known_providers_accepted(self, client, monkeypatch, provider):
        import auth as _auth_mod
        from providers.provider_adapter import ProviderKeyStore

        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        monkeypatch.setattr(ProviderKeyStore, 'get_key', staticmethod(lambda p: ''))

        r = client.get(f'/admin/providers/{provider}/health', headers=_auth())
        # Should not return 400 (unknown provider); any 2xx is valid
        assert r.status_code != 400

    def test_response_schema_complete(self, client, monkeypatch):
        import auth as _auth_mod
        from providers.provider_adapter import ProviderKeyStore

        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        monkeypatch.setattr(ProviderKeyStore, 'get_key', staticmethod(lambda p: ''))

        r = client.get('/admin/providers/openai/health', headers=_auth())
        body = r.json()
        assert 'provider' in body
        assert 'status' in body
        assert 'configured' in body
        assert 'available' in body
