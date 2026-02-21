"""Tests for the rate-limit admin API.

Covers:
  GET  /admin/rate-limits        — read config + usage snapshot
  PUT  /admin/rate-limits        — runtime reconfiguration
  DELETE /admin/rate-limits/clients/{client_key}  — reset a client window
  DELETE /admin/rate-limits/users/{username}       — reset a user window

Also tests the underlying rate_limit module helper functions:
  get_config(), update_config(), usage_snapshot().
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import rate_limit
from app import app

ADMIN_TOKEN: str = 'test-secret'


@pytest.fixture(autouse=True)
def _reset():
    """Restore original rate-limit config + clear all windows after each test."""
    original = rate_limit.get_config()
    rate_limit.reset_all()
    rate_limit.reset_all_users()
    yield
    rate_limit.update_config(
        max_requests=original['max_requests'],
        window_seconds=original['window_seconds'],
        burst=original['burst'],
        enabled=original['enabled'],
        user_max_requests=original['user_max_requests'],
        user_window_seconds=original['user_window_seconds'],
    )
    rate_limit.reset_all()
    rate_limit.reset_all_users()


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


# ===========================================================================
# Module-level unit tests
# ===========================================================================

class TestGetConfig:
    def test_returns_all_keys(self):
        cfg = rate_limit.get_config()
        assert 'enabled' in cfg
        assert 'max_requests' in cfg
        assert 'window_seconds' in cfg
        assert 'burst' in cfg
        assert 'user_max_requests' in cfg
        assert 'user_window_seconds' in cfg

    def test_values_are_positive(self):
        cfg = rate_limit.get_config()
        assert cfg['max_requests'] >= 1
        assert cfg['window_seconds'] > 0
        assert cfg['burst'] >= 0

    def test_enabled_is_bool(self):
        assert isinstance(rate_limit.get_config()['enabled'], bool)


class TestUpdateConfig:
    def test_update_max_requests(self):
        rate_limit.update_config(max_requests=42)
        assert rate_limit.get_config()['max_requests'] == 42

    def test_update_window_seconds(self):
        rate_limit.update_config(window_seconds=120.0)
        assert rate_limit.get_config()['window_seconds'] == 120.0

    def test_update_burst(self):
        rate_limit.update_config(burst=5)
        assert rate_limit.get_config()['burst'] == 5

    def test_update_enabled_false(self):
        rate_limit.update_config(enabled=False)
        assert rate_limit.get_config()['enabled'] is False

    def test_update_enabled_true(self):
        rate_limit.update_config(enabled=False)
        rate_limit.update_config(enabled=True)
        assert rate_limit.get_config()['enabled'] is True

    def test_update_user_limits(self):
        rate_limit.update_config(user_max_requests=10, user_window_seconds=30.0)
        cfg = rate_limit.get_config()
        assert cfg['user_max_requests'] == 10
        assert cfg['user_window_seconds'] == 30.0

    def test_partial_update_leaves_others_unchanged(self):
        original_burst = rate_limit.get_config()['burst']
        rate_limit.update_config(max_requests=99)
        assert rate_limit.get_config()['burst'] == original_burst

    def test_invalid_max_requests_raises(self):
        with pytest.raises(ValueError):
            rate_limit.update_config(max_requests=0)

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            rate_limit.update_config(window_seconds=-1.0)

    def test_invalid_burst_raises(self):
        with pytest.raises(ValueError):
            rate_limit.update_config(burst=-1)

    def test_returns_config_dict(self):
        result = rate_limit.update_config(max_requests=55)
        assert isinstance(result, dict)
        assert result['max_requests'] == 55


class TestUsageSnapshot:
    def test_empty_when_no_traffic(self):
        snap = rate_limit.usage_snapshot()
        assert snap['clients'] == []
        assert snap['total_tracked'] == 0


# ===========================================================================
# HTTP endpoint tests
# ===========================================================================

class TestGetRateLimitsEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/admin/rate-limits')
        assert r.status_code == 401

    def test_requires_admin(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: False)
        r = client.get('/admin/rate-limits', headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 403

    def test_returns_config_and_usage(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.get('/admin/rate-limits', headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200
        body = r.json()
        assert 'config' in body
        assert 'usage' in body
        assert 'max_requests' in body['config']
        assert 'clients' in body['usage']


class TestPutRateLimitsEndpoint:
    def test_requires_auth(self, client):
        r = client.put('/admin/rate-limits', json={'max_requests': 10})
        assert r.status_code == 401

    def test_update_max_requests(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.put('/admin/rate-limits', json={'max_requests': 77}, headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200
        body = r.json()
        assert body['updated'] is True
        assert body['config']['max_requests'] == 77

    def test_update_enabled_false(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.put('/admin/rate-limits', json={'enabled': False}, headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200
        assert r.json()['config']['enabled'] is False

    def test_invalid_value_returns_422(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.put('/admin/rate-limits', json={'max_requests': 0}, headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 422

    def test_null_fields_are_ignored(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        original = rate_limit.get_config()['burst']
        r = client.put('/admin/rate-limits', json={'burst': None}, headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200
        assert r.json()['config']['burst'] == original


class TestResetClientEndpoint:
    def test_requires_auth(self, client):
        r = client.delete('/admin/rate-limits/clients/1.2.3.4')
        assert r.status_code == 401

    def test_reset_succeeds(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.delete('/admin/rate-limits/clients/1.2.3.4', headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200
        body = r.json()
        assert body['reset'] is True
        assert body['client'] == '1.2.3.4'


class TestResetUserEndpoint:
    def test_requires_auth(self, client):
        r = client.delete('/admin/rate-limits/users/alice')
        assert r.status_code == 401

    def test_reset_succeeds(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.delete('/admin/rate-limits/users/alice', headers=_auth(ADMIN_TOKEN))
        assert r.status_code == 200
        body = r.json()
        assert body['reset'] is True
        assert body['user'] == 'alice'
