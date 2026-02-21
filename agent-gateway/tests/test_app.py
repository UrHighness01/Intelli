import pytest
from fastapi.testclient import TestClient
from app import app


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_validate_good_payload():
    payload = {"tool": "echo", "args": {"text": "hello"}}
    r = client.post("/validate", json=payload)
    assert r.status_code == 200
    assert r.json().get("valid") is True


def test_validate_bad_payload():
    payload = {"tool": 123, "bad": "field"}
    r = client.post("/validate", json=payload)
    assert r.status_code == 400


def test_tool_call_stub():
    payload = {"tool": "summarize", "args": {"url": "https://example.com"}}
    r = client.post("/tools/call", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "stubbed"
    assert body.get("tool") == "summarize"
