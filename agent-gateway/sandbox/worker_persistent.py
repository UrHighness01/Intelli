#!/usr/bin/env python3
"""Persistent sandbox worker — line-oriented IPC.

Reads newline-delimited JSON requests from stdin and writes newline-delimited
JSON responses to stdout.  Runs in an infinite loop until stdin closes or a
SIGTERM is received, allowing a WorkerPool to reuse the process across calls.

Wire format (both directions): one JSON object per line, no trailing spaces.
  Request:   {"id": "<uuid>", "action": "<name>", "params": {…}}
  Response:  {"id": "<uuid>", "result": {…}}
             {"id": "<uuid>", "error": "<message>"}
"""
import sys
import json
import signal


def _handle_noop(params):
    return {"status": "ok", "message": "noop"}


def _handle_echo(params):
    safe = {}
    for k, v in (params or {}).items():
        try:
            json.dumps(v)
            safe[k] = v
        except Exception:
            safe[k] = str(v)
    return {"status": "ok", "echo": safe}


ALLOWED = {
    "noop": _handle_noop,
    "echo": _handle_echo,
}

MAX_INPUT_BYTES = 256 * 1024  # 256 KB per line


def _respond(obj: dict):
    print(json.dumps(obj), flush=True)


def _handle_line(line: str):
    req_id = None
    try:
        if len(line) > MAX_INPUT_BYTES:
            _respond({"error": "input too large"})
            return
        payload = json.loads(line)
        req_id = payload.get("id")
        action = payload.get("action")
        params = payload.get("params") or {}
        if action not in ALLOWED:
            _respond({"id": req_id, "error": f"action not allowed: {action}"})
            return
        result = ALLOWED[action](params)
        _respond({"id": req_id, "result": result})
    except Exception as e:
        _respond({"id": req_id, "error": str(e)})


def main():
    # Graceful shutdown on SIGTERM (Unix); Windows ignores but that's fine.
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except Exception:
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        _handle_line(line)


if __name__ == "__main__":
    main()
