from supervisor import Supervisor, load_schema_from_file
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parents[1].joinpath("tool_schema.json")
schema = load_schema_from_file(SCHEMA_PATH)
sup = Supervisor(schema)


def test_sanitize_and_accept():
    payload = {"tool": "echo", "args": {"text": "hello", "password": "s3cr3t"}}
    res = sup.process_call(payload)
    assert res.get("status") == "accepted"
    assert res["args"]["password"] == "[REDACTED]"


def test_approval_queue():
    payload = {"tool": "system.exec", "args": {"cmd": "rm -rf /", "token": "abcdef"}}
    res = sup.process_call(payload)
    assert res.get("status") == "pending_approval"
    req_id = res.get("id")
    assert req_id is not None
    status_obj = sup.queue.status(req_id)
    assert status_obj["status"] == "pending"
    sup.queue.approve(req_id)
    assert sup.queue.status(req_id)["status"] == "approved"
