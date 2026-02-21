"""Tests for agent-gateway/agent_memory.py.

Covers:
  - _validate_id           – accepts valid IDs, rejects invalid ones
  - memory_set/get         – basic round-trip
  - memory_set TTL/expiry  – key expires after ttl_seconds, prune, get_meta
  - memory_delete          – key deletion, missing key returns False
  - memory_list            – returns all keys for an agent (excludes expired)
  - memory_clear           – removes all keys, returns count
  - memory_prune           – removes only expired keys
  - list_agents            – lists all agent IDs with stored memory
  - HTTP endpoints         – GET/POST/DELETE /agents/…  (FastAPI TestClient)
"""
import importlib
import os
import sys
import time as _time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_memory_dir(monkeypatch, tmp_path):
    """Redirect every test to an isolated temp directory and reload module."""
    monkeypatch.setenv('AGENT_GATEWAY_MEMORY_DIR', str(tmp_path / 'agent_memories'))
    import agent_memory
    importlib.reload(agent_memory)
    return agent_memory


@pytest.fixture()
def mem(tmp_memory_dir):
    return tmp_memory_dir


# ---------------------------------------------------------------------------
# _validate_id
# ---------------------------------------------------------------------------

class TestValidateId:
    @pytest.mark.parametrize('good_id', ['agent1', 'agent-2', 'MY_AGENT', 'a' * 128])
    def test_valid_ids_accepted(self, mem, good_id):
        # Should not raise
        mem.memory_set(good_id, 'k', 'v')

    @pytest.mark.parametrize('bad_id', [
        '',
        'has space',
        '../evil',
        'a' * 129,
        'slash/path',
        'dot.dot',
    ])
    def test_invalid_ids_rejected(self, mem, bad_id):
        with pytest.raises(ValueError):
            mem.memory_set(bad_id, 'key', 'value')


# ---------------------------------------------------------------------------
# memory_set / memory_get
# ---------------------------------------------------------------------------

class TestMemorySetGet:
    def test_string_roundtrip(self, mem):
        mem.memory_set('agent1', 'greeting', 'hello')
        assert mem.memory_get('agent1', 'greeting') == 'hello'

    def test_integer_roundtrip(self, mem):
        mem.memory_set('agent1', 'count', 42)
        assert mem.memory_get('agent1', 'count') == 42

    def test_dict_roundtrip(self, mem):
        data = {'a': 1, 'b': [1, 2, 3]}
        mem.memory_set('agent1', 'obj', data)
        assert mem.memory_get('agent1', 'obj') == data

    def test_missing_key_returns_none(self, mem):
        assert mem.memory_get('agent1', 'no_such_key') is None

    def test_missing_agent_returns_none(self, mem):
        assert mem.memory_get('nonexistent_agent', 'k') is None

    def test_overwrite_value(self, mem):
        mem.memory_set('a1', 'x', 'old')
        mem.memory_set('a1', 'x', 'new')
        assert mem.memory_get('a1', 'x') == 'new'

    def test_multiple_keys_independent(self, mem):
        mem.memory_set('a1', 'foo', 'FOO')
        mem.memory_set('a1', 'bar', 'BAR')
        assert mem.memory_get('a1', 'foo') == 'FOO'
        assert mem.memory_get('a1', 'bar') == 'BAR'


# ---------------------------------------------------------------------------
# TTL / expiry
# ---------------------------------------------------------------------------


class TestTTL:
    def test_key_accessible_within_ttl(self, mem):
        mem.memory_set('a1', 'temp', 'session', ttl_seconds=3600)
        assert mem.memory_get('a1', 'temp') == 'session'

    def test_expired_key_returns_none(self, mem):
        mem.memory_set('a1', 'gone', 'bye', ttl_seconds=0.01)
        _time.sleep(0.05)
        assert mem.memory_get('a1', 'gone') is None

    def test_expired_key_excluded_from_list(self, mem):
        mem.memory_set('a1', 'live', 'yes', ttl_seconds=3600)
        mem.memory_set('a1', 'dead', 'no',  ttl_seconds=0.01)
        _time.sleep(0.05)
        result = mem.memory_list('a1')
        assert 'live' in result
        assert 'dead' not in result

    def test_expired_key_delete_returns_false(self, mem):
        mem.memory_set('a1', 'temp', 'x', ttl_seconds=0.01)
        _time.sleep(0.05)
        assert mem.memory_delete('a1', 'temp') is False

    def test_no_ttl_key_never_expires(self, mem):
        mem.memory_set('a1', 'forever', 'yes')
        assert mem.memory_get('a1', 'forever') == 'yes'

    def test_overwrite_adds_ttl(self, mem):
        mem.memory_set('a1', 'k', 'v1')
        mem.memory_set('a1', 'k', 'v2', ttl_seconds=0.01)
        _time.sleep(0.05)
        assert mem.memory_get('a1', 'k') is None

    def test_overwrite_removes_ttl(self, mem):
        mem.memory_set('a1', 'k', 'v1', ttl_seconds=0.01)
        _time.sleep(0.005)
        mem.memory_set('a1', 'k', 'v2')          # rewrite without TTL
        _time.sleep(0.05)                          # original TTL would have passed
        assert mem.memory_get('a1', 'k') == 'v2'  # should still be present

    def test_memory_prune_removes_expired(self, mem):
        mem.memory_set('a1', 'live',  'yes', ttl_seconds=3600)
        mem.memory_set('a1', 'dead1', 'no',  ttl_seconds=0.01)
        mem.memory_set('a1', 'dead2', 'no',  ttl_seconds=0.01)
        _time.sleep(0.05)
        pruned = mem.memory_prune('a1')
        assert pruned == 2
        assert mem.memory_list('a1') == {'live': 'yes'}

    def test_memory_prune_empty_returns_zero(self, mem):
        assert mem.memory_prune('nobody') == 0

    def test_memory_prune_no_expired_returns_zero(self, mem):
        mem.memory_set('a1', 'k', 'v', ttl_seconds=3600)
        assert mem.memory_prune('a1') == 0

    def test_memory_get_meta_returns_value_and_expiry(self, mem):
        before = _time.time()
        mem.memory_set('a1', 'session', 'tok', ttl_seconds=60)
        meta = mem.memory_get_meta('a1', 'session')
        assert meta is not None
        assert meta['value'] == 'tok'
        assert meta['expires_at'] is not None
        assert meta['expires_at'] > before + 59   # roughly now + 60

    def test_memory_get_meta_no_ttl_key(self, mem):
        mem.memory_set('a1', 'k', 'v')
        meta = mem.memory_get_meta('a1', 'k')
        assert meta is not None
        assert meta['value'] == 'v'
        assert meta['expires_at'] is None

    def test_memory_get_meta_missing_returns_none(self, mem):
        assert mem.memory_get_meta('a1', 'no_such') is None

    def test_memory_get_meta_expired_returns_none(self, mem):
        mem.memory_set('a1', 'x', 'v', ttl_seconds=0.01)
        _time.sleep(0.05)
        assert mem.memory_get_meta('a1', 'x') is None


# ---------------------------------------------------------------------------
# memory_delete
# ---------------------------------------------------------------------------

class TestMemoryDelete:
    def test_delete_existing_key(self, mem):
        mem.memory_set('agent1', 'k', 'v')
        assert mem.memory_delete('agent1', 'k') is True
        assert mem.memory_get('agent1', 'k') is None

    def test_delete_missing_key_returns_false(self, mem):
        assert mem.memory_delete('agent1', 'no_such') is False

    def test_delete_does_not_affect_other_keys(self, mem):
        mem.memory_set('a1', 'x', 1)
        mem.memory_set('a1', 'y', 2)
        mem.memory_delete('a1', 'x')
        assert mem.memory_get('a1', 'y') == 2


# ---------------------------------------------------------------------------
# memory_list
# ---------------------------------------------------------------------------

class TestMemoryList:
    def test_returns_all_keys(self, mem):
        mem.memory_set('a1', 'p', 1)
        mem.memory_set('a1', 'q', 2)
        result = mem.memory_list('a1')
        assert result == {'p': 1, 'q': 2}

    def test_empty_agent_returns_empty_dict(self, mem):
        assert mem.memory_list('brand_new_agent') == {}


# ---------------------------------------------------------------------------
# memory_clear
# ---------------------------------------------------------------------------

class TestMemoryClear:
    def test_clear_removes_all_keys(self, mem):
        for k in ['a', 'b', 'c']:
            mem.memory_set('a1', k, k)
        removed = mem.memory_clear('a1')
        assert removed == 3
        assert mem.memory_list('a1') == {}

    def test_clear_empty_agent_returns_zero(self, mem):
        assert mem.memory_clear('nobody') == 0


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------

class TestListAgents:
    def test_starts_empty(self, mem):
        assert mem.list_agents() == []

    def test_lists_created_agents(self, mem):
        mem.memory_set('alpha', 'k', 1)
        mem.memory_set('beta', 'k', 2)
        agents = mem.list_agents()
        assert sorted(agents) == ['alpha', 'beta']

    def test_cleared_agent_still_listed(self, mem):
        """Clearing memory doesn't delete the agent file."""
        mem.memory_set('a1', 'k', 'v')
        mem.memory_clear('a1')
        # File still exists with empty dict — agent still appears
        assert 'a1' in mem.list_agents()


# ---------------------------------------------------------------------------
# HTTP endpoints via FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_memory_dir, tmp_path, monkeypatch):
    """Return a (TestClient, admin_headers) pair with isolated auth + fresh memory."""
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'testpass-mem')
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    from fastapi.testclient import TestClient
    from app import app
    tc = TestClient(app)
    r = tc.post('/admin/login', json={'username': 'admin', 'password': 'testpass-mem'})
    token = r.json()['token']
    return tc, {'Authorization': f'Bearer {token}'}


@pytest.fixture()
def admin_headers(client):
    _, headers = client
    return headers


class TestAgentMemoryHTTP:
    def test_list_all_agents_empty(self, client, admin_headers):
        tc, _ = client
        r = tc.get('/agents', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['agents'] == []

    def test_upsert_and_get_key(self, client, admin_headers):
        tc, _ = client
        r = tc.post(
            '/agents/bot1/memory',
            json={'key': 'name', 'value': 'Testy McTestface'},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()['stored'] is True

        r2 = tc.get('/agents/bot1/memory/name', headers=admin_headers)
        assert r2.status_code == 200
        assert r2.json()['value'] == 'Testy McTestface'

    def test_get_missing_key_returns_404(self, client, admin_headers):
        tc, _ = client
        r = tc.get('/agents/bot1/memory/no_key', headers=admin_headers)
        assert r.status_code == 404

    def test_list_memory_keys(self, client, admin_headers):
        tc, _ = client
        for k, v in [('x', 1), ('y', 2)]:
            tc.post('/agents/bot2/memory', json={'key': k, 'value': v}, headers=admin_headers)
        r = tc.get('/agents/bot2/memory', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['memory'] == {'x': 1, 'y': 2}

    def test_delete_key(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/bot3/memory', json={'key': 'tmp', 'value': 'val'}, headers=admin_headers)
        r = tc.delete('/agents/bot3/memory/tmp', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['deleted'] is True

    def test_delete_missing_key_returns_404(self, client, admin_headers):
        tc, _ = client
        r = tc.delete('/agents/bot3/memory/ghost', headers=admin_headers)
        assert r.status_code == 404

    def test_clear_agent_memory(self, client, admin_headers):
        tc, _ = client
        for k in ['a', 'b', 'c']:
            tc.post('/agents/bot4/memory', json={'key': k, 'value': k}, headers=admin_headers)
        r = tc.delete('/agents/bot4/memory', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['cleared'] == 3

    def test_list_agents_after_write(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/agent_listed/memory', json={'key': 'k', 'value': 1}, headers=admin_headers)
        r = tc.get('/agents', headers=admin_headers)
        assert 'agent_listed' in r.json()['agents']

    def test_invalid_agent_id_returns_422(self, client, admin_headers):
        tc, _ = client
        r = tc.get('/agents/../evil/memory', headers=admin_headers)
        # FastAPI may return 404 (path not matched) or 422 — either is fine
        assert r.status_code in (404, 422)

    def test_requires_admin_auth(self, client):
        tc, _ = client
        r = tc.get('/agents')
        assert r.status_code in (401, 403)

    def test_upsert_with_ttl_seconds(self, client, admin_headers):
        tc, _ = client
        r = tc.post(
            '/agents/ttlbot/memory',
            json={'key': 'session', 'value': 'tok', 'ttl_seconds': 3600},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()['ttl_seconds'] == 3600
        # Value should be accessible
        r2 = tc.get('/agents/ttlbot/memory/session', headers=admin_headers)
        assert r2.status_code == 200
        assert r2.json()['value'] == 'tok'
        assert r2.json()['expires_at'] is not None

    def test_get_key_returns_expires_at_none_when_no_ttl(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/a1/memory', json={'key': 'k', 'value': 'v'}, headers=admin_headers)
        r = tc.get('/agents/a1/memory/k', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['expires_at'] is None

    def test_prune_endpoint(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/prunebot/memory', json={'key': 'live', 'value': 1, 'ttl_seconds': 3600}, headers=admin_headers)
        tc.post('/agents/prunebot/memory', json={'key': 'dead', 'value': 2, 'ttl_seconds': 0.01}, headers=admin_headers)
        _time.sleep(0.05)
        r = tc.post('/agents/prunebot/memory/prune', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['pruned'] == 1


# ---------------------------------------------------------------------------
# export_all / import_all (module-level)
# ---------------------------------------------------------------------------

class TestExportAll:
    def test_empty_when_no_agents(self, mem):
        result = mem.export_all()
        assert result['agents'] == {}
        assert result['agent_count'] == 0
        assert result['key_count'] == 0
        assert 'exported_at' in result

    def test_export_includes_all_live_keys(self, mem):
        mem.memory_set('a1', 'x', 1)
        mem.memory_set('a1', 'y', 2)
        mem.memory_set('a2', 'z', 3)
        result = mem.export_all()
        assert result['agent_count'] == 2
        assert result['key_count'] == 3
        assert result['agents']['a1'] == {'x': 1, 'y': 2}
        assert result['agents']['a2'] == {'z': 3}

    def test_export_excludes_expired_keys(self, mem):
        mem.memory_set('exp_agent', 'live', 'yes', ttl_seconds=3600)
        mem.memory_set('exp_agent', 'dead', 'no',  ttl_seconds=0.01)
        _time.sleep(0.05)
        result = mem.export_all()
        assert 'dead' not in result['agents']['exp_agent']
        assert result['agents']['exp_agent']['live'] == 'yes'

    def test_export_exported_at_is_iso8601(self, mem):
        result = mem.export_all()
        # Should look like 2025-01-01T00:00:00Z
        import re
        assert re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', result['exported_at'])


class TestImportAll:
    def test_basic_import_creates_agents(self, mem):
        result = mem.import_all({'bot-a': {'k1': 'v1', 'k2': 'v2'}})
        assert result['imported_agents'] == 1
        assert result['imported_keys'] == 2
        assert mem.memory_get('bot-a', 'k1') == 'v1'
        assert mem.memory_get('bot-a', 'k2') == 'v2'

    def test_import_merge_true_preserves_existing_keys(self, mem):
        mem.memory_set('bot-b', 'original', 'kept')
        mem.import_all({'bot-b': {'new_key': 'added'}}, merge=True)
        assert mem.memory_get('bot-b', 'original') == 'kept'
        assert mem.memory_get('bot-b', 'new_key') == 'added'

    def test_import_merge_false_replaces_agent_memory(self, mem):
        mem.memory_set('bot-c', 'original', 'gone')
        mem.import_all({'bot-c': {'brand_new': 'yes'}}, merge=False)
        # Original key should be gone
        assert mem.memory_get('bot-c', 'original') is None
        assert mem.memory_get('bot-c', 'brand_new') == 'yes'

    def test_import_merge_true_overwrites_matching_keys(self, mem):
        mem.memory_set('bot-d', 'key', 'old')
        mem.import_all({'bot-d': {'key': 'new'}}, merge=True)
        assert mem.memory_get('bot-d', 'key') == 'new'

    def test_import_invalid_agent_id_raises(self, mem):
        with pytest.raises(ValueError):
            mem.import_all({'../../evil': {'k': 'v'}})

    def test_import_skips_non_dict_agent_entries(self, mem):
        result = mem.import_all({'bot-e': 'not_a_dict'})  # type: ignore[arg-type]
        assert result['imported_agents'] == 0

    def test_import_empty_agents_dict(self, mem):
        result = mem.import_all({})
        assert result['imported_agents'] == 0
        assert result['imported_keys'] == 0

    def test_roundtrip_export_import(self, mem):
        mem.memory_set('rt-agent', 'hello', 'world')
        snapshot = mem.export_all()
        # Clear and restore
        mem.memory_clear('rt-agent')
        assert mem.memory_get('rt-agent', 'hello') is None
        mem.import_all(snapshot['agents'], merge=False)
        assert mem.memory_get('rt-agent', 'hello') == 'world'


# ---------------------------------------------------------------------------
# HTTP: GET /admin/memory/export  &  POST /admin/memory/import
# ---------------------------------------------------------------------------

class TestMemoryExportImportHTTP:
    def test_requires_auth_export(self, client):
        tc, _ = client
        r = tc.get('/admin/memory/export')
        assert r.status_code in (401, 403)

    def test_requires_auth_import(self, client):
        tc, _ = client
        r = tc.post('/admin/memory/import', json={'agents': {}})
        assert r.status_code in (401, 403)

    def test_export_returns_expected_shape(self, client, admin_headers):
        tc, _ = client
        r = tc.get('/admin/memory/export', headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert 'agents' in body
        assert 'agent_count' in body
        assert 'key_count' in body
        assert 'exported_at' in body

    def test_export_includes_stored_data(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/expbot/memory', json={'key': 'x', 'value': 42}, headers=admin_headers)
        r = tc.get('/admin/memory/export', headers=admin_headers)
        assert r.status_code == 200
        assert r.json()['agents'].get('expbot', {}).get('x') == 42

    def test_import_merge_default(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/imp-bot/memory', json={'key': 'existing', 'value': 'stay'}, headers=admin_headers)
        r = tc.post(
            '/admin/memory/import',
            json={'agents': {'imp-bot': {'new_key': 'added'}}},
            headers=admin_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body['imported_agents'] == 1
        assert body['imported_keys'] == 1
        # Existing key preserved in merge mode
        rg = tc.get('/agents/imp-bot/memory/existing', headers=admin_headers)
        assert rg.json()['value'] == 'stay'

    def test_import_merge_false_replaces(self, client, admin_headers):
        tc, _ = client
        tc.post('/agents/rep-bot/memory', json={'key': 'gone', 'value': 'will_vanish'}, headers=admin_headers)
        tc.post(
            '/admin/memory/import',
            json={'agents': {'rep-bot': {'brand_new': 'hi'}}, 'merge': False},
            headers=admin_headers,
        )
        rg = tc.get('/agents/rep-bot/memory/gone', headers=admin_headers)
        assert rg.status_code == 404

    def test_import_invalid_agent_id_returns_422(self, client, admin_headers):
        tc, _ = client
        r = tc.post(
            '/admin/memory/import',
            json={'agents': {'../../evil': {'k': 'v'}}},
            headers=admin_headers,
        )
        assert r.status_code == 422
