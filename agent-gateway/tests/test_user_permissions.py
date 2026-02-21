"""Tests for per-user scoped tool permissions.

Each user may have an ``allowed_tools`` allow-list.  When set, calls to tools
outside the list are rejected with HTTP 403 {status: tool_not_permitted}.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'adminpass-perm')
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    # Create a restricted user
    auth.create_user('alice', 'alicepass', roles=['user'])

    from app import app
    client = TestClient(app)

    # Login as admin
    r = client.post('/admin/login', json={'username': 'admin', 'password': 'adminpass-perm'})
    admin_token = r.json()['token']

    # Login as alice
    r = client.post('/admin/login', json={'username': 'alice', 'password': 'alicepass'})
    alice_token = r.json()['token']

    return client, admin_token, alice_token


# ---- get_user_allowed_tools / set_user_allowed_tools unit tests --------

def test_auth_get_allowed_tools_no_restriction(tmp_path, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u.json')
    auth.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth.create_user('bob', 'pw', roles=['user'])
    assert auth.get_user_allowed_tools('bob') is None


def test_auth_set_and_get_allowed_tools(tmp_path, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u2.json')
    auth.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth.create_user('carol', 'pw', roles=['user'])
    auth.set_user_allowed_tools('carol', ['noop', 'file.read'])
    result = auth.get_user_allowed_tools('carol')
    assert result is not None
    assert set(result) == {'noop', 'file.read'}


def test_auth_clear_allowed_tools(tmp_path, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u3.json')
    auth.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth.create_user('dave', 'pw', roles=['user'])
    auth.set_user_allowed_tools('dave', ['noop'])
    auth.set_user_allowed_tools('dave', None)
    assert auth.get_user_allowed_tools('dave') is None


def test_auth_set_tools_nonexistent_user(tmp_path, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'u4.json')
    auth.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ok = auth.set_user_allowed_tools('nobody', ['noop'])
    assert ok is False


# ---- HTTP endpoint tests -----------------------------------------------

def test_get_permissions_no_restriction(setup):
    client, admin_token, alice_token = setup
    r = client.get('/admin/users/alice/permissions',
                   headers={'Authorization': f'Bearer {admin_token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['username'] == 'alice'
    assert body['allowed_tools'] is None


def test_set_permissions_endpoint(setup):
    client, admin_token, alice_token = setup
    r = client.put('/admin/users/alice/permissions',
                   json={'allowed_tools': ['noop', 'file.read']},
                   headers={'Authorization': f'Bearer {admin_token}'})
    assert r.status_code == 200
    body = r.json()
    assert set(body['allowed_tools']) == {'noop', 'file.read'}


def test_get_permissions_unknown_user(setup):
    client, admin_token, _ = setup
    # No 404 for get — just returns null (user might not exist in users.json)
    # Actually set should return 404
    r = client.put('/admin/users/nobody/permissions',
                   json={'allowed_tools': ['noop']},
                   headers={'Authorization': f'Bearer {admin_token}'})
    assert r.status_code == 404


def test_permissions_requires_admin(setup):
    client, _, alice_token = setup
    r = client.get('/admin/users/alice/permissions',
                   headers={'Authorization': f'Bearer {alice_token}'})
    assert r.status_code == 403


# ---- Tool call enforcement -----------------------------------------------

def test_tool_call_allowed_tool(setup):
    """Alice has noop in her allow-list → 200."""
    client, admin_token, alice_token = setup
    client.put('/admin/users/alice/permissions',
               json={'allowed_tools': ['noop']},
               headers={'Authorization': f'Bearer {admin_token}'})
    r = client.post('/tools/call',
                    json={'tool': 'noop', 'args': {}},
                    headers={'Authorization': f'Bearer {alice_token}'})
    assert r.status_code == 200


def test_tool_call_blocked_tool(setup):
    """Alice does NOT have file.read in her allow-list → 403."""
    client, admin_token, alice_token = setup
    client.put('/admin/users/alice/permissions',
               json={'allowed_tools': ['noop']},
               headers={'Authorization': f'Bearer {admin_token}'})
    r = client.post('/tools/call',
                    json={'tool': 'file.read', 'args': {'path': '/tmp/x'}},
                    headers={'Authorization': f'Bearer {alice_token}'})
    assert r.status_code == 403
    detail = r.json()['detail']
    assert detail['status'] == 'tool_not_permitted'
    assert detail['tool'] == 'file.read'


def test_tool_call_no_restriction_unrestricted(setup):
    """Without a Bearer token the tool call proceeds normally (no restriction)."""
    client, admin_token, alice_token = setup
    r = client.post('/tools/call', json={'tool': 'noop', 'args': {}})
    assert r.status_code == 200


def test_tool_call_null_restriction_allows_all(setup):
    """Clearing the allow-list (null) makes alice unrestricted again."""
    client, admin_token, alice_token = setup
    # Set restriction
    client.put('/admin/users/alice/permissions',
               json={'allowed_tools': ['noop']},
               headers={'Authorization': f'Bearer {admin_token}'})
    # Clear it
    client.put('/admin/users/alice/permissions',
               json={'allowed_tools': None},
               headers={'Authorization': f'Bearer {admin_token}'})
    # file.read should be fine now
    r = client.post('/tools/call',
                    json={'tool': 'file.read', 'args': {'path': '/tmp/x'}},
                    headers={'Authorization': f'Bearer {alice_token}'})
    assert r.status_code == 200
