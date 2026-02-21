"""End-to-end integration tests for the Agent Gateway.

These tests exercise full HTTP flows from a user perspective using
FastAPI's TestClient (in-process).  They complement unit tests by
verifying that all layers (auth, supervisor, capability verifier,
consent timeline, key rotation, audit, approvals) work together
correctly.

All tests run with AGENT_GATEWAY_ALLOWED_CAPS=ALL (set by conftest.py)
so capability restrictions don't interfere unless specifically tested.
"""
import json
import os
import sys
import time
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import auth as _auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def client():
    """Module-scoped TestClient so the gateway is only started once."""
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_credentials(tmp_path):
    """Fresh admin account and token for each test."""
    users_path = _auth.USERS_PATH
    users = {}
    if users_path.exists():
        try:
            users = json.loads(users_path.read_text(encoding='utf-8'))
        except Exception:
            users = {}
    users.pop('admin', None)
    users_path.write_text(json.dumps(users), encoding='utf-8')
    _auth._TOKENS.clear()
    _auth._REFRESH_TOKENS.clear()
    _auth.create_user('admin', 'e2e-secret', ['admin'])
    yield 'admin', 'e2e-secret'


def _login(client, username, password):
    r = client.post('/admin/login', json={'username': username, 'password': password})
    assert r.status_code == 200, f'login failed: {r.text}'
    return r.json()['token']


# ---------------------------------------------------------------------------
# 1. Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_endpoint(self, client):
        r = client.get('/health')
        assert r.status_code == 200
        assert r.json().get('status') == 'ok'


# ---------------------------------------------------------------------------
# 2. Auth flow: register → login → protected resource
# ---------------------------------------------------------------------------

class TestAuthFlow:
    def test_register_and_login(self, client, admin_credentials):
        username, password = admin_credentials
        # Login as admin
        token = _login(client, username, password)
        assert len(token) > 10

    def test_unauthenticated_admin_endpoint_rejected(self, client):
        r = client.get('/admin/providers/openai/key/status')
        assert r.status_code == 401

    def test_authenticated_admin_endpoint_allowed(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        r = client.get(
            '/admin/providers/openai/key/status',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 200

    def test_invalid_token_rejected(self, client):
        r = client.get(
            '/admin/providers/openai/key/status',
            headers={'Authorization': 'Bearer bad-token-xyz'},
        )
        # Gateway may return 401 (Unauthorized) or 403 (Forbidden) for bad tokens
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 3. Tool call pipeline: validate → call → audit
# ---------------------------------------------------------------------------

class TestToolCallPipeline:
    def test_echo_call_returns_stubbed(self, client):
        r = client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'ping'}})
        assert r.status_code == 200
        assert r.json()['status'] == 'stubbed'

    def test_bad_schema_returns_400(self, client):
        r = client.post('/tools/call', json={'tool': 123})
        assert r.status_code in (400, 422)

    def test_validate_endpoint_accepts_valid_payload(self, client):
        r = client.post('/validate', json={'tool': 'echo', 'args': {'text': 'hi'}})
        assert r.status_code == 200
        assert r.json()['valid'] is True

    def test_validate_endpoint_rejects_invalid_payload(self, client):
        r = client.post('/validate', json={'tool': 99, 'no_args': True})
        assert r.status_code == 400

    def test_high_risk_call_enters_approval_queue(self, client):
        """system.exec is high-risk — should be queued for approval."""
        r = client.post('/tools/call', json={
            'tool': 'system.exec',
            'args': {'command': 'ls', 'timeout': 5},
        })
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'pending_approval'
        assert 'id' in body

    def test_approved_call_changes_status(self, client, admin_credentials):
        """Full approval cycle: enqueue → approve → status=approved."""
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        # Enqueue
        r = client.post('/tools/call', json={
            'tool': 'system.exec',
            'args': {'command': 'echo hello'},
        })
        assert r.status_code == 200
        req_id = r.json()['id']

        # Approve
        r = client.post(f'/approvals/{req_id}/approve', headers=headers)
        assert r.status_code == 200
        assert r.json()['status'] == 'approved'

        # Verify
        r = client.get(f'/approvals/{req_id}')
        assert r.status_code == 200
        assert r.json()['status'] == 'approved'


# ---------------------------------------------------------------------------
# 4. Capability system
# ---------------------------------------------------------------------------

class TestCapabilitySystem:
    def test_capabilities_list_returns_tools(self, client):
        r = client.get('/tools/capabilities')
        assert r.status_code == 200
        body = r.json()
        tools = [t['tool'] for t in body.get('tools', [])]
        assert 'echo' in tools
        assert 'file.write' in tools
        assert 'system.exec' in tools

    def test_capability_denied_when_cap_not_in_allowlist(self, client, monkeypatch):
        """Restrict caps to only fs.read — file.write should be denied."""
        from tools.capability import CapabilityVerifier
        # Create a verifier that only allows fs.read
        restricted = CapabilityVerifier(allowed=frozenset({'fs.read'}))
        import app as _app
        monkeypatch.setattr(_app.supervisor, '_cap_verifier', restricted)

        r = client.post('/tools/call', json={
            'tool': 'file.write',
            'args': {'path': '/tmp/x', 'content': 'hello'},
        })
        assert r.status_code == 403
        detail = r.json().get('detail', {})
        assert detail.get('status') == 'capability_denied'
        assert 'fs.write' in detail.get('denied_capabilities', [])

    def test_allowed_cap_passes_through(self, client):
        """With ALL caps (conftest.py default), file.write should not be blocked 
        by capability verifier (it may still pend for risk approval)."""
        r = client.post('/tools/call', json={
            'tool': 'file.write',
            'args': {'path': '/tmp/test.txt', 'content': 'data'},
        })
        # Should NOT be 403 — may be 200 stubbed or pending_approval
        assert r.status_code == 200
        assert r.json().get('status') != 'capability_denied'


# ---------------------------------------------------------------------------
# 5. Provider key lifecycle: store → status → rotate → expiry
# ---------------------------------------------------------------------------

class TestProviderKeyLifecycle:
    def test_store_key_returns_expires_at(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        r = client.post(
            '/admin/providers/openai/key',
            json={'key': 'sk-e2e-test', 'ttl_days': 30},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'stored'
        assert body['expires_at'] is not None
        assert body['expires_at'] > time.time()  # in the future

    def test_key_status_shows_configured(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        client.post('/admin/providers/openai/key',
                    json={'key': 'sk-status-test', 'ttl_days': 45}, headers=headers)

        r = client.get('/admin/providers/openai/key/status', headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body['configured'] is True
        assert body['is_expired'] is False
        assert body['days_until_expiry'] > 0

    def test_rotate_key_updates_last_rotated(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        # Store initial
        client.post('/admin/providers/anthropic/key',
                    json={'key': 'ant-old', 'ttl_days': 30}, headers=headers)

        # Rotate
        r = client.post('/admin/providers/anthropic/key/rotate',
                        json={'key': 'ant-new', 'ttl_days': 60}, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'rotated'
        assert body['last_rotated'] is not None

    def test_expiry_endpoint_returns_metadata(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        client.post('/admin/providers/openrouter/key',
                    json={'key': 'or-e2e', 'ttl_days': 10}, headers=headers)

        r = client.get('/admin/providers/openrouter/key/expiry', headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert 'set_at' in body
        assert 'expires_at' in body
        assert body['is_expired'] is False
        assert 8 <= body['days_until_expiry'] <= 11

    def test_expiring_endpoint_lists_soon_keys(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        # Store with long TTL — should not appear in 7-day window
        client.post('/admin/providers/openai/key',
                    json={'key': 'sk-long', 'ttl_days': 90}, headers=headers)

        r = client.get('/admin/providers/expiring?within_days=7', headers=headers)
        assert r.status_code == 200
        assert 'expiring' in r.json()
        # openai should NOT appear (expires in 90 days)
        providers = [m['provider'] for m in r.json()['expiring']]
        assert 'openai' not in providers


# ---------------------------------------------------------------------------
# 6. Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_log_accessible_to_admin(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        r = client.get('/admin/audit', headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200
        body = r.json()
        assert 'entries' in body

    def test_key_operations_appear_in_audit(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        headers = {'Authorization': f'Bearer {token}'}

        client.post('/admin/providers/openai/key',
                    json={'key': 'sk-audit-test'}, headers=headers)

        r = client.get('/admin/audit', headers=headers)
        assert r.status_code == 200
        actions = [e.get('event') for e in r.json().get('entries', [])]
        assert 'set_provider_key' in actions


# ---------------------------------------------------------------------------
# 7. Consent timeline
# ---------------------------------------------------------------------------

class TestConsentTimeline:
    def test_timeline_requires_auth(self, client):
        r = client.get('/consent/timeline')
        assert r.status_code == 401

    def test_timeline_returns_list(self, client, admin_credentials):
        username, password = admin_credentials
        token = _login(client, username, password)
        r = client.get('/consent/timeline',
                       headers={'Authorization': f'Bearer {token}'})
        assert r.status_code == 200

    def test_clear_timeline_requires_auth(self, client):
        r = client.delete('/consent/timeline')
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# 8. Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_repeated_calls_do_not_immediately_429(self, client):
        """A handful of identical calls should not trigger the rate limiter."""
        for _ in range(5):
            r = client.post('/tools/call',
                            json={'tool': 'noop', 'args': {}})
            assert r.status_code in (200, 202), f'Unexpected status {r.status_code}'
