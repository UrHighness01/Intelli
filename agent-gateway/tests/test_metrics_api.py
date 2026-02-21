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
