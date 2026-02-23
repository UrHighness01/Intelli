"""Tests for the first-run setup endpoints.

Covers:
  GET  /admin/setup-status   — returns {needs_setup: bool}
  POST /admin/setup           — creates the admin account (first-run only)
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_client(tmp_path, monkeypatch):
    """TestClient with no admin user and no env-var default password."""
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    monkeypatch.delenv('AGENT_GATEWAY_ADMIN_PASSWORD', raising=False)
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    # Do NOT call _ensure_default_admin() — we want 0 users
    from app import app
    return TestClient(app)


@pytest.fixture()
def client_with_admin(tmp_path, monkeypatch):
    """TestClient where the admin account already exists."""
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'already-set-pass1')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()
    from app import app
    return TestClient(app)


# ===========================================================================
# GET /admin/setup-status
# ===========================================================================

class TestSetupStatus:
    def test_needs_setup_true_when_no_admin(self, fresh_client):
        r = fresh_client.get('/admin/setup-status')
        assert r.status_code == 200
        assert r.json() == {'needs_setup': True}

    def test_needs_setup_false_when_admin_exists(self, client_with_admin):
        r = client_with_admin.get('/admin/setup-status')
        assert r.status_code == 200
        assert r.json() == {'needs_setup': False}

    def test_no_auth_required(self, fresh_client):
        """Endpoint must be reachable without a Bearer token."""
        r = fresh_client.get('/admin/setup-status')
        assert r.status_code == 200


# ===========================================================================
# POST /admin/setup
# ===========================================================================

class TestSetupEndpoint:
    def test_creates_admin_and_returns_token(self, fresh_client):
        r = fresh_client.post('/admin/setup', json={'password': 'NewAdminPass1!'})
        assert r.status_code == 200
        body = r.json()
        assert 'token' in body
        assert 'refresh_token' in body
        assert isinstance(body['token'], str) and len(body['token']) > 10

    def test_setup_status_becomes_false_after_setup(self, fresh_client):
        fresh_client.post('/admin/setup', json={'password': 'NewAdminPass1!'})
        r = fresh_client.get('/admin/setup-status')
        assert r.json() == {'needs_setup': False}

    def test_token_is_usable_for_admin_apis(self, fresh_client):
        r = fresh_client.post('/admin/setup', json={'password': 'NewAdminPass1!'})
        token = r.json()['token']
        r2 = fresh_client.get(
            '/admin/users',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r2.status_code == 200

    def test_rejects_short_password(self, fresh_client):
        r = fresh_client.post('/admin/setup', json={'password': 'short'})
        assert r.status_code == 400
        assert 'characters' in r.json()['detail'].lower()

    def test_rejects_missing_password(self, fresh_client):
        r = fresh_client.post('/admin/setup', json={})
        # Pydantic will raise a 422 for missing required field
        assert r.status_code == 422

    def test_409_when_admin_already_exists(self, client_with_admin):
        r = client_with_admin.post('/admin/setup', json={'password': 'AnotherPass123!'})
        assert r.status_code == 409
        assert 'already exists' in r.json()['detail'].lower()

    def test_no_auth_required_when_no_admin(self, fresh_client):
        """Setup must be callable without any existing credentials."""
        r = fresh_client.post('/admin/setup', json={'password': 'NewAdminPass1!'})
        assert r.status_code == 200

    def test_can_login_with_setup_password(self, fresh_client):
        fresh_client.post('/admin/setup', json={'password': 'MySetupPW99!'})
        r = fresh_client.post(
            '/admin/login',
            json={'username': 'admin', 'password': 'MySetupPW99!'},
        )
        assert r.status_code == 200
        assert 'token' in r.json()
