"""Tests for the agent kill-switch (POST/DELETE/GET /admin/kill-switch)."""
import pytest
from fastapi.testclient import TestClient
import os


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    """Ensure the kill-switch is always cleared between tests."""
    import app as _app
    _app._kill_switch.clear()
    _app._kill_switch_reason = ''
    yield
    _app._kill_switch.clear()
    _app._kill_switch_reason = ''


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'testpass-ks')
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)

    r = client.post('/admin/login', json={'username': 'admin', 'password': 'testpass-ks'})
    token = r.json()['token']
    return client, token


def test_kill_switch_status_inactive(admin_client):
    client, token = admin_client
    r = client.get('/admin/kill-switch', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['active'] is False
    assert body['reason'] == ''


def test_kill_switch_activate(admin_client):
    client, token = admin_client
    r = client.post(
        '/admin/kill-switch',
        json={'reason': 'incident CVE-2025-TEST'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 200
    body = r.json()
    assert body['active'] is True
    assert body['reason'] == 'incident CVE-2025-TEST'


def test_kill_switch_blocks_tool_call(admin_client):
    client, token = admin_client
    # Activate
    client.post(
        '/admin/kill-switch',
        json={'reason': 'test block'},
        headers={'Authorization': f'Bearer {token}'},
    )
    # Tool call should be 503
    r = client.post('/tools/call', json={'tool': 'noop', 'args': {}})
    assert r.status_code == 503
    body = r.json()
    assert 'kill-switch' in body['detail']['error']


def test_kill_switch_deactivate_resumes(admin_client):
    client, token = admin_client
    # Activate then deactivate
    client.post('/admin/kill-switch', json={'reason': 'temp'},
                headers={'Authorization': f'Bearer {token}'})
    r = client.delete('/admin/kill-switch', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    assert r.json()['active'] is False

    # Tool call should work again
    r = client.post('/tools/call', json={'tool': 'noop', 'args': {}})
    assert r.status_code == 200


def test_kill_switch_requires_admin(admin_client):
    client, _ = admin_client
    r = client.post('/admin/kill-switch', json={'reason': 'x'})
    assert r.status_code == 401

    r = client.delete('/admin/kill-switch')
    assert r.status_code == 401

    r = client.get('/admin/kill-switch')
    assert r.status_code == 401
