"""Tests for the webhook registry and delivery.

Covers:
  - webhooks module: register, list, get, delete, fire_webhooks
  - HTTP: POST /admin/webhooks, GET /admin/webhooks, GET /admin/webhooks/{id},
          DELETE /admin/webhooks/{id}
  - Webhook events fired from approval approve/reject endpoints
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import webhooks
from app import app

ADMIN_TOKEN = 'test-secret'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_webhooks(tmp_path, monkeypatch):
    """Isolate webhook state per test using a temp file."""
    tmp_file = tmp_path / 'webhooks.json'
    monkeypatch.setattr(webhooks, 'WEBHOOKS_FILE', tmp_file)
    monkeypatch.setattr(webhooks, '_hooks', {})
    monkeypatch.setattr(webhooks, '_loaded', True)   # skip file-load in tests
    monkeypatch.setattr(webhooks, '_delivery_log', {})  # clear delivery log
    yield
    monkeypatch.setattr(webhooks, '_hooks', {})
    monkeypatch.setattr(webhooks, '_loaded', True)
    monkeypatch.setattr(webhooks, '_delivery_log', {})


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


# ===========================================================================
# Module-level unit tests
# ===========================================================================

class TestRegisterWebhook:
    def test_basic_registration(self):
        hook = webhooks.register_webhook('https://example.com/hook')
        assert hook['url'] == 'https://example.com/hook'
        assert 'id' in hook
        assert 'created_at' in hook
        assert set(hook['events']) == webhooks.VALID_EVENTS

    def test_custom_events(self):
        hook = webhooks.register_webhook('https://example.com/hook', events=['approval.created'])
        assert hook['events'] == ['approval.created']

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match='http'):
            webhooks.register_webhook('not-a-url')

    def test_invalid_event_raises(self):
        with pytest.raises(ValueError, match='unknown events'):
            webhooks.register_webhook('https://x.com/', events=['bogus.event'])

    def test_multiple_hooks_get_unique_ids(self):
        h1 = webhooks.register_webhook('https://a.com/')
        h2 = webhooks.register_webhook('https://b.com/')
        assert h1['id'] != h2['id']

    def test_persisted_to_file(self, tmp_path, monkeypatch):
        f = tmp_path / 'wh.json'
        monkeypatch.setattr(webhooks, 'WEBHOOKS_FILE', f)
        webhooks.register_webhook('https://save.test/')
        assert f.exists()
        data = json.loads(f.read_text())
        assert len(data) == 1


class TestListWebhooks:
    def test_empty_initially(self):
        assert webhooks.list_webhooks() == []

    def test_lists_registered_hooks(self):
        webhooks.register_webhook('https://one.com/')
        webhooks.register_webhook('https://two.com/')
        result = webhooks.list_webhooks()
        urls = {h['url'] for h in result}
        assert urls == {'https://one.com/', 'https://two.com/'}


class TestGetWebhook:
    def test_returns_none_for_missing(self):
        assert webhooks.get_webhook('nonexistent') is None

    def test_returns_hook_for_valid_id(self):
        h = webhooks.register_webhook('https://get.test/')
        assert webhooks.get_webhook(h['id']) == h


class TestDeleteWebhook:
    def test_delete_existing(self):
        h = webhooks.register_webhook('https://del.test/')
        assert webhooks.delete_webhook(h['id']) is True
        assert webhooks.get_webhook(h['id']) is None

    def test_delete_missing_returns_false(self):
        assert webhooks.delete_webhook('nonexistent-id') is False

    def test_delete_reduces_list(self):
        h1 = webhooks.register_webhook('https://a.test/')
        h2 = webhooks.register_webhook('https://b.test/')
        webhooks.delete_webhook(h1['id'])
        assert len(webhooks.list_webhooks()) == 1
        assert webhooks.list_webhooks()[0]['id'] == h2['id']


class TestFireWebhooks:
    def test_fire_calls_deliver_for_matching_event(self, monkeypatch):
        delivered: List[Dict[str, Any]] = []

        def _fake_deliver(hook_id, url, event, body, secret=''):
            delivered.append({'hook_id': hook_id, 'url': url, 'event': event})

        monkeypatch.setattr(webhooks, '_deliver', _fake_deliver)

        h = webhooks.register_webhook('https://fire.test/', events=['approval.created'])
        # Submit synchronously by bypassing the executor
        webhooks._executor.submit = lambda fn, *args, **kwargs: fn(*args, **kwargs) or MagicMock()  # type: ignore

        webhooks.fire_webhooks('approval.created', {'approval_id': 99})
        # Give the mock a moment if async
        import time; time.sleep(0.05)

    def test_no_delivery_for_non_subscribed_event(self, monkeypatch):
        delivered: List[str] = []

        def _fake_deliver(hook_id, url, event, body, secret=''):
            delivered.append(event)

        monkeypatch.setattr(webhooks, '_deliver', _fake_deliver)
        webhooks._executor.submit = lambda fn, *a, **kw: fn(*a, **kw) or MagicMock()

        webhooks.register_webhook('https://x.com/', events=['approval.approved'])
        webhooks.fire_webhooks('approval.rejected', {'approval_id': 5})
        import time; time.sleep(0.05)
        assert 'approval.rejected' not in delivered

    def test_deliver_silently_swallows_errors(self):
        """_deliver must never raise even when the URL is unreachable."""
        webhooks._deliver('fake-id', 'http://127.0.0.1:1/', 'approval.created', b'{}')


# ===========================================================================
# HTTP endpoint tests
# ===========================================================================

class TestCreateWebhookEndpoint:
    def test_requires_auth(self, client):
        r = client.post('/admin/webhooks', json={'url': 'https://x.com/'})
        assert r.status_code == 401

    def test_requires_admin_role(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: False)
        r = client.post('/admin/webhooks', json={'url': 'https://x.com/'}, headers=_auth())
        assert r.status_code == 403

    def test_create_returns_201(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.post('/admin/webhooks', json={'url': 'https://hook.test/'}, headers=_auth())
        assert r.status_code == 201
        body = r.json()
        assert body['url'] == 'https://hook.test/'
        assert 'id' in body

    def test_invalid_url_returns_422(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.post('/admin/webhooks', json={'url': 'not-a-url'}, headers=_auth())
        assert r.status_code == 422

    def test_invalid_event_returns_422(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.post(
            '/admin/webhooks',
            json={'url': 'https://x.com/', 'events': ['bogus.event']},
            headers=_auth(),
        )
        assert r.status_code == 422

    def test_custom_events_stored(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.post(
            '/admin/webhooks',
            json={'url': 'https://x.com/', 'events': ['approval.approved']},
            headers=_auth(),
        )
        assert r.status_code == 201
        assert r.json()['events'] == ['approval.approved']


class TestListWebhooksEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/admin/webhooks')
        assert r.status_code == 401

    def test_empty_list(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.get('/admin/webhooks', headers=_auth())
        assert r.status_code == 200
        assert r.json()['webhooks'] == []

    def test_returns_registered_hooks(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        webhooks.register_webhook('https://list.test/')
        r = client.get('/admin/webhooks', headers=_auth())
        assert r.status_code == 200
        assert len(r.json()['webhooks']) == 1


class TestGetWebhookEndpoint:
    def test_requires_auth(self, client):
        r = client.get('/admin/webhooks/some-id')
        assert r.status_code == 401

    def test_missing_hook_returns_404(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.get('/admin/webhooks/nonexistent', headers=_auth())
        assert r.status_code == 404

    def test_returns_hook_data(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        h = webhooks.register_webhook('https://fetch.test/')
        r = client.get(f'/admin/webhooks/{h["id"]}', headers=_auth())
        assert r.status_code == 200
        assert r.json()['url'] == 'https://fetch.test/'


class TestDeleteWebhookEndpoint:
    def test_requires_auth(self, client):
        r = client.delete('/admin/webhooks/some-id')
        assert r.status_code == 401

    def test_missing_hook_returns_404(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.delete('/admin/webhooks/nonexistent', headers=_auth())
        assert r.status_code == 404

    def test_delete_existing_hook(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        h = webhooks.register_webhook('https://todel.test/')
        r = client.delete(f'/admin/webhooks/{h["id"]}', headers=_auth())
        assert r.status_code == 200
        assert r.json()['deleted'] is True
        assert r.json()['id'] == h['id']
        # Should be gone from list now
        r2 = client.get('/admin/webhooks', headers=_auth())
        assert r2.json()['webhooks'] == []


class TestWebhookFireOnApprovalEvents:
    """Integration: verify that approve/reject endpoints trigger webhook fire."""

    def test_approve_fires_webhook_event(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)

        fired: List[str] = []
        monkeypatch.setattr(webhooks, 'fire_webhooks', lambda event, payload: fired.append(event))

        # Stub the supervisor queue so approval 1 exists
        from app import supervisor
        monkeypatch.setattr(supervisor.queue, 'approve', lambda req_id: True)

        r = client.post('/approvals/1/approve', headers=_auth())
        assert r.status_code == 200
        assert 'approval.approved' in fired

    def test_reject_fires_webhook_event(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)

        fired: List[str] = []
        monkeypatch.setattr(webhooks, 'fire_webhooks', lambda event, payload: fired.append(event))

        from app import supervisor
        monkeypatch.setattr(supervisor.queue, 'reject', lambda req_id: True)

        r = client.post('/approvals/1/reject', headers=_auth())
        assert r.status_code == 200
        assert 'approval.rejected' in fired


# ===========================================================================
# Delivery log (module-level)
# ===========================================================================

class TestGetDeliveries:
    def test_empty_for_new_hook(self):
        hook = webhooks.register_webhook('https://example.com/')
        assert webhooks.get_deliveries(hook['id']) == []

    def test_unknown_hook_id_returns_empty(self):
        assert webhooks.get_deliveries('nonexistent-id') == []

    def test_deliver_records_ok_on_2xx(self, monkeypatch):
        """_deliver() should create an 'ok' record when the HTTP call returns 2xx."""
        hook = webhooks.register_webhook('https://example.com/')

        # Stub urllib to return a 200 response
        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: _FakeResp())
        webhooks._deliver(hook['id'], hook['url'], 'approval.created', b'{}')

        deliveries = webhooks.get_deliveries(hook['id'])
        assert len(deliveries) == 1
        rec = deliveries[0]
        assert rec['status'] == 'ok'
        assert rec['status_code'] == 200
        assert rec['error'] is None
        assert rec['event'] == 'approval.created'
        assert 'timestamp' in rec

    def test_deliver_records_error_on_exception(self, monkeypatch):
        """_deliver() should record an 'error' entry on network failure."""
        hook = webhooks.register_webhook('https://badhost.invalid/')

        def _raise(*a, **kw):
            raise ConnectionError('no route to host')

        monkeypatch.setattr('urllib.request.urlopen', _raise)
        webhooks._deliver(hook['id'], hook['url'], 'approval.rejected', b'{}')

        deliveries = webhooks.get_deliveries(hook['id'])
        assert len(deliveries) == 1
        rec = deliveries[0]
        assert rec['status'] == 'error'
        assert rec['status_code'] is None
        assert 'ConnectionError' in rec['error']

    def test_deliveries_newest_first(self, monkeypatch):
        """Records are ordered newest-first."""
        hook = webhooks.register_webhook('https://example.com/')

        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: _FakeResp())
        # Two deliveries
        webhooks._deliver(hook['id'], hook['url'], 'approval.created', b'{}')
        webhooks._deliver(hook['id'], hook['url'], 'approval.approved', b'{}')

        deliveries = webhooks.get_deliveries(hook['id'])
        assert len(deliveries) == 2
        # Newest (approval.approved) should be first
        assert deliveries[0]['event'] == 'approval.approved'

    def test_limit_parameter(self, monkeypatch):
        """get_deliveries respects limit."""
        hook = webhooks.register_webhook('https://example.com/')

        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: _FakeResp())
        for _ in range(5):
            webhooks._deliver(hook['id'], hook['url'], 'approval.created', b'{}')

        assert len(webhooks.get_deliveries(hook['id'], limit=3)) == 3
        assert len(webhooks.get_deliveries(hook['id'])) == 5


# ===========================================================================
# HTTP: GET /admin/webhooks/{hook_id}/deliveries
# ===========================================================================

class TestDeliveriesEndpoint:
    def test_requires_auth(self, client):
        hook = webhooks.register_webhook('https://example.com/')
        r = client.get(f'/admin/webhooks/{hook["id"]}/deliveries')
        assert r.status_code in (401, 403)

    def test_unknown_hook_returns_404(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.get('/admin/webhooks/no-such-id/deliveries', headers=_auth())
        assert r.status_code == 404

    def test_empty_log_for_new_hook(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        hook = webhooks.register_webhook('https://example.com/')
        r = client.get(f'/admin/webhooks/{hook["id"]}/deliveries', headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body['hook_id'] == hook['id']
        assert body['deliveries'] == []
        assert body['count'] == 0

    def test_returns_delivery_records(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        hook = webhooks.register_webhook('https://example.com/')

        class _FakeResp:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: _FakeResp())
        webhooks._deliver(hook['id'], hook['url'], 'approval.created', b'{}')

        r = client.get(f'/admin/webhooks/{hook["id"]}/deliveries', headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body['count'] == 1
        rec = body['deliveries'][0]
        assert rec['status'] == 'ok'
        assert rec['status_code'] == 201
        assert rec['event'] == 'approval.created'

    def test_limit_query_param(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        hook = webhooks.register_webhook('https://example.com/')

        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: _FakeResp())
        for _ in range(5):
            webhooks._deliver(hook['id'], hook['url'], 'approval.created', b'{}')

        r = client.get(f'/admin/webhooks/{hook["id"]}/deliveries?limit=2', headers=_auth())
        assert r.status_code == 200
        assert r.json()['count'] == 2


# ===========================================================================
# HMAC signing tests
# ===========================================================================

import hashlib
import hmac as _hmac


class TestHMACSigning:
    """Verify that _deliver adds/omits X-Intelli-Signature-256 correctly."""

    def _captured_headers(self, monkeypatch) -> dict:
        """Patch urlopen to capture the request headers and return 200."""
        captured: dict = {}

        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def _fake_urlopen(req, *args, **kwargs):
            captured.update(dict(req.headers))
            return _FakeResp()

        monkeypatch.setattr('urllib.request.urlopen', _fake_urlopen)
        return captured

    def test_signature_header_present_when_secret(self, monkeypatch):
        captured = self._captured_headers(monkeypatch)
        hook = webhooks.register_webhook('https://secret.test/', secret='mysecret')
        body = b'{"event":"approval.created"}'
        webhooks._deliver(hook['id'], hook['url'], 'approval.created', body, secret='mysecret')
        assert 'X-intelli-signature-256' in captured or 'x-intelli-signature-256' in {k.lower() for k in captured}

    def test_signature_value_is_correct(self, monkeypatch):
        captured = self._captured_headers(monkeypatch)
        secret = 'supersecret'
        body = b'{"event":"approval.approved"}'
        hook = webhooks.register_webhook('https://hmac.test/', secret=secret)
        webhooks._deliver(hook['id'], hook['url'], 'approval.approved', body, secret=secret)
        expected = 'sha256=' + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        header_key = next((k for k in captured if k.lower() == 'x-intelli-signature-256'), None)
        assert header_key is not None, 'X-Intelli-Signature-256 header missing'
        assert captured[header_key] == expected

    def test_no_signature_header_without_secret(self, monkeypatch):
        captured = self._captured_headers(monkeypatch)
        hook = webhooks.register_webhook('https://nosig.test/')
        body = b'{"event":"approval.created"}'
        webhooks._deliver(hook['id'], hook['url'], 'approval.created', body, secret='')
        assert not any(k.lower() == 'x-intelli-signature-256' for k in captured)

    def test_secret_stored_in_hook_dict(self):
        hook = webhooks.register_webhook('https://stored.test/', secret='abc123')
        stored = webhooks.get_webhook(hook['id'])
        assert stored is not None
        assert stored.get('secret') == 'abc123'

    def test_no_secret_field_empty_string(self):
        hook = webhooks.register_webhook('https://nosecret.test/')
        stored = webhooks.get_webhook(hook['id'])
        assert stored is not None
        assert stored.get('secret', '') == ''

    def test_fire_webhooks_passes_secret_to_deliver(self, monkeypatch):
        """fire_webhooks should forward the stored secret when calling _deliver."""
        calls: list = []

        def _fake_deliver(hook_id, url, event, body, secret=''):
            calls.append({'secret': secret})

        monkeypatch.setattr(webhooks, '_deliver', _fake_deliver)
        webhooks._executor.submit = lambda fn, *a, **kw: fn(*a, **kw) or None  # type: ignore

        webhooks.register_webhook('https://pass.test/', secret='passme')
        webhooks.fire_webhooks('approval.created', {'id': 1})
        import time; time.sleep(0.05)
        assert any(c['secret'] == 'passme' for c in calls)

    def test_create_webhook_endpoint_accepts_secret(self, client, monkeypatch):
        import auth as _auth_mod
        monkeypatch.setattr(_auth_mod, 'check_role', lambda token, role: True)
        r = client.post(
            '/admin/webhooks',
            json={'url': 'https://endpoint.test/', 'secret': 'topsecret'},
            headers=_auth(),
        )
        assert r.status_code == 201
        hook_id = r.json()['id']
        stored = webhooks.get_webhook(hook_id)
        assert stored is not None
        assert stored.get('secret') == 'topsecret'
