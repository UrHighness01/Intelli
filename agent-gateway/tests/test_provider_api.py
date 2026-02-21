"""Tests for the provider key management API endpoints.

POST  /admin/providers/{provider}/key
GET   /admin/providers/{provider}/key/status
DELETE /admin/providers/{provider}/key
GET   /providers
"""
import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import auth


def _reset_admin(pw: str = 'dev-key'):
    """Ensure a clean admin account for each test."""
    import json
    from pathlib import Path
    users_path = Path(__file__).parent.parent / 'users.json'
    try:
        data = json.loads(users_path.read_text(encoding='utf-8'))
    except Exception:
        data = {}
    data.pop('admin', None)
    users_path.write_text(json.dumps(data), encoding='utf-8')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth.create_user('admin', pw, ['admin'])


def _get_admin_token(client) -> str:
    r = client.post('/admin/login', json={'username': 'admin', 'password': 'dev-key'})
    assert r.status_code == 200, r.text
    return r.json()['token']


@pytest.fixture
def client():
    _reset_admin()
    from app import app
    with TestClient(app) as c:
        yield c


# ── /providers ─────────────────────────────────────────────────────────────

def test_providers_list_requires_auth(client):
    r = client.get('/providers')
    assert r.status_code == 401


def test_providers_list_returns_known_providers(client):
    token = _get_admin_token(client)
    r = client.get('/providers', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    names = [p['name'] for p in r.json()['providers']]
    assert 'openai' in names
    assert 'anthropic' in names
    assert 'ollama' in names


# ── set provider key ───────────────────────────────────────────────────────

def test_set_provider_key_requires_auth(client):
    r = client.post('/admin/providers/openai/key', json={'key': 'sk-test'})
    assert r.status_code == 401


def test_set_provider_key_success(client):
    token = _get_admin_token(client)
    r = client.post(
        '/admin/providers/openai/key',
        json={'key': 'sk-test-1234'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 200
    assert r.json()['status'] == 'stored'
    assert r.json()['provider'] == 'openai'


def test_set_provider_key_empty_key_rejected(client):
    token = _get_admin_token(client)
    r = client.post(
        '/admin/providers/openai/key',
        json={'key': ''},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 400


# ── key status ─────────────────────────────────────────────────────────────

def test_key_status_requires_auth(client):
    r = client.get('/admin/providers/openai/key/status')
    assert r.status_code == 401


def test_key_status_reflects_stored_key(client):
    token = _get_admin_token(client)
    headers = {'Authorization': f'Bearer {token}'}

    # Store a key
    client.post('/admin/providers/openrouter/key', json={'key': 'or-key-abc'}, headers=headers)

    r = client.get('/admin/providers/openrouter/key/status', headers=headers)
    assert r.status_code == 200
    assert r.json()['configured'] is True


def test_key_status_unconfigured_provider(client, monkeypatch):
    """A provider with no key stored should report configured=False."""
    token = _get_admin_token(client)
    # Ensure no key exists for a synthetic provider
    import providers.provider_adapter as pa
    monkeypatch.setattr(pa.ProviderKeyStore, 'get_key', lambda *_: None)
    r = client.get(
        '/admin/providers/nonexistent_prov/key/status',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 200
    assert r.json()['configured'] is False


# ── delete provider key ────────────────────────────────────────────────────

def test_delete_provider_key_requires_auth(client):
    r = client.delete('/admin/providers/openai/key')
    assert r.status_code == 401


def test_delete_provider_key_success(client):
    token = _get_admin_token(client)
    headers = {'Authorization': f'Bearer {token}'}
    # First store a key
    client.post('/admin/providers/anthropic/key', json={'key': 'ant-key'}, headers=headers)
    # Then delete it
    r = client.delete('/admin/providers/anthropic/key', headers=headers)
    assert r.status_code == 200
    assert r.json()['status'] == 'deleted'


# ── key rotation ──────────────────────────────────────────────────────────

def test_set_provider_key_returns_expires_at(client):
    token = _get_admin_token(client)
    r = client.post(
        '/admin/providers/openai/key',
        json={'key': 'sk-ttl-test', 'ttl_days': 30},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'stored'
    assert 'expires_at' in body
    assert body['expires_at'] is not None


def test_rotate_provider_key_requires_auth(client):
    r = client.post('/admin/providers/openai/key/rotate', json={'key': 'sk-new'})
    assert r.status_code == 401


def test_rotate_provider_key_success(client):
    token = _get_admin_token(client)
    headers = {'Authorization': f'Bearer {token}'}
    # Store initial key
    client.post('/admin/providers/openai/key', json={'key': 'sk-old', 'ttl_days': 30}, headers=headers)
    # Rotate it
    r = client.post(
        '/admin/providers/openai/key/rotate',
        json={'key': 'sk-new', 'ttl_days': 60},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'rotated'
    assert body['provider'] == 'openai'
    assert 'last_rotated' in body
    assert body['last_rotated'] is not None
    assert 'expires_at' in body


def test_rotate_provider_key_empty_key_rejected(client):
    token = _get_admin_token(client)
    r = client.post(
        '/admin/providers/openai/key/rotate',
        json={'key': ''},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 400


# ── key expiry ─────────────────────────────────────────────────────────────

def test_key_expiry_requires_auth(client):
    r = client.get('/admin/providers/openai/key/expiry')
    assert r.status_code == 401


def test_key_expiry_404_when_no_metadata(client, monkeypatch):
    # Patch the symbol as it lives in app.py (imported with `from ... import`)
    import app as _app
    monkeypatch.setattr(_app, 'get_key_metadata', lambda *_: None)
    token = _get_admin_token(client)
    r = client.get(
        '/admin/providers/openai/key/expiry',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert r.status_code == 404


def test_key_expiry_returns_metadata(client):
    token = _get_admin_token(client)
    headers = {'Authorization': f'Bearer {token}'}
    # Store a key with TTL so metadata exists
    client.post('/admin/providers/openai/key', json={'key': 'sk-exp', 'ttl_days': 30}, headers=headers)
    r = client.get('/admin/providers/openai/key/expiry', headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body['provider'] == 'openai'
    assert 'expires_at' in body
    assert 'is_expired' in body
    assert body['is_expired'] is False
    assert 'days_until_expiry' in body
    assert body['days_until_expiry'] > 0


# ── expiring providers ─────────────────────────────────────────────────────

def test_expiring_keys_requires_auth(client):
    r = client.get('/admin/providers/expiring')
    assert r.status_code == 401


def test_expiring_keys_returns_list(client):
    token = _get_admin_token(client)
    headers = {'Authorization': f'Bearer {token}'}
    client.post('/admin/providers/openai/key', json={'key': 'sk-exp2', 'ttl_days': 100}, headers=headers)
    r = client.get('/admin/providers/expiring?within_days=7', headers=headers)
    assert r.status_code == 200
    assert 'expiring' in r.json()
    assert isinstance(r.json()['expiring'], list)
