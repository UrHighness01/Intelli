#!/usr/bin/env python3
"""Simple sandbox worker process.

Receives a single JSON object on stdin with keys: action, params
Writes a JSON object to stdout with either {"result": ...} or {"error": ...}

This is intentionally minimal — it is a scaffold demonstrating subprocess
isolation. A production worker must enforce OS-level sandboxing.
"""
import sys
import json
import subprocess
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


def _handle_shell(params: Dict[str, Any]):
    """Run a shell command inside the sandbox with hard limits."""
    cmd     = params.get("cmd", "")
    timeout = int(params.get("timeout", 30))
    cwd     = params.get("cwd", "/workspace")
    max_out = int(params.get("max_output", 8000))
    if not cmd:
        return {"error": "cmd is required"}
    # Hard cap timeout inside worker so container can't be cheated
    timeout = min(timeout, 120)
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = proc.stdout or ""
        if proc.stderr.strip():
            combined += "\n[stderr]\n" + proc.stderr
        if len(combined) > max_out:
            combined = combined[:max_out] + f"\n… (truncated, {len(combined)} chars total)"
        return {"exit_code": proc.returncode, "output": combined}
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


_ALLOWED = {
    "noop":  _handle_noop,
    "echo":  _handle_echo,
    "shell": _handle_shell,
}


def main():
    try:
        raw = sys.stdin.read()
        if not raw:
            print(json.dumps({"error": "no input"}))
            sys.exit(2)
        # Protect against very large inputs
        if len(raw) > 256 * 1024:
            print(json.dumps({"error": "input too large"}))
            sys.exit(4)
        payload = json.loads(raw)
        req_id = payload.get("id")
        action = payload.get("action")
        params = payload.get("params") or {}
        if action not in _ALLOWED:
            print(json.dumps({"id": req_id, "error": f"action not allowed: {action}"}))
            sys.exit(3)
        handler = _ALLOWED[action]
        result = handler(params)
        print(json.dumps({"id": req_id, "result": result}))
        sys.exit(0)
    except Exception as e:
        try:
            print(json.dumps({"error": str(e)}))
        except Exception:
            print(json.dumps({"error": "worker failure"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
