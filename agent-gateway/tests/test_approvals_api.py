from fastapi.testclient import TestClient
from app import app


client = TestClient(app)


def test_approval_workflow():
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

    # Approve it
    r = client.post(f"/approvals/{req_id}/approve")
    assert r.status_code == 200
    assert r.json().get("status") == "approved"

    # Now status should be approved
    r = client.get(f"/approvals/{req_id}")
    assert r.status_code == 200
    assert r.json().get("status") == "approved"
