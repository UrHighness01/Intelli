"""Tests for the user management API and auth.py helper functions.

Covers:
  auth module:
    - list_users()
    - delete_user()   (including protection of 'admin')
    - change_password()

  HTTP endpoints (admin Bearer auth required):
    - GET  /admin/users
    - POST /admin/users
    - DELETE /admin/users/{username}
    - POST /admin/users/{username}/password
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixture: isolated auth + real login
# ---------------------------------------------------------------------------

@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """Return (TestClient, admin_token) with isolated users.json."""
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'adminpass-mgmt')
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    from app import app
    client = TestClient(app)

    r = client.post('/admin/login', json={'username': 'admin', 'password': 'adminpass-mgmt'})
    assert r.status_code == 200, r.text
    admin_token = r.json()['token']
    return client, admin_token


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


# ===========================================================================
# auth.list_users() — unit tests
# ===========================================================================

class TestListUsers:
    def test_returns_admin_by_default(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u.json')
        auth._ensure_default_admin.__module__  # touch module
        monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'pw')
        auth._ensure_default_admin()
        users = auth.list_users()
        names = [u['username'] for u in users]
        assert 'admin' in names

    def test_contains_expected_fields(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u2.json')
        auth.create_user('bob', 'bobpass', roles=['user'])
        users = auth.list_users()
        bob = next((u for u in users if u['username'] == 'bob'), None)
        assert bob is not None
        assert bob['roles'] == ['user']
        assert isinstance(bob['has_tool_restrictions'], bool)

    def test_no_passwords_returned(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u3.json')
        auth.create_user('carol', 'secret', roles=['admin'])
        users = auth.list_users()
        for u in users:
            assert 'salt' not in u
            assert 'hash' not in u
            assert 'password' not in u

    def test_has_tool_restrictions_false_without_restriction(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u4.json')
        auth.create_user('dave', 'pw', roles=['user'])
        users = auth.list_users()
        dave = next(u for u in users if u['username'] == 'dave')
        assert dave['has_tool_restrictions'] is False

    def test_has_tool_restrictions_true_with_restriction(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u5.json')
        auth.create_user('eve', 'pw', roles=['user'])
        auth.set_user_allowed_tools('eve', ['file.read'])
        users = auth.list_users()
        eve = next(u for u in users if u['username'] == 'eve')
        assert eve['has_tool_restrictions'] is True


# ===========================================================================
# auth.delete_user() — unit tests
# ===========================================================================

class TestDeleteUser:
    def test_delete_existing_user(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u.json')
        auth.create_user('frank', 'pw', roles=['user'])
        assert auth.delete_user('frank') is True
        users = auth.list_users()
        assert not any(u['username'] == 'frank' for u in users)

    def test_delete_nonexistent_returns_false(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u2.json')
        assert auth.delete_user('nobody') is False

    def test_cannot_delete_admin(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u3.json')
        monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'pw')
        auth._ensure_default_admin()
        assert auth.delete_user('admin') is False
        users = auth.list_users()
        assert any(u['username'] == 'admin' for u in users)


# ===========================================================================
# auth.change_password() — unit tests
# ===========================================================================

class TestChangePassword:
    def test_change_password_success(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u.json')
        monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'r.json')
        auth.create_user('gina', 'oldpw', roles=['user'])
        assert auth.change_password('gina', 'newpw') is True
        # Should be able to authenticate with the new password
        result = auth.authenticate_user('gina', 'newpw')
        assert result is not None
        assert 'access_token' in result

    def test_old_password_rejected_after_change(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u2.json')
        monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'r2.json')
        auth.create_user('hank', 'oldpw', roles=['user'])
        auth.change_password('hank', 'newpw')
        assert auth.authenticate_user('hank', 'oldpw') is None

    def test_change_password_nonexistent_returns_false(self, tmp_path, monkeypatch):
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u3.json')
        assert auth.change_password('nobody', 'pw') is False


# ===========================================================================
# GET /admin/users
# ===========================================================================

class TestListUsersEndpoint:
    def test_requires_auth(self, setup):
        client, _ = setup
        r = client.get('/admin/users')
        assert r.status_code == 401

    def test_returns_user_list(self, setup):
        client, token = setup
        r = client.get('/admin/users', headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert 'users' in data
        assert isinstance(data['users'], list)
        names = [u['username'] for u in data['users']]
        assert 'admin' in names

    def test_no_passwords_in_response(self, setup):
        client, token = setup
        r = client.get('/admin/users', headers=_auth(token))
        for u in r.json()['users']:
            assert 'password' not in u
            assert 'salt' not in u
            assert 'hash' not in u


# ===========================================================================
# POST /admin/users
# ===========================================================================

class TestCreateUserEndpoint:
    def test_requires_auth(self, setup):
        client, _ = setup
        r = client.post('/admin/users', json={'username': 'ivan', 'password': 'pw'})
        assert r.status_code == 401

    def test_create_success(self, setup):
        client, token = setup
        r = client.post('/admin/users',
                        json={'username': 'ivan', 'password': 'pw123', 'roles': ['user']},
                        headers=_auth(token))
        assert r.status_code == 201
        data = r.json()
        assert data['username'] == 'ivan'
        assert data['roles'] == ['user']

    def test_create_default_role(self, setup):
        client, token = setup
        r = client.post('/admin/users',
                        json={'username': 'judy', 'password': 'pw'},
                        headers=_auth(token))
        assert r.status_code == 201
        # default roles=['user'] as per Pydantic model default
        assert 'roles' in r.json()

    def test_create_duplicate_returns_409(self, setup):
        client, token = setup
        payload = {'username': 'kurt', 'password': 'pw', 'roles': ['user']}
        client.post('/admin/users', json=payload, headers=_auth(token))
        r = client.post('/admin/users', json=payload, headers=_auth(token))
        assert r.status_code == 409

    def test_new_user_can_login(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'luna', 'password': 'lunapass', 'roles': ['user']},
                    headers=_auth(token))
        r = client.post('/admin/login', json={'username': 'luna', 'password': 'lunapass'})
        assert r.status_code == 200
        assert 'token' in r.json()

    def test_new_user_appears_in_list(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'mike', 'password': 'pw', 'roles': ['user']},
                    headers=_auth(token))
        r = client.get('/admin/users', headers=_auth(token))
        names = [u['username'] for u in r.json()['users']]
        assert 'mike' in names

    def test_audit_log_written(self, setup, tmp_path, monkeypatch):
        from pathlib import Path
        import app as _app
        log = tmp_path / 'audit.log'
        monkeypatch.setattr(_app, 'AUDIT_PATH', log)
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'nick', 'password': 'pw', 'roles': ['admin']},
                    headers=_auth(token))
        assert log.exists()
        content = log.read_text()
        assert 'create_user' in content
        assert 'nick' in content


# ===========================================================================
# DELETE /admin/users/{username}
# ===========================================================================

class TestDeleteUserEndpoint:
    def test_requires_auth(self, setup):
        client, token = setup
        # Create a user first
        client.post('/admin/users',
                    json={'username': 'olivia', 'password': 'pw', 'roles': ['user']},
                    headers=_auth(token))
        r = client.delete('/admin/users/olivia')
        assert r.status_code == 401

    def test_delete_existing_user(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'peter', 'password': 'pw', 'roles': ['user']},
                    headers=_auth(token))
        r = client.delete('/admin/users/peter', headers=_auth(token))
        assert r.status_code == 200
        assert r.json()['deleted'] == 'peter'

    def test_delete_removes_from_list(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'quinn', 'password': 'pw', 'roles': ['user']},
                    headers=_auth(token))
        client.delete('/admin/users/quinn', headers=_auth(token))
        r = client.get('/admin/users', headers=_auth(token))
        names = [u['username'] for u in r.json()['users']]
        assert 'quinn' not in names

    def test_delete_nonexistent_returns_404(self, setup):
        client, token = setup
        r = client.delete('/admin/users/nobody', headers=_auth(token))
        assert r.status_code == 404

    def test_cannot_delete_admin_via_api(self, setup):
        client, token = setup
        r = client.delete('/admin/users/admin', headers=_auth(token))
        assert r.status_code == 403

    def test_deleted_user_cannot_login(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'rose', 'password': 'rosepass', 'roles': ['user']},
                    headers=_auth(token))
        client.delete('/admin/users/rose', headers=_auth(token))
        r = client.post('/admin/login', json={'username': 'rose', 'password': 'rosepass'})
        assert r.status_code != 200


# ===========================================================================
# POST /admin/users/{username}/password
# ===========================================================================

class TestChangePasswordEndpoint:
    def test_requires_auth(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'sam', 'password': 'pw', 'roles': ['user']},
                    headers=_auth(token))
        r = client.post('/admin/users/sam/password', json={'new_password': 'newpw'})
        assert r.status_code == 401

    def test_change_password_success(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'tia', 'password': 'oldpw', 'roles': ['user']},
                    headers=_auth(token))
        r = client.post('/admin/users/tia/password',
                        json={'new_password': 'newpw'},
                        headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        assert data['username'] == 'tia'
        assert data['password_changed'] is True

    def test_new_password_allows_login(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'uma', 'password': 'oldpw', 'roles': ['user']},
                    headers=_auth(token))
        client.post('/admin/users/uma/password',
                    json={'new_password': 'freshpw'},
                    headers=_auth(token))
        r = client.post('/admin/login', json={'username': 'uma', 'password': 'freshpw'})
        assert r.status_code == 200

    def test_old_password_rejected_after_change(self, setup):
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'vera', 'password': 'oldpw', 'roles': ['user']},
                    headers=_auth(token))
        client.post('/admin/users/vera/password',
                    json={'new_password': 'freshpw'},
                    headers=_auth(token))
        r = client.post('/admin/login', json={'username': 'vera', 'password': 'oldpw'})
        assert r.status_code != 200

    def test_nonexistent_user_returns_404(self, setup):
        client, token = setup
        r = client.post('/admin/users/nobody/password',
                        json={'new_password': 'pw'},
                        headers=_auth(token))
        assert r.status_code == 404

    def test_audit_log_written(self, setup, tmp_path, monkeypatch):
        import app as _app
        log = tmp_path / 'audit2.log'
        monkeypatch.setattr(_app, 'AUDIT_PATH', log)
        client, token = setup
        client.post('/admin/users',
                    json={'username': 'wade', 'password': 'pw', 'roles': ['user']},
                    headers=_auth(token))
        client.post('/admin/users/wade/password',
                    json={'new_password': 'newpw'},
                    headers=_auth(token))
        content = log.read_text()
        assert 'change_password' in content
        assert 'wade' in content
