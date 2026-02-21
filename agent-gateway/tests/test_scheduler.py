"""Tests for scheduler.py module and /admin/schedule* HTTP endpoints.

Covers:
  Module API (unit tests):
    add_task()       — validation, shape, persistence
    list_tasks()     — empty and populated
    get_task()       — found / missing
    delete_task()    — found / missing
    set_enabled()    — toggle / missing
    update_task()    — partial update, invalid fields, missing

  HTTP endpoints (admin Bearer auth required):
    GET    /admin/schedule
    POST   /admin/schedule
    GET    /admin/schedule/{task_id}
    PATCH  /admin/schedule/{task_id}
    DELETE /admin/schedule/{task_id}
"""
from __future__ import annotations

import importlib
import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _reset_scheduler(monkeypatch, tmp_path):
    """Import (or reload) scheduler with isolated state using tmp_path for JSON."""
    import scheduler
    monkeypatch.setattr(scheduler, 'SCHEDULE_PATH', tmp_path / 'schedule.json')
    monkeypatch.setattr(scheduler, '_tasks', {})
    monkeypatch.setattr(scheduler, '_loaded', True)   # skip disk load
    return scheduler


@pytest.fixture()
def sched(tmp_path, monkeypatch):
    """Return an isolated scheduler module."""
    return _reset_scheduler(monkeypatch, tmp_path)


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """Return (TestClient, admin_token) + isolated auth + scheduler."""
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'adminpass-sched')

    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    _reset_scheduler(monkeypatch, tmp_path)

    from app import app
    client = TestClient(app)

    r = client.post('/admin/login', json={'username': 'admin', 'password': 'adminpass-sched'})
    assert r.status_code == 200, r.text
    return client, r.json()['token']


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


# ===========================================================================
# add_task() — unit tests
# ===========================================================================

class TestAddTask:
    def test_returns_task_dict(self, sched):
        t = sched.add_task('ping', 'echo', {'msg': 'hi'}, 60)
        assert isinstance(t, dict)
        assert t['name'] == 'ping'
        assert t['tool'] == 'echo'
        assert t['args'] == {'msg': 'hi'}
        assert t['interval_seconds'] == 60
        assert t['enabled'] is True

    def test_generates_unique_ids(self, sched):
        t1 = sched.add_task('a', 'echo', {}, 30)
        t2 = sched.add_task('b', 'echo', {}, 30)
        assert t1['id'] != t2['id']

    def test_next_run_at_is_iso_string(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        nra = t['next_run_at']
        assert isinstance(nra, str) and 'T' in nra  # ISO-8601

    def test_run_count_starts_at_zero(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        assert t['run_count'] == 0

    def test_disabled_on_request(self, sched):
        t = sched.add_task('x', 'tool', {}, 10, enabled=False)
        assert t['enabled'] is False

    def test_raises_on_empty_name(self, sched):
        with pytest.raises(ValueError, match='name'):
            sched.add_task('', 'tool', {}, 10)

    def test_raises_on_whitespace_name(self, sched):
        with pytest.raises(ValueError, match='name'):
            sched.add_task('   ', 'tool', {}, 10)

    def test_raises_on_zero_interval(self, sched):
        with pytest.raises(ValueError, match='interval_seconds'):
            sched.add_task('x', 'tool', {}, 0)

    def test_raises_on_negative_interval(self, sched):
        with pytest.raises(ValueError, match='interval_seconds'):
            sched.add_task('x', 'tool', {}, -5)

    def test_raises_on_empty_tool(self, sched):
        with pytest.raises(ValueError, match='tool'):
            sched.add_task('x', '', {}, 10)

    def test_persists_to_json(self, sched, tmp_path):
        sched.add_task('save-me', 'tool', {}, 15)
        assert (tmp_path / 'schedule.json').exists()


# ===========================================================================
# list_tasks() — unit tests
# ===========================================================================

class TestListTasks:
    def test_empty_by_default(self, sched):
        assert sched.list_tasks() == []

    def test_returns_all_added(self, sched):
        sched.add_task('a', 'tool', {}, 10)
        sched.add_task('b', 'tool', {}, 20)
        results = sched.list_tasks()
        assert len(results) == 2

    def test_items_have_no_raw_timestamp(self, sched):
        sched.add_task('x', 'tool', {}, 5)
        for t in sched.list_tasks():
            # next_run_at must be ISO string, not a float
            assert isinstance(t['next_run_at'], str)


# ===========================================================================
# get_task() — unit tests
# ===========================================================================

class TestGetTask:
    def test_returns_none_for_missing(self, sched):
        assert sched.get_task('does-not-exist') is None

    def test_returns_task_for_existing(self, sched):
        t = sched.add_task('x', 'tool', {}, 5)
        found = sched.get_task(t['id'])
        assert found is not None
        assert found['id'] == t['id']

    def test_next_run_at_iso_in_get(self, sched):
        t = sched.add_task('x', 'tool', {}, 5)
        found = sched.get_task(t['id'])
        assert isinstance(found['next_run_at'], str)


# ===========================================================================
# delete_task() — unit tests
# ===========================================================================

class TestDeleteTask:
    def test_deletes_existing(self, sched):
        t = sched.add_task('x', 'tool', {}, 5)
        assert sched.delete_task(t['id']) is True
        assert sched.get_task(t['id']) is None

    def test_returns_false_for_missing(self, sched):
        assert sched.delete_task('ghost-id') is False

    def test_list_shrinks_after_delete(self, sched):
        t = sched.add_task('x', 'tool', {}, 5)
        sched.delete_task(t['id'])
        assert sched.list_tasks() == []


# ===========================================================================
# set_enabled() — unit tests
# ===========================================================================

class TestSetEnabled:
    def test_disable_existing(self, sched):
        t = sched.add_task('x', 'tool', {}, 5)
        assert sched.set_enabled(t['id'], False) is True
        assert sched.get_task(t['id'])['enabled'] is False

    def test_re_enable(self, sched):
        t = sched.add_task('x', 'tool', {}, 5, enabled=False)
        sched.set_enabled(t['id'], True)
        assert sched.get_task(t['id'])['enabled'] is True

    def test_returns_false_for_missing(self, sched):
        assert sched.set_enabled('no-such-id', True) is False


# ===========================================================================
# update_task() — unit tests
# ===========================================================================

class TestUpdateTask:
    def test_update_name(self, sched):
        t = sched.add_task('old', 'tool', {}, 10)
        updated = sched.update_task(t['id'], name='new')
        assert updated['name'] == 'new'

    def test_update_interval(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        updated = sched.update_task(t['id'], interval_seconds=99)
        assert updated['interval_seconds'] == 99

    def test_update_args(self, sched):
        t = sched.add_task('x', 'tool', {'a': 1}, 10)
        updated = sched.update_task(t['id'], args={'b': 2})
        assert updated['args'] == {'b': 2}

    def test_update_enabled(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        updated = sched.update_task(t['id'], enabled=False)
        assert updated['enabled'] is False

    def test_partial_update_preserves_other_fields(self, sched):
        t = sched.add_task('x', 'tool', {'k': 'v'}, 10)
        updated = sched.update_task(t['id'], name='y')
        assert updated['args'] == {'k': 'v'}
        assert updated['interval_seconds'] == 10

    def test_returns_none_for_missing(self, sched):
        assert sched.update_task('ghost', name='x') is None

    def test_raises_on_unknown_field(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        with pytest.raises(ValueError, match='unknown fields'):
            sched.update_task(t['id'], bogus='value')

    def test_raises_on_invalid_interval(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        with pytest.raises(ValueError, match='interval_seconds'):
            sched.update_task(t['id'], interval_seconds=0)

    def test_raises_on_empty_name_update(self, sched):
        t = sched.add_task('x', 'tool', {}, 10)
        with pytest.raises(ValueError, match='name'):
            sched.update_task(t['id'], name='')


# ===========================================================================
# trigger_task() — unit tests
# ===========================================================================

class TestTriggerTask:
    def test_sets_next_run_at_to_past(self, sched):
        import time
        t = sched.add_task('x', 'tool', {}, 3600)
        assert sched.trigger_task(t['id']) is True
        # next_run_at in the raw dict should be <= now (in the past)
        raw = sched._tasks[t['id']]
        assert raw['next_run_at'] <= time.time()

    def test_returns_false_for_missing(self, sched):
        assert sched.trigger_task('ghost') is False

    def test_triggered_task_shows_in_view_as_past_date(self, sched):
        """After trigger the ISO next_run_at in the view is in the past."""
        from datetime import datetime, timezone
        t = sched.add_task('x', 'tool', {}, 3600)
        sched.trigger_task(t['id'])
        view = sched.get_task(t['id'])
        nra = datetime.fromisoformat(view['next_run_at'].replace('Z', '+00:00'))
        assert nra <= datetime.now(timezone.utc)


# ===========================================================================
# Scheduler metrics — unit tests
# ===========================================================================

class TestSchedulerMetrics:
    def test_runs_total_incremented_on_success(self, sched, monkeypatch):
        import metrics as m
        monkeypatch.setattr(m, '_counters', __import__('collections').defaultdict(lambda: __import__('collections').defaultdict(float)))
        sched.set_executor(lambda payload: {'ok': True})
        t_raw = dict(sched.add_task('m', 'echo', {}, 60))
        # manipulate the raw internal dict for _run_task
        raw = sched._tasks[t_raw['id']]
        sched._run_task(raw)
        val = m.get_counter('scheduler_runs_total', labels={'task': 'm'})
        assert val == 1.0

    def test_errors_total_incremented_on_exception(self, sched, monkeypatch):
        import metrics as m
        monkeypatch.setattr(m, '_counters', __import__('collections').defaultdict(lambda: __import__('collections').defaultdict(float)))
        def boom(payload): raise RuntimeError('oops')
        sched.set_executor(boom)
        t_raw = sched.add_task('e', 'fail', {}, 60)
        raw = sched._tasks[t_raw['id']]
        sched._run_task(raw)
        assert m.get_counter('scheduler_errors_total', labels={'task': 'e'}) == 1.0

    def test_tasks_total_gauge_updated_on_add(self, sched, monkeypatch):
        import metrics as m
        monkeypatch.setattr(m, '_gauges', __import__('collections').defaultdict(lambda: __import__('collections').defaultdict(float)))
        sched.add_task('g', 'echo', {}, 10)
        assert m.get_gauge('scheduler_tasks_total') >= 1.0

    def test_tasks_total_gauge_decrements_on_delete(self, sched, monkeypatch):
        import metrics as m
        monkeypatch.setattr(m, '_gauges', __import__('collections').defaultdict(lambda: __import__('collections').defaultdict(float)))
        t = sched.add_task('g', 'echo', {}, 10)
        sched.delete_task(t['id'])
        assert m.get_gauge('scheduler_tasks_total') == 0.0


# ===========================================================================
# HTTP — GET /admin/schedule
# ===========================================================================

class TestScheduleList:
    def test_returns_empty_list(self, setup):
        client, token = setup
        r = client.get('/admin/schedule', headers=_auth(token))
        assert r.status_code == 200
        assert r.json()['tasks'] == []

    def test_returns_tasks_after_create(self, setup):
        client, token = setup
        client.post('/admin/schedule', headers=_auth(token),
                    json={'name': 'x', 'tool': 'echo', 'args': {}, 'interval_seconds': 60})
        r = client.get('/admin/schedule', headers=_auth(token))
        assert r.status_code == 200
        assert len(r.json()['tasks']) == 1

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.get('/admin/schedule')
        assert r.status_code == 401


# ===========================================================================
# HTTP — POST /admin/schedule
# ===========================================================================

class TestScheduleCreate:
    def _payload(self, **kwargs):
        base = {'name': 'test-task', 'tool': 'echo', 'args': {}, 'interval_seconds': 30}
        base.update(kwargs)
        return base

    def test_creates_and_returns_201(self, setup):
        client, token = setup
        r = client.post('/admin/schedule', headers=_auth(token), json=self._payload())
        assert r.status_code == 201
        body = r.json()
        assert body['name'] == 'test-task'
        assert 'id' in body

    def test_next_run_at_is_string(self, setup):
        client, token = setup
        r = client.post('/admin/schedule', headers=_auth(token), json=self._payload())
        body = r.json()
        assert isinstance(body['next_run_at'], str)

    def test_rejects_zero_interval(self, setup):
        client, token = setup
        r = client.post('/admin/schedule', headers=_auth(token),
                        json=self._payload(interval_seconds=0))
        assert r.status_code == 422

    def test_rejects_empty_name(self, setup):
        client, token = setup
        r = client.post('/admin/schedule', headers=_auth(token),
                        json=self._payload(name=''))
        assert r.status_code == 422

    def test_rejects_empty_tool(self, setup):
        client, token = setup
        r = client.post('/admin/schedule', headers=_auth(token),
                        json=self._payload(tool=''))
        assert r.status_code == 422

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.post('/admin/schedule', json=self._payload())
        assert r.status_code == 401

    def test_created_task_appears_in_list(self, setup):
        client, token = setup
        client.post('/admin/schedule', headers=_auth(token), json=self._payload(name='listed'))
        tasks = client.get('/admin/schedule', headers=_auth(token)).json()['tasks']
        assert any(t['name'] == 'listed' for t in tasks)


# ===========================================================================
# HTTP — GET /admin/schedule/{task_id}
# ===========================================================================

class TestScheduleGet:
    def test_returns_task(self, setup):
        client, token = setup
        created = client.post('/admin/schedule', headers=_auth(token),
                              json={'name': 'g', 'tool': 'echo', 'args': {}, 'interval_seconds': 5}).json()
        r = client.get(f"/admin/schedule/{created['id']}", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()['id'] == created['id']

    def test_returns_404_for_missing(self, setup):
        client, token = setup
        r = client.get('/admin/schedule/no-such-id', headers=_auth(token))
        assert r.status_code == 404

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.get('/admin/schedule/anything')
        assert r.status_code == 401


# ===========================================================================
# HTTP — PATCH /admin/schedule/{task_id}
# ===========================================================================

class TestSchedulePatch:
    def _create(self, client, token, **overrides):
        payload = {'name': 'patchy', 'tool': 'echo', 'args': {}, 'interval_seconds': 20}
        payload.update(overrides)
        return client.post('/admin/schedule', headers=_auth(token), json=payload).json()

    def test_patch_name(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.patch(f"/admin/schedule/{t['id']}", headers=_auth(token),
                         json={'name': 'renamed'})
        assert r.status_code == 200
        assert r.json()['name'] == 'renamed'

    def test_patch_interval(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.patch(f"/admin/schedule/{t['id']}", headers=_auth(token),
                         json={'interval_seconds': 99})
        assert r.status_code == 200
        assert r.json()['interval_seconds'] == 99

    def test_patch_enabled_false(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.patch(f"/admin/schedule/{t['id']}", headers=_auth(token),
                         json={'enabled': False})
        assert r.status_code == 200
        assert r.json()['enabled'] is False

    def test_patch_returns_422_on_invalid_interval(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.patch(f"/admin/schedule/{t['id']}", headers=_auth(token),
                         json={'interval_seconds': -1})
        assert r.status_code == 422

    def test_patch_returns_404_for_missing(self, setup):
        client, token = setup
        r = client.patch('/admin/schedule/ghost-id', headers=_auth(token),
                         json={'name': 'x'})
        assert r.status_code == 404

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.patch('/admin/schedule/anything', json={'name': 'x'})
        assert r.status_code == 401


# ===========================================================================
# HTTP — DELETE /admin/schedule/{task_id}
# ===========================================================================

class TestScheduleDelete:
    def test_deletes_task(self, setup):
        client, token = setup
        t = client.post('/admin/schedule', headers=_auth(token),
                        json={'name': 'del', 'tool': 'echo', 'args': {}, 'interval_seconds': 5}).json()
        r = client.delete(f"/admin/schedule/{t['id']}", headers=_auth(token))
        assert r.status_code == 200
        # Confirm gone
        r2 = client.get(f"/admin/schedule/{t['id']}", headers=_auth(token))
        assert r2.status_code == 404

    def test_returns_404_for_missing(self, setup):
        client, token = setup
        r = client.delete('/admin/schedule/no-such', headers=_auth(token))
        assert r.status_code == 404

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.delete('/admin/schedule/anything')
        assert r.status_code == 401


# ===========================================================================
# HTTP — POST /admin/schedule/{task_id}/trigger
# ===========================================================================

class TestScheduleTrigger:
    def _create(self, client, token, **kw):
        payload = {'name': 'trig', 'tool': 'echo', 'args': {}, 'interval_seconds': 3600}
        payload.update(kw)
        return client.post('/admin/schedule', headers=_auth(token), json=payload).json()

    def test_returns_202(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.post(f"/admin/schedule/{t['id']}/trigger", headers=_auth(token))
        assert r.status_code == 202

    def test_response_contains_triggered_id(self, setup):
        client, token = setup
        t = self._create(client, token)
        body = client.post(f"/admin/schedule/{t['id']}/trigger", headers=_auth(token)).json()
        assert body.get('triggered') == t['id']

    def test_trigger_resets_next_run_at_to_past(self, setup):
        """next_run_at should be in the past directly after trigger."""
        from datetime import datetime, timezone
        client, token = setup
        t = self._create(client, token)
        client.post(f"/admin/schedule/{t['id']}/trigger", headers=_auth(token))
        detail = client.get(f"/admin/schedule/{t['id']}", headers=_auth(token)).json()
        nra = datetime.fromisoformat(detail['next_run_at'].replace('Z', '+00:00'))
        assert nra <= datetime.now(timezone.utc)

    def test_returns_404_for_missing(self, setup):
        client, token = setup
        r = client.post('/admin/schedule/ghost-id/trigger', headers=_auth(token))
        assert r.status_code == 404

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.post('/admin/schedule/anything/trigger')
        assert r.status_code == 401


# ===========================================================================
# get_history() — unit tests
# ===========================================================================

class TestGetHistory:
    def test_returns_none_for_missing_task(self, sched):
        assert sched.get_history('no-such-id') is None

    def test_returns_empty_list_before_any_run(self, sched):
        t = sched.add_task('h', 'echo', {}, 60)
        result = sched.get_history(t['id'])
        assert result == []

    def test_records_successful_run(self, sched):
        sched.set_executor(lambda p: {'done': True})
        t = sched.add_task('h', 'echo', {}, 60)
        raw = sched._tasks[t['id']]
        sched._run_task(raw)
        history = sched.get_history(t['id'])
        assert len(history) == 1
        rec = history[0]
        assert rec['ok'] is True
        assert rec['error'] is None
        assert isinstance(rec['duration_seconds'], float)
        assert 'timestamp' in rec

    def test_records_failed_run(self, sched):
        sched.set_executor(lambda p: (_ for _ in ()).throw(RuntimeError('boom')))
        t = sched.add_task('h', 'fail', {}, 60)
        raw = sched._tasks[t['id']]
        sched._run_task(raw)
        history = sched.get_history(t['id'])
        assert len(history) == 1
        rec = history[0]
        assert rec['ok'] is False
        assert 'boom' in rec['error']

    def test_newest_first_ordering(self, sched):
        """History is returned newest-first."""
        counter = [0]
        def executor(p):
            counter[0] += 1
            return {'run': counter[0]}
        sched.set_executor(executor)
        t = sched.add_task('h', 'echo', {}, 60)
        raw = sched._tasks[t['id']]
        sched._run_task(raw)
        sched._run_task(raw)
        sched._run_task(raw)
        history = sched.get_history(t['id'])
        assert len(history) == 3
        # run_counts should be descending (newest first)
        runs = [r['run'] for r in history]
        assert runs == sorted(runs, reverse=True)

    def test_respects_limit_parameter(self, sched):
        sched.set_executor(lambda p: {})
        t = sched.add_task('h', 'echo', {}, 60)
        raw = sched._tasks[t['id']]
        for _ in range(5):
            sched._run_task(raw)
        assert len(sched.get_history(t['id'], limit=3)) == 3
        assert len(sched.get_history(t['id'], limit=1)) == 1

    def test_cleared_on_delete(self, sched):
        sched.set_executor(lambda p: {})
        t = sched.add_task('h', 'echo', {}, 60)
        raw = sched._tasks[t['id']]
        sched._run_task(raw)
        tid = t['id']
        sched.delete_task(tid)
        # task is gone, so get_history returns None
        assert sched.get_history(tid) is None

    def test_capped_at_history_max(self, sched, monkeypatch):
        import scheduler as _sched_mod
        monkeypatch.setattr(_sched_mod, '_HISTORY_MAX', 5)
        sched.set_executor(lambda p: {})
        t = sched.add_task('h', 'echo', {}, 60)
        raw = sched._tasks[t['id']]
        # Rebuild the deque with the new (monkeypatched) maxlen
        from collections import deque
        _sched_mod._history[t['id']] = deque(maxlen=5)
        for _ in range(10):
            sched._run_task(raw)
        history = sched.get_history(t['id'])
        assert len(history) <= 5


# ===========================================================================
# HTTP — GET /admin/schedule/{task_id}/history
# ===========================================================================

class TestScheduleHistory:
    def _create(self, client, token, **kw):
        payload = {'name': 'hist', 'tool': 'echo', 'args': {}, 'interval_seconds': 3600}
        payload.update(kw)
        return client.post('/admin/schedule', headers=_auth(token), json=payload).json()

    def test_returns_200_with_empty_history(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.get(f"/admin/schedule/{t['id']}/history", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert body['task_id'] == t['id']
        assert body['count'] == 0
        assert body['history'] == []

    def test_returns_404_for_missing_task(self, setup):
        client, token = setup
        r = client.get('/admin/schedule/no-such-id/history', headers=_auth(token))
        assert r.status_code == 404

    def test_rejects_unauthenticated(self, setup):
        client, _ = setup
        r = client.get('/admin/schedule/anything/history')
        assert r.status_code == 401

    def test_history_populated_after_run(self, setup, monkeypatch):
        import scheduler as _sched
        client, token = setup
        t = self._create(client, token)
        # Simulate a successful run by directly calling _run_task
        _sched.set_executor(lambda p: {'ok': True})
        raw = _sched._tasks[t['id']]
        _sched._run_task(raw)
        r = client.get(f"/admin/schedule/{t['id']}/history", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert body['count'] == 1
        rec = body['history'][0]
        assert 'ok' in rec
        assert 'timestamp' in rec
        assert 'duration_seconds' in rec

    def test_response_has_correct_shape(self, setup):
        client, token = setup
        t = self._create(client, token)
        r = client.get(f"/admin/schedule/{t['id']}/history", headers=_auth(token))
        body = r.json()
        assert set(body.keys()) >= {'task_id', 'count', 'history'}
