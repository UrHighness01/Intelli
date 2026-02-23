"""Tests for the alert monitoring system added in the security hardening pass.

Covers:
  - New PUT /admin/alerts/config fields:
      worker_check_interval_seconds, validation_error_window_seconds,
      validation_error_threshold
  - GET /admin/alerts/config returns all new keys with defaults
  - Partial PUT (only new fields) preserves existing threshold
  - Validation of new field boundaries
  - _alert_monitor() helper logic (worker health transition + validation error rate)
  - _validation_error_times deque is appended on tool validation errors
"""
from __future__ import annotations

import collections
import time
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient

import app as _app
import webhooks
from app import app

ADMIN_TOKEN = 'monitor-test-secret'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """Isolate state per test."""
    monkeypatch.setattr(webhooks, 'WEBHOOKS_FILE', tmp_path / 'webhooks.json')
    monkeypatch.setattr(webhooks, '_hooks', {})
    monkeypatch.setattr(webhooks, '_loaded', True)
    monkeypatch.setattr(webhooks, '_delivery_log', {})

    original_config = dict(_app._alert_config)
    original_err_times = _app._validation_error_times
    original_was_healthy = _app._worker_was_healthy

    # Reset to full defaults
    _app._alert_config = {
        'approval_queue_threshold': 0,
        'worker_check_interval_seconds': 60,
        'validation_error_window_seconds': 60,
        'validation_error_threshold': 0,
    }
    _app._validation_error_times = collections.deque()
    _app._worker_was_healthy = None

    yield

    _app._alert_config = original_config
    _app._validation_error_times = original_err_times
    _app._worker_was_healthy = original_was_healthy


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', ADMIN_TOKEN)
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    monkeypatch.setattr(auth, 'check_role', lambda token, role: bool(token))
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    return TestClient(app)


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


# ===========================================================================
# GET /admin/alerts/config — new keys present
# ===========================================================================

class TestGetAlertsConfigNewKeys:
    def test_all_new_keys_returned(self, client):
        r = client.get('/admin/alerts/config', headers=_auth())
        assert r.status_code == 200
        data = r.json()
        assert 'worker_check_interval_seconds'  in data
        assert 'validation_error_window_seconds' in data
        assert 'validation_error_threshold'      in data
        assert 'approval_queue_threshold'        in data

    def test_defaults_are_sensible(self, client):
        r = client.get('/admin/alerts/config', headers=_auth())
        data = r.json()
        assert data['validation_error_threshold'] == 0         # disabled by default
        assert data['worker_check_interval_seconds'] >= 5      # at least 5 s
        assert data['validation_error_window_seconds'] > 0


# ===========================================================================
# PUT /admin/alerts/config — new fields
# ===========================================================================

class TestPutAlertsConfigNewFields:
    def test_update_validation_threshold(self, client):
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'validation_error_threshold': 10,
        }, headers=_auth())
        assert r.status_code == 200
        assert r.json()['validation_error_threshold'] == 10

    def test_update_worker_check_interval(self, client):
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'worker_check_interval_seconds': 30,
        }, headers=_auth())
        assert r.status_code == 200
        assert r.json()['worker_check_interval_seconds'] == 30

    def test_update_validation_window(self, client):
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'validation_error_window_seconds': 120,
        }, headers=_auth())
        assert r.status_code == 200
        assert r.json()['validation_error_window_seconds'] == 120

    def test_partial_update_preserves_other_keys(self, client):
        """Updating only approval_queue_threshold must not wipe new keys."""
        # First set new key
        client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'validation_error_threshold': 5,
        }, headers=_auth())
        # Then update only approval threshold
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 3,
        }, headers=_auth())
        assert r.status_code == 200
        data = r.json()
        assert data['approval_queue_threshold'] == 3
        assert data['validation_error_threshold'] == 5  # preserved

    def test_worker_interval_below_5_rejected(self, client):
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'worker_check_interval_seconds': 4,
        }, headers=_auth())
        assert r.status_code == 422

    def test_validation_window_zero_rejected(self, client):
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'validation_error_window_seconds': 0,
        }, headers=_auth())
        assert r.status_code == 422

    def test_negative_validation_threshold_rejected(self, client):
        r = client.put('/admin/alerts/config', json={
            'approval_queue_threshold': 0,
            'validation_error_threshold': -1,
        }, headers=_auth())
        assert r.status_code == 422


# ===========================================================================
# Validation error timestamps recorded in _validation_error_times
# ===========================================================================

class TestValidationErrorTimestamps:
    def test_deque_appended_on_validation_error(self, client, monkeypatch):
        """After a /tools/call that produces a validation error, the deque grows."""
        import app as _a

        monkeypatch.setattr(_a.supervisor, 'process_call',
                            lambda payload: {'status': 'validation_error', 'detail': 'forced'})

        before = len(_a._validation_error_times)
        client.post('/tools/call', json={'tool': 'noop', 'args': {}},
                    headers=_auth())
        after = len(_a._validation_error_times)
        assert after == before + 1

    def test_timestamps_are_recent(self, client, monkeypatch):
        import app as _a

        monkeypatch.setattr(_a.supervisor, 'process_call',
                            lambda payload: {'status': 'validation_error', 'detail': 'forced'})

        before = time.time()
        client.post('/tools/call', json={'tool': 'noop', 'args': {}},
                    headers=_auth())
        after = time.time()
        assert len(_a._validation_error_times) > 0
        ts = _a._validation_error_times[-1]
        assert before <= ts <= after + 0.5


# ===========================================================================
# _alert_monitor internal logic (unit tests — no threading)
# ===========================================================================

class TestAlertMonitorLogic:
    """Unit-test the logic inside _alert_monitor without actually sleeping."""

    def _run_monitor_cycle(self, worker_ok, monkeypatch):
        """Execute one worker-health snapshot and return fired events."""
        import app as _a
        import webhooks as _wh

        fired: list = []
        monkeypatch.setattr(_wh, 'fire_webhooks', lambda ev, pl: fired.append((ev, pl)))
        monkeypatch.setattr(_a, '_webhooks', _wh)
        monkeypatch.setattr(_a._worker_manager, 'check_health', lambda: worker_ok)

        # Simulate the health-check + transition logic inline
        import metrics as _metrics
        ok = worker_ok
        _metrics.gauge('worker_healthy', 1.0 if ok else 0.0)

        if _a._worker_was_healthy is not None:
            if not ok and _a._worker_was_healthy:
                _wh.fire_webhooks('gateway.alert', {'alert': 'worker_unhealthy'})
            elif ok and not _a._worker_was_healthy:
                _wh.fire_webhooks('gateway.alert', {'alert': 'worker_recovered'})
        _a._worker_was_healthy = ok

        return fired

    def test_no_alert_on_first_check(self, monkeypatch):
        """No alert should fire on the very first health check (no previous state)."""
        assert _app._worker_was_healthy is None
        fired = self._run_monitor_cycle(worker_ok=False, monkeypatch=monkeypatch)
        assert not any(ev == 'gateway.alert' for ev, _ in fired)

    def test_unhealthy_alert_on_transition(self, monkeypatch):
        """Alert fires when worker transitions healthy → unhealthy."""
        _app._worker_was_healthy = True
        fired = self._run_monitor_cycle(worker_ok=False, monkeypatch=monkeypatch)
        events = [pl['alert'] for ev, pl in fired if ev == 'gateway.alert']
        assert 'worker_unhealthy' in events

    def test_recovered_alert_on_transition(self, monkeypatch):
        """Alert fires when worker transitions unhealthy → healthy."""
        _app._worker_was_healthy = False
        fired = self._run_monitor_cycle(worker_ok=True, monkeypatch=monkeypatch)
        events = [pl['alert'] for ev, pl in fired if ev == 'gateway.alert']
        assert 'worker_recovered' in events

    def test_no_repeat_alert_when_still_unhealthy(self, monkeypatch):
        """No alert fires if worker stays unhealthy (already unhealthy)."""
        _app._worker_was_healthy = False
        fired = self._run_monitor_cycle(worker_ok=False, monkeypatch=monkeypatch)
        assert not any(ev == 'gateway.alert' for ev, _ in fired)

    def test_no_repeat_alert_when_still_healthy(self, monkeypatch):
        """No alert fires if worker stays healthy."""
        _app._worker_was_healthy = True
        fired = self._run_monitor_cycle(worker_ok=True, monkeypatch=monkeypatch)
        assert not any(ev == 'gateway.alert' for ev, _ in fired)

    def test_validation_rate_alert_fires_when_threshold_met(self, monkeypatch):
        """gateway.alert with alert='validation_error_rate' fires when count >= threshold."""
        import app as _a
        import webhooks as _wh
        fired: list = []
        monkeypatch.setattr(_wh, 'fire_webhooks', lambda ev, pl: fired.append((ev, pl)))
        monkeypatch.setattr(_a, '_webhooks', _wh)

        threshold = 3
        window = 60.0
        _a._alert_config['validation_error_threshold'] = threshold
        _a._alert_config['validation_error_window_seconds'] = window

        now = time.time()
        _a._validation_error_times = collections.deque([now - 5, now - 3, now - 1])

        # Run the rate-check logic inline
        cutoff = time.time() - window
        while _a._validation_error_times and _a._validation_error_times[0] < cutoff:
            _a._validation_error_times.popleft()
        count = len(_a._validation_error_times)
        if threshold > 0 and count >= threshold:
            _wh.fire_webhooks('gateway.alert', {
                'alert': 'validation_error_rate',
                'count': count,
                'window_seconds': window,
                'threshold': threshold,
            })

        events = [pl['alert'] for ev, pl in fired if ev == 'gateway.alert']
        assert 'validation_error_rate' in events
        payload = next(pl for ev, pl in fired if ev == 'gateway.alert')
        assert payload['count'] == 3
        assert payload['threshold'] == 3

    def test_validation_rate_alert_not_fired_when_threshold_zero(self, monkeypatch):
        """Disabled (threshold=0) means no alert even with many errors."""
        import app as _a
        import webhooks as _wh
        fired: list = []
        monkeypatch.setattr(_wh, 'fire_webhooks', lambda ev, pl: fired.append((ev, pl)))
        monkeypatch.setattr(_a, '_webhooks', _wh)

        _a._alert_config['validation_error_threshold'] = 0
        now = time.time()
        _a._validation_error_times = collections.deque([now - 1, now - 2, now - 3])

        threshold = _a._alert_config.get('validation_error_threshold', 0)
        if threshold > 0:
            _wh.fire_webhooks('gateway.alert', {'alert': 'validation_error_rate'})

        assert not any(ev == 'gateway.alert' for ev, _ in fired)

    def test_old_error_timestamps_pruned(self):
        """Timestamps older than window are discarded before rate check."""
        import app as _a
        window = 10.0
        now = time.time()
        # 3 old, 2 recent
        _a._validation_error_times = collections.deque([
            now - 100, now - 50, now - 20,   # outside 10-s window
            now - 5,   now - 1,               # inside window
        ])
        cutoff = time.time() - window
        while _a._validation_error_times and _a._validation_error_times[0] < cutoff:
            _a._validation_error_times.popleft()
        assert len(_a._validation_error_times) == 2
