"""Tests for approval auto-reject timeout: GET/PUT /admin/approvals/config
and the ApprovalQueue.expire_pending() helper.
"""
import json
import time
import pytest
from fastapi.testclient import TestClient

import app as _app
from app import app
import auth as _auth
from supervisor import ApprovalQueue

client = TestClient(app)
_TEST_PW = 'dev-key-timeout'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_admin(pw: str = _TEST_PW):
    users_path = _auth.USERS_PATH
    users = {}
    if users_path.exists():
        try:
            with users_path.open('r') as f:
                users = json.load(f)
        except Exception:
            users = {}
    users.pop('admin', None)
    with users_path.open('w') as f:
        json.dump(users, f)
    _auth._TOKENS.clear()
    _auth._REFRESH_TOKENS.clear()
    _auth.create_user('admin', pw, roles=['admin'])


def _get_admin_token() -> str:
    r = client.post('/admin/login', json={'username': 'admin', 'password': _TEST_PW})
    assert r.status_code == 200, f'admin login failed: {r.text}'
    return r.json()['token']


def _auth_header(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _reset_approvals_config():
    """Restore timeout to 0 between tests."""
    _app._approvals_config['timeout_seconds'] = 0.0


# ---------------------------------------------------------------------------
# Unit tests — ApprovalQueue.expire_pending()
# ---------------------------------------------------------------------------

class TestExpirePending:
    def test_no_expiry_when_timeout_zero(self):
        q = ApprovalQueue()
        q.submit({'tool': 'file.write', 'args': {}})
        # timeout=0 should never expire
        assert q.expire_pending(0) == []

    def test_fresh_item_not_expired(self):
        q = ApprovalQueue()
        q.submit({'tool': 'file.write', 'args': {}})
        # items just submitted — well within a 60-second window
        assert q.expire_pending(60) == []

    def test_stale_item_is_rejected(self, monkeypatch):
        q = ApprovalQueue()
        id_ = q.submit({'tool': 'file.write', 'args': {}})
        # Back-date the enqueued_at so the item looks 10 seconds old
        q._store[id_]['enqueued_at'] -= 10
        expired = q.expire_pending(5)   # timeout = 5 s
        assert id_ in expired
        assert q._store[id_]['status'] == 'rejected'

    def test_non_pending_item_not_returned(self, monkeypatch):
        q = ApprovalQueue()
        id_ = q.submit({'tool': 'file.write', 'args': {}})
        q._store[id_]['enqueued_at'] -= 20
        q.approve(id_)  # now approved
        # should NOT appear in expire_pending, regardless of age
        assert q.expire_pending(5) == []

    def test_only_old_items_expired(self, monkeypatch):
        q = ApprovalQueue()
        fresh_id = q.submit({'tool': 'system.exec', 'args': {}})
        stale_id = q.submit({'tool': 'file.delete', 'args': {}})
        # Back-date only the stale item
        q._store[stale_id]['enqueued_at'] -= 20
        expired = q.expire_pending(10)
        assert stale_id in expired
        assert fresh_id not in expired
        assert q._store[stale_id]['status'] == 'rejected'
        assert q._store[fresh_id]['status'] == 'pending'

    def test_enqueued_at_field_present(self):
        q = ApprovalQueue()
        id_ = q.submit({'tool': 'file.read', 'args': {}})
        item = q._store[id_]
        assert 'enqueued_at' in item
        assert isinstance(item['enqueued_at'], float)
        assert item['enqueued_at'] <= time.time()


# ---------------------------------------------------------------------------
# GET /admin/approvals/config
# ---------------------------------------------------------------------------

class TestGetApprovalsConfig:
    def setup_method(self):
        _reset_admin()
        _reset_approvals_config()

    def test_requires_auth(self):
        r = client.get('/admin/approvals/config')
        assert r.status_code == 401

    def test_returns_timeout_seconds(self):
        token = _get_admin_token()
        r = client.get('/admin/approvals/config', headers=_auth_header(token))
        assert r.status_code == 200
        data = r.json()
        assert 'timeout_seconds' in data

    def test_default_is_zero(self):
        token = _get_admin_token()
        r = client.get('/admin/approvals/config', headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()['timeout_seconds'] == 0.0


# ---------------------------------------------------------------------------
# PUT /admin/approvals/config
# ---------------------------------------------------------------------------

class TestPutApprovalsConfig:
    def setup_method(self):
        _reset_admin()
        _reset_approvals_config()

    def test_requires_auth(self):
        r = client.put('/admin/approvals/config',
                       json={'timeout_seconds': 30}, headers={})
        assert r.status_code == 401

    def test_set_timeout(self):
        token = _get_admin_token()
        r = client.put('/admin/approvals/config',
                       json={'timeout_seconds': 120},
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()['timeout_seconds'] == 120.0

    def test_set_timeout_to_zero_disables(self):
        token = _get_admin_token()
        # First set non-zero
        client.put('/admin/approvals/config',
                   json={'timeout_seconds': 60},
                   headers=_auth_header(token))
        # Then set to 0
        r = client.put('/admin/approvals/config',
                       json={'timeout_seconds': 0},
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()['timeout_seconds'] == 0.0

    def test_negative_value_rejected(self):
        token = _get_admin_token()
        r = client.put('/admin/approvals/config',
                       json={'timeout_seconds': -1},
                       headers=_auth_header(token))
        assert r.status_code == 422

    def test_config_persists_across_get(self):
        token = _get_admin_token()
        client.put('/admin/approvals/config',
                   json={'timeout_seconds': 300},
                   headers=_auth_header(token))
        r = client.get('/admin/approvals/config', headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()['timeout_seconds'] == 300.0

    def test_fractional_seconds_accepted(self):
        token = _get_admin_token()
        r = client.put('/admin/approvals/config',
                       json={'timeout_seconds': 30.5},
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()['timeout_seconds'] == 30.5

    def test_missing_field_rejected(self):
        token = _get_admin_token()
        r = client.put('/admin/approvals/config',
                       json={},
                       headers=_auth_header(token))
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Integration: expire_pending + audit log
# ---------------------------------------------------------------------------

class TestExpireIntegration:
    def setup_method(self):
        _reset_admin()
        _reset_approvals_config()

    def test_expire_pending_rejects_in_queue(self):
        """Submit a stale approval; expire it; confirm status is rejected."""
        payload = {'tool': 'system.exec', 'args': {'cmd': 'rm -rf /'}}
        r = client.post('/tools/call', json=payload)
        assert r.status_code == 200
        req_id = r.json()['id']

        # Backdate the enqueued_at
        _app.supervisor.queue._store[req_id]['enqueued_at'] -= 100

        expired = _app.supervisor.queue.expire_pending(10)
        assert req_id in expired

        r = client.get(f'/approvals/{req_id}')
        assert r.status_code == 200
        assert r.json()['status'] == 'rejected'
