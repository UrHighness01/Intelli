from fastapi.testclient import TestClient
from app import app
import auth as _auth
import os
import time


client = TestClient(app)


def test_login_and_refresh_and_revoke(monkeypatch):
    # ensure default admin exists via env
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'pw123')
    # Force (re-)creation of admin with this password in case users.json is stale
    _auth._load_users  # ensure module loaded
    users_path = _auth.USERS_PATH
    if users_path.exists():
        import json
        with users_path.open('r') as f:
            u = json.load(f)
        if 'admin' in u:
            u.pop('admin')
            with users_path.open('w') as f:
                json.dump(u, f)
    _auth._TOKENS.clear()
    _auth._REFRESH_TOKENS.clear()
    _auth._ensure_default_admin()
    # login
    r = client.post('/admin/login', json={'username': 'admin', 'password': 'pw123'})
    assert r.status_code == 200
    data = r.json()
    assert 'token' in data and 'refresh_token' in data
    at = data['token']
    rt = data['refresh_token']

    # refresh
    r2 = client.post('/admin/refresh', json={'refresh_token': rt})
    assert r2.status_code == 200
    new_at = r2.json().get('token')
    assert new_at and new_at != at

    # revoke refresh token using admin access
    r3 = client.post('/admin/revoke', json={'token': rt}, headers={'Authorization': f'Bearer {new_at}'})
    assert r3.status_code == 200

    # refresh again should fail
    r4 = client.post('/admin/refresh', json={'refresh_token': rt})
    assert r4.status_code == 401
