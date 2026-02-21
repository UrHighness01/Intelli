import json
import re
from jsonschema import validate, ValidationError
from pathlib import Path
from typing import Any, Dict


class ApprovalQueue:
    def __init__(self):
        self._store = {}
        self._next = 1

    def submit(self, payload: Dict[str, Any]) -> int:
        id_ = self._next
        self._store[id_] = {"payload": payload, "status": "pending"}
        self._next += 1
        return id_

    def approve(self, id_: int):
        if id_ in self._store:
            self._store[id_]["status"] = "approved"
            return True
        return False

    def reject(self, id_: int):
        if id_ in self._store:
            self._store[id_]["status"] = "rejected"
            return True
        return False

    def status(self, id_: int):
        return self._store.get(id_)

    def list_pending(self):
        return {k: v for k, v in self._store.items() if v["status"] == "pending"}


class Supervisor:
    SENSITIVE_KEYS = re.compile(r"password|secret|token|api_key|cvv|card|ssn|credentials", re.I)

    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
        self.queue = ApprovalQueue()
        # Define simple high-risk tool ids
        self.high_risk_tools = {"system.exec", "file.write", "system.update"}

    def _validate_schema(self, payload: Dict[str, Any]):
        try:
            validate(instance=payload, schema=self.schema)
        except ValidationError as e:
            raise e

    def _sanitize(self, obj: Any):
        if isinstance(obj, dict):
            sanitized = {}
            for k, v in obj.items():
                if self.SENSITIVE_KEYS.search(k):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = self._sanitize(v)
            return sanitized
        if isinstance(obj, list):
            return [self._sanitize(i) for i in obj]
        return obj

    def approval_required(self, payload: Dict[str, Any]) -> bool:
        tool = payload.get("tool", "")
        return tool in self.high_risk_tools

    def process_call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Validate schema
        try:
            self._validate_schema(payload)
        except ValidationError as e:
            return {"error": f"schema validation failed: {e.message}"}

        # Sanitize
        sanitized = {"tool": payload.get("tool"), "args": self._sanitize(payload.get("args", {}))}

        # If approval required, enqueue and return pending status
        if self.approval_required(payload):
            req_id = self.queue.submit(sanitized)
            return {"status": "pending_approval", "id": req_id}

        # Otherwise accept and return sanitized stubbed result
        return {"tool": sanitized["tool"], "args": sanitized["args"], "status": "accepted", "message": "validated and sanitized (supervisor)"}


def load_schema_from_file(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    # simple smoke
    schema = load_schema_from_file(Path(__file__).with_name("tool_schema.json"))
    sup = Supervisor(schema)
    print(sup.process_call({"tool": "echo", "args": {"text": "hi", "password": "secret"}}))
