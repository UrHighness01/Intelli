from fastapi.testclient import TestClient
from app import app
import os
import time


client = TestClient(app)


def test_login_and_refresh_and_revoke(monkeypatch):
    # ensure default admin exists via env
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'pw123')
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
