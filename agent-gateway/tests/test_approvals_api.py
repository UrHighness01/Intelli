import json
import os
from fastapi.testclient import TestClient
from app import app
import auth as _auth


client = TestClient(app)
_TEST_PW = 'dev-key'


def _reset_admin(pw: str = _TEST_PW):
    """Ensure a fresh admin account with the given password (test isolation)."""
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


def _get_admin_token():
    """Login as admin and return an access token."""
    r = client.post('/admin/login', json={'username': 'admin', 'password': _TEST_PW})
    assert r.status_code == 200, f'admin login failed: {r.text}'
    return r.json()['token']


def test_approval_workflow():
    _reset_admin()
    # Submit a high-risk tool call which should enqueue for approval
    payload = {"tool": "system.exec", "args": {"cmd": "do dangerous", "token": "abc"}}
    r = client.post("/tools/call", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "pending_approval"
    req_id = body.get("id")
    assert isinstance(req_id, int)

    # List approvals
    r = client.get("/approvals")
    assert r.status_code == 200
    pend = r.json().get("pending")
    assert str(req_id) in pend

    # Get single approval
    r = client.get(f"/approvals/{req_id}")
    assert r.status_code == 200
    assert r.json().get("status") == "pending"

    # Approve it — requires admin Bearer token
    admin_token = _get_admin_token()
    r = client.post(f"/approvals/{req_id}/approve", headers={'Authorization': f'Bearer {admin_token}'})
    assert r.status_code == 200
    assert r.json().get("status") == "approved"

    # Now status should be approved
    r = client.get(f"/approvals/{req_id}")
    assert r.status_code == 200
    assert r.json().get("status") == "approved"
