"""Tests for GDPR consent export / erasure API.

Covers:
  GET  /consent/export/{actor} — data-subject access request
  DELETE /consent/export/{actor} — right to erasure
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

_GW_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _GW_DIR not in sys.path:
    sys.path.insert(0, _GW_DIR)

os.environ.setdefault('AGENT_GATEWAY_ALLOWED_CAPS', 'ALL')


# ---------------------------------------------------------------------------
# Unit-level tests for consent_log.export_actor_data / erase_actor_data
# ---------------------------------------------------------------------------

class TestConsentLogGDPRFunctions:

    def test_export_empty_timeline(self, tmp_path, monkeypatch):
        timeline = tmp_path / 'c.jsonl'
        monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(timeline))
        import consent_log
        importlib.reload(consent_log)
        assert consent_log.export_actor_data('nobody') == []

    def test_export_returns_only_matching_actor(self, tmp_path, monkeypatch):
        timeline = tmp_path / 'c.jsonl'
        monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(timeline))
        import consent_log
        importlib.reload(consent_log)

        snap = {'inputs': [{'name': 'q', 'value': 'hello'}]}
        consent_log.log_context_share('https://a.com', 'https://a.com', snap, actor='alice…')
        consent_log.log_context_share('https://b.com', 'https://b.com', snap, actor='bob…')
        consent_log.log_context_share('https://a.com', 'https://a.com', snap, actor='alice…')

        result = consent_log.export_actor_data('alice…')
        assert len(result) == 2
        assert all(e['actor'] == 'alice…' for e in result)

    def test_export_oldest_first(self, tmp_path, monkeypatch):
        timeline = tmp_path / 'c.jsonl'
        monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(timeline))
        import consent_log
        importlib.reload(consent_log)

        snap = {'inputs': []}
        consent_log.log_context_share('https://x.com', 'https://x.com', snap, actor='user1…')
        consent_log.log_context_share('https://y.com', 'https://y.com', snap, actor='user1…')

        result = consent_log.export_actor_data('user1…')
        assert result[0]['url'] == 'https://x.com'
        assert result[1]['url'] == 'https://y.com'

    def test_erase_removes_only_target_actor(self, tmp_path, monkeypatch):
        timeline = tmp_path / 'c.jsonl'
        monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(timeline))
        import consent_log
        importlib.reload(consent_log)

        snap = {'inputs': []}
        consent_log.log_context_share('https://a.com', 'https://a.com', snap, actor='alice…')
        consent_log.log_context_share('https://b.com', 'https://b.com', snap, actor='bob…')
        consent_log.log_context_share('https://a.com', 'https://a.com', snap, actor='alice…')

        removed = consent_log.erase_actor_data('alice…')
        assert removed == 2
        remaining = consent_log.get_timeline()
        assert len(remaining) == 1
        assert remaining[0]['actor'] == 'bob…'

    def test_erase_nonexistent_actor_returns_zero(self, tmp_path, monkeypatch):
        timeline = tmp_path / 'c.jsonl'
        monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(timeline))
        import consent_log
        importlib.reload(consent_log)

        removed = consent_log.erase_actor_data('ghost…')
        assert removed == 0


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """Provide (TestClient, consent_log_module, admin_token)."""
    consent_file = tmp_path / 'consent.jsonl'
    monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(consent_file))
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'changeme')

    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    import consent_log
    importlib.reload(consent_log)

    from app import app as _fastapi_app
    tc = TestClient(_fastapi_app, raise_server_exceptions=False)

    # Login via the test client (uses the already-loaded auth module)
    r = tc.post('/admin/login', json={'username': 'admin', 'password': 'changeme'})
    assert r.status_code == 200, f'login failed: {r.text}'
    token = r.json()['token']

    import app as _app
    _app._consent = consent_log

    return tc, consent_log, token


def test_export_requires_admin(api_client):
    tc, _, _ = api_client
    r = tc.get('/consent/export/nobody')
    assert r.status_code == 401


def test_erase_requires_admin(api_client):
    tc, _, _ = api_client
    r = tc.delete('/consent/export/nobody')
    assert r.status_code == 401


def test_export_empty_returns_empty_list(api_client):
    tc, _, token = api_client
    r = tc.get('/consent/export/nobody', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['count'] == 0
    assert body['entries'] == []


def test_export_returns_all_actor_entries(api_client):
    tc, clog, token = api_client
    snap = {'inputs': [{'name': 'search', 'value': 'secret'}]}
    clog.log_context_share('https://site.com', 'https://site.com', snap, actor='abc123…')
    clog.log_context_share('https://other.com', 'https://other.com', snap, actor='xyz000…')
    clog.log_context_share('https://site2.com', 'https://site2.com', snap, actor='abc123…')

    r = tc.get('/consent/export/abc123…', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['count'] == 2
    assert all(e['actor'] == 'abc123…' for e in body['entries'])


def test_erase_returns_removed_count(api_client):
    tc, clog, token = api_client
    snap = {'inputs': []}
    clog.log_context_share('https://a.com', 'https://a.com', snap, actor='del123…')
    clog.log_context_share('https://b.com', 'https://b.com', snap, actor='keep99…')
    clog.log_context_share('https://c.com', 'https://c.com', snap, actor='del123…')

    r = tc.delete('/consent/export/del123…', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['removed'] == 2
    assert body['actor'] == 'del123…'


def test_erase_leaves_other_actors_intact(api_client):
    tc, clog, token = api_client
    snap = {'inputs': []}
    clog.log_context_share('https://a.com', 'https://a.com', snap, actor='victim…')
    clog.log_context_share('https://b.com', 'https://b.com', snap, actor='innocent…')

    tc.delete('/consent/export/victim…', headers={'Authorization': f'Bearer {token}'})

    # Verify innocents remain
    remaining = clog.get_timeline()
    assert len(remaining) == 1
    assert remaining[0]['actor'] == 'innocent…'
