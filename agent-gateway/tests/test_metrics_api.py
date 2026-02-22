"""Tests for the /metrics Prometheus endpoint."""
import json
from fastapi.testclient import TestClient
from app import app
import auth as _auth
import metrics as _metrics


client = TestClient(app)
_TEST_PW = 'dev-key'


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


def test_metrics_endpoint_returns_prometheus_format():
    _metrics.reset()
    r = client.get('/metrics')
    assert r.status_code == 200
    ct = r.headers.get('content-type', '')
    assert 'text/plain' in ct
    body = r.text
    # Should contain at least some metric lines (HELP or TYPE or metric value)
    has_content = any(
        line.startswith(('#', 'worker_', 'tool_'))
        for line in body.splitlines()
        if line.strip()
    )
    assert has_content, f"Expected Prometheus lines in response, got: {body!r}"


def test_metrics_incremented_by_tool_call():
    _metrics.reset()
    # Make a non-high-risk tool call to avoid approval queue
    call_payload = {"tool": "echo", "args": {"text": "hello"}}
    r = client.post('/tools/call', json=call_payload)
    # 200 or 400 (schema) — either way tool_calls_total should increment
    assert r.status_code in (200, 400)

    mr = client.get('/metrics')
    assert mr.status_code == 200
    # tool_calls_total counter should appear in output
    assert 'tool_calls_total' in mr.text


def test_metrics_no_auth_required():
    """Metrics endpoint is public (no auth)."""
    r = client.get('/metrics')
    assert r.status_code == 200


def test_audit_export_requires_auth():
    r = client.get('/admin/audit')
    assert r.status_code == 401


def test_audit_export_returns_entries():
    _reset_admin()
    # Login to get token
    lr = client.post('/admin/login', json={'username': 'admin', 'password': _TEST_PW})
    assert lr.status_code == 200
    token = lr.json()['token']

    # Perform an action that writes to audit log (approval)
    call_payload = {"tool": "system.exec", "args": {"cmd": "x", "token": "y"}}
    cr = client.post('/tools/call', json=call_payload)
    assert cr.status_code == 200
    req_id = cr.json().get('id')
    assert req_id is not None

    # Approve → writes audit entry
    ar = client.post(f'/approvals/{req_id}/approve',
                     headers={'Authorization': f'Bearer {token}'})
    assert ar.status_code == 200

    # Export audit
    er = client.get('/admin/audit?tail=50',
                    headers={'Authorization': f'Bearer {token}'})
    assert er.status_code == 200
    body = er.json()
    assert 'count' in body and 'entries' in body
    # At least the approval event should be there
    events = [e.get('event') for e in body['entries']]
    assert 'approve' in events


# ---------------------------------------------------------------------------
# Audit server-side filtering + CSV export
# ---------------------------------------------------------------------------

import json as _json
import datetime as _dt
from pathlib import Path as _Path


def _admin_token():
    """Return a fresh admin token, creating the user if needed."""
    _reset_admin()
    lr = client.post('/admin/login', json={'username': 'admin', 'password': _TEST_PW})
    assert lr.status_code == 200
    return lr.json()['token']


def _inject_audit_entry(ts: str, event: str, actor: str, details: dict | None = None):
    """Directly append a synthetic audit entry to the audit log."""
    import app as _app
    entry = {'ts': ts, 'event': event, 'actor': actor, 'details': details or {}}
    with _app.AUDIT_PATH.open('a', encoding='utf-8') as f:
        f.write(_json.dumps(entry) + '\n')


def test_audit_filter_by_actor():
    token = _admin_token()
    _inject_audit_entry('2099-06-01T10:00:00+00:00', 'login', 'alice')
    _inject_audit_entry('2099-06-01T10:01:00+00:00', 'login', 'bob')
    r = client.get('/admin/audit?actor=alice&tail=50',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    actors = [e.get('actor') for e in body['entries']]
    assert all(a == 'alice' for a in actors if a is not None)
    assert 'alice' in actors


def test_audit_filter_by_action():
    token = _admin_token()
    _inject_audit_entry('2099-06-01T11:00:00+00:00', 'approve', 'charlie')
    _inject_audit_entry('2099-06-01T11:01:00+00:00', 'deny',    'charlie')
    r = client.get('/admin/audit?action=approve&tail=50',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    events = [e.get('event') for e in body['entries']]
    assert all('approve' in ev for ev in events if ev)
    assert any('approve' in ev for ev in events)


def test_audit_filter_since_excludes_old():
    token = _admin_token()
    _inject_audit_entry('2000-01-01T00:00:00+00:00', 'old_event', 'tester')
    _inject_audit_entry('2099-07-01T00:00:00+00:00', 'new_event', 'tester')
    r = client.get('/admin/audit?since=2050-01-01T00%3A00%3A00%2B00%3A00&action=old_event&tail=200',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    events = [e.get('event') for e in r.json()['entries']]
    assert 'old_event' not in events


def test_audit_filter_until_excludes_new():
    token = _admin_token()
    _inject_audit_entry('2000-02-01T00:00:00+00:00', 'vintage_event', 'tester')
    _inject_audit_entry('2099-08-01T00:00:00+00:00', 'futuristic_event', 'tester')
    r = client.get('/admin/audit?until=2001-01-01T00%3A00%3A00%2B00%3A00&action=futuristic_event&tail=200',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    events = [e.get('event') for e in r.json()['entries']]
    assert 'futuristic_event' not in events


def test_audit_invalid_since_returns_400():
    token = _admin_token()
    r = client.get('/admin/audit?since=not-a-date',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 400


def test_audit_invalid_until_returns_400():
    token = _admin_token()
    r = client.get('/admin/audit?until=yesterday',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 400


def test_audit_csv_export_requires_auth():
    r = client.get('/admin/audit/export.csv')
    assert r.status_code == 401


def test_audit_csv_export_returns_csv():
    token = _admin_token()
    _inject_audit_entry('2099-09-01T00:00:00+00:00', 'csv_test_event', 'csv_tester')
    r = client.get('/admin/audit/export.csv?action=csv_test_event&tail=50',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    ct = r.headers.get('content-type', '')
    assert 'text/csv' in ct
    text = r.text
    # Header row
    assert text.startswith('ts,event,actor,details')
    # Data row present
    assert 'csv_test_event' in text
    assert 'csv_tester'     in text


def test_audit_csv_export_content_disposition():
    token = _admin_token()
    r = client.get('/admin/audit/export.csv?tail=1',
                   headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    cd = r.headers.get('content-disposition', '')
    assert 'attachment' in cd
    assert 'audit.csv'  in cd


# ---------------------------------------------------------------------------
# GET /admin/status
# ---------------------------------------------------------------------------

def test_gateway_status_requires_auth():
    r = client.get('/admin/status')
    assert r.status_code == 401


def test_gateway_status_returns_expected_fields():
    token = _admin_token()
    r = client.get('/admin/status', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    for field in ('version', 'uptime_seconds', 'kill_switch_active',
                  'tool_calls_total', 'pending_approvals',
                  'scheduler_tasks', 'memory_agents'):
        assert field in body, f'Missing field: {field}'


def test_gateway_status_uptime_is_positive():
    token = _admin_token()
    r = client.get('/admin/status', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    assert r.json()['uptime_seconds'] >= 0


def test_gateway_status_kill_switch_off_by_default():
    token = _admin_token()
    # Ensure kill-switch is off first
    client.delete('/admin/kill-switch', headers={'Authorization': f'Bearer {token}'})
    r = client.get('/admin/status', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['kill_switch_active'] is False
    assert body['kill_switch_reason'] is None


def test_gateway_status_reflects_kill_switch_on():
    token = _admin_token()
    # Arm the kill-switch
    client.post('/admin/kill-switch',
                json={'reason': 'status_test'},
                headers={'Authorization': f'Bearer {token}'})
    r = client.get('/admin/status', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['kill_switch_active'] is True
    assert body['kill_switch_reason'] == 'status_test'
    # Restore
    client.delete('/admin/kill-switch', headers={'Authorization': f'Bearer {token}'})


# ---------------------------------------------------------------------------
# GET /admin/metrics/tools
# ---------------------------------------------------------------------------

def test_metrics_tools_requires_auth():
    r = client.get('/admin/metrics/tools')
    assert r.status_code == 401


def test_metrics_tools_returns_expected_shape():
    token = _admin_token()
    _metrics.reset()
    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert 'tools' in body
    assert 'total' in body
    assert isinstance(body['tools'], list)
    assert isinstance(body['total'], int)


def test_metrics_tools_records_tool_call():
    token = _admin_token()
    _metrics.reset()
    # Make a tool call so that tool_calls_total{tool="echo"} gets incremented
    client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'hi'}})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    tool_names = [t['tool'] for t in body['tools']]
    assert 'echo' in tool_names


def test_metrics_tools_counts_are_positive():
    token = _admin_token()
    _metrics.reset()
    client.post('/tools/call', json={'tool': 'noop', 'args': {}})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    for entry in r.json()['tools']:
        assert entry['calls'] >= 0


def test_metrics_tools_total_matches_sum():
    token = _admin_token()
    _metrics.reset()
    client.post('/tools/call', json={'tool': 'noop', 'args': {}})
    client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'x'}})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == sum(t['calls'] for t in body['tools'])


def test_metrics_tools_sorted_descending():
    token = _admin_token()
    _metrics.reset()
    # Call 'echo' twice, 'noop' once → echo should rank first
    client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'a'}})
    client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'b'}})
    client.post('/tools/call', json={'tool': 'noop', 'args': {}})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    tools = r.json()['tools']
    counts = [t['calls'] for t in tools]
    assert counts == sorted(counts, reverse=True)


def test_metrics_tools_empty_when_no_calls():
    token = _admin_token()
    _metrics.reset()
    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    assert body['tools'] == []
    assert body['total'] == 0


def test_gateway_status_tool_calls_total_sum():
    """GET /admin/status tool_calls_total must sum across all tools, not read unlabelled bucket."""
    token = _admin_token()
    _metrics.reset()
    client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'x'}})
    client.post('/tools/call', json={'tool': 'noop', 'args': {}})

    r = client.get('/admin/status', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    body = r.json()
    # Should be at least 2 (one echo + one noop), not 0
    assert body['tool_calls_total'] >= 2


# ---------------------------------------------------------------------------
# get_labels_for_histogram (metrics.py unit tests)
# ---------------------------------------------------------------------------

def test_get_labels_for_histogram_empty_when_no_observations():
    _metrics.reset()
    result = _metrics.get_labels_for_histogram('tool_call_duration_seconds')
    assert result == []


def test_get_labels_for_histogram_returns_tuples():
    _metrics.reset()
    _metrics.observe('tool_call_duration_seconds', 0.05, labels={'tool': 'echo'})
    result = _metrics.get_labels_for_histogram('tool_call_duration_seconds')
    assert len(result) == 1
    labels, s, c, vals = result[0]
    assert labels == {'tool': 'echo'}
    assert s == pytest.approx(0.05)
    assert c == 1
    assert vals == pytest.approx([0.05])


def test_get_labels_for_histogram_multiple_labels():
    _metrics.reset()
    for _ in range(3):
        _metrics.observe('tool_call_duration_seconds', 0.1, labels={'tool': 'echo'})
    _metrics.observe('tool_call_duration_seconds', 0.2, labels={'tool': 'noop'})
    result = _metrics.get_labels_for_histogram('tool_call_duration_seconds')
    counts = {r[0]['tool']: r[2] for r in result}
    assert counts['echo'] == 3
    assert counts['noop'] == 1


def test_get_labels_for_histogram_sorted_by_count_desc():
    _metrics.reset()
    _metrics.observe('tool_call_duration_seconds', 0.1, labels={'tool': 'a'})
    for _ in range(5):
        _metrics.observe('tool_call_duration_seconds', 0.2, labels={'tool': 'b'})
    result = _metrics.get_labels_for_histogram('tool_call_duration_seconds')
    counts = [r[2] for r in result]
    assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# /admin/metrics/tools — latency fields
# ---------------------------------------------------------------------------

import pytest


def test_metrics_tools_has_latency_fields_after_call():
    token = _admin_token()
    _metrics.reset()
    client.post('/tools/call', json={'tool': 'echo', 'args': {'text': 'hi'}})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    tools = r.json()['tools']
    echo_entries = [t for t in tools if t['tool'] == 'echo']
    assert len(echo_entries) == 1
    entry = echo_entries[0]
    # p50_seconds and mean_seconds should be present and positive
    assert 'p50_seconds' in entry
    assert entry['p50_seconds'] > 0
    assert 'mean_seconds' in entry
    assert entry['mean_seconds'] > 0


def test_metrics_tools_no_latency_when_no_histogram():
    """When histogram has no observations for a tool, latency keys are absent."""
    token = _admin_token()
    _metrics.reset()
    # Inject a counter without a corresponding histogram observation
    _metrics.inc('tool_calls_total', labels={'tool': 'synthetic'})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    tools = r.json()['tools']
    synth = next((t for t in tools if t['tool'] == 'synthetic'), None)
    assert synth is not None
    assert 'p50_seconds' not in synth
    assert 'mean_seconds' not in synth


def test_metrics_tools_p50_is_plausible():
    """p50 should be between the min and max observation values."""
    token = _admin_token()
    _metrics.reset()
    # Manually observe 10 values: 0.01, 0.02, …, 0.10
    for i in range(1, 11):
        _metrics.observe('tool_call_duration_seconds', i * 0.01, labels={'tool': 'echo'})
    _metrics.inc('tool_calls_total', labels={'tool': 'echo'})

    r = client.get('/admin/metrics/tools', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    echo = next(t for t in r.json()['tools'] if t['tool'] == 'echo')
    assert 0.01 <= echo['p50_seconds'] <= 0.10

