"""Sandbox proxy scaffold.

This module provides a minimal, safe sandbox proxy for tool execution.
It does NOT execute arbitrary code. Instead, it validates requested actions
against a whitelist and returns controlled, auditable responses. The real
sandbox implementation should run in a separate process with OS-level
capabilities and strict resource limits.
"""
from typing import Any, Dict, Optional
import json
import subprocess
import sys
import os
from shutil import which


class SandboxError(Exception):
    pass


class SandboxProxy:
    """A very small scaffolded sandbox proxy.

    Usage:
      proxy = SandboxProxy()
      proxy.execute('noop', {})
    """

    # allowed action -> handler name
    _ALLOWED = {
        'noop': '_handle_noop',
        'echo': '_handle_echo',
    }

    def __init__(self, audit_callback=None):
        # audit_callback(event_name: str, details: dict)
        self.audit = audit_callback
        # If WORKER_PATH env var is provided, try to use subprocess-based worker
        self._use_worker = False
        self._worker_path: Optional[str] = None
        wp = os.environ.get('SANDBOX_WORKER_PATH')
        if wp:
            # allow either absolute path or module-relative path under sandbox/
            if os.path.isabs(wp) and os.path.exists(wp):
                self._worker_path = wp
                self._use_worker = True
            else:
                # try workspace-relative path
                candidate = os.path.join(os.getcwd(), wp)
                if os.path.exists(candidate):
                    self._worker_path = candidate
                    self._use_worker = True
        # fallback: look for bundled worker next to this module
        if not self._use_worker:
            bundled = os.path.join(os.path.dirname(__file__), 'worker.py')
            if os.path.exists(bundled):
                self._worker_path = bundled
                self._use_worker = True

    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate action and run handler.

        Returns a dict result. Raises SandboxError for disallowed actions.
        """
        if action not in self._ALLOWED:
            raise SandboxError(f"action not allowed: {action}")

        handler_name = self._ALLOWED[action]
        handler = getattr(self, handler_name, None)
        if not handler:
            raise SandboxError(f"no handler for action: {action}")

        # Audit request
        try:
            if self.audit:
                self.audit('execute_request', {'action': action, 'params': params})
        except Exception:
            pass

        result = handler(params)

        try:
            if self.audit:
                self.audit('execute_result', {'action': action, 'result': result})
        except Exception:
            pass

        return result

    def _handle_noop(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {'status': 'ok', 'message': 'noop'}

    def _handle_echo(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # echo back a JSON-serializable subset of params
        safe = {}
        for k, v in params.items():
            try:
                json.dumps(v)
                safe[k] = v
            except Exception:
                safe[k] = str(v)
        return {'status': 'ok', 'echo': safe}

    def _execute_worker(self, action: str, params: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
        """Spawn the sandbox worker as a subprocess and send a single JSON request.

        The worker receives a JSON object on stdin and writes a JSON object to stdout.
        """
        if not self._worker_path:
            raise SandboxError('no worker configured')

        if not os.path.exists(self._worker_path):
            raise SandboxError('worker binary not found')

        import uuid

        payload = {'id': str(uuid.uuid4()), 'action': action, 'params': params}
        # enforce max payload size (avoid spawning worker with huge stdin)
        inp = json.dumps(payload)
        if len(inp) > 256 * 1024:
            raise SandboxError('payload too large')
        proc = subprocess.Popen([sys.executable, self._worker_path],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True)
        try:
            out, err = proc.communicate(inp, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise SandboxError('worker timeout')

        if proc.returncode != 0:
            # include stderr message for diagnostics but avoid leaking sensitive data
            raise SandboxError(f'worker error (rc={proc.returncode}): {err.strip()}')

        try:
            data = json.loads(out)
        except Exception as e:
            raise SandboxError(f'bad worker output: {e}')

        # verify response id matches
        resp_id = data.get('id')
        if resp_id and resp_id != payload['id']:
            raise SandboxError('mismatched response id from worker')

        if 'error' in data:
            raise SandboxError(data['error'])

        return data.get('result', data)


def run_demo():
    proxy = SandboxProxy()
    print(proxy.execute('noop', {}))
    print(proxy.execute('echo', {'msg': 'hello'}))


if __name__ == '__main__':
    run_demo()
