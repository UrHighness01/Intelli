#!/usr/bin/env python3
"""Simple sandbox worker process.

Receives a single JSON object on stdin with keys: action, params
Writes a JSON object to stdout with either {"result": ...} or {"error": ...}

This is intentionally minimal — it is a scaffold demonstrating subprocess
isolation. A production worker must enforce OS-level sandboxing.
"""
import sys
import json
from typing import Any, Dict


class WorkerError(Exception):
    pass


def _handle_noop(params: Dict[str, Any]):
    return {"status": "ok", "message": "noop"}


def _handle_echo(params: Dict[str, Any]):
    safe = {}
    for k, v in (params or {}).items():
        try:
            json.dumps(v)
            safe[k] = v
        except Exception:
            safe[k] = str(v)
    return {"status": "ok", "echo": safe}


_ALLOWED = {
    "noop": _handle_noop,
    "echo": _handle_echo,
}


def main():
    try:
        raw = sys.stdin.read()
        if not raw:
            print(json.dumps({"error": "no input"}))
            sys.exit(2)
        payload = json.loads(raw)
        action = payload.get("action")
        params = payload.get("params") or {}
        if action not in _ALLOWED:
            print(json.dumps({"error": f"action not allowed: {action}"}))
            sys.exit(3)
        handler = _ALLOWED[action]
        result = handler(params)
        print(json.dumps({"result": result}))
        sys.exit(0)
    except Exception as e:
        try:
            print(json.dumps({"error": str(e)}))
        except Exception:
            print(json.dumps({"error": "worker failure"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
