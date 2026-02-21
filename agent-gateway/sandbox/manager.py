"""Lightweight WorkerManager for sandbox worker lifecycle and health checks.

This module provides a minimal manager that can perform health checks against
the subprocess worker and perform simple restart/backoff logic when a
transient failure is detected. It intentionally does not implement a
persistent worker pool; that is a future enhancement.
"""
import os
import subprocess
import sys
import json
import time
from typing import Optional


class WorkerManager:
    """Manage and health-check the sandbox worker binary.

    Usage:
      mgr = WorkerManager()
      ok = mgr.check_health()
    """

    def __init__(self, worker_path: Optional[str] = None, timeout: int = 3):
        self.timeout = timeout
        if worker_path:
            self.worker_path = worker_path
        else:
            # default to bundled worker next to module
            self.worker_path = os.path.join(os.path.dirname(__file__), 'worker.py')

    def _worker_exists(self) -> bool:
        return bool(self.worker_path and os.path.exists(self.worker_path))

    def _spawn_and_call(self, action: str, params: dict) -> dict:
        payload = {'id': 'health-check', 'action': action, 'params': params}
        proc = subprocess.Popen([sys.executable, self.worker_path],
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True)
        try:
            out, err = proc.communicate(json.dumps(payload), timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError('worker timeout')

        if proc.returncode != 0:
            raise RuntimeError(f'worker error rc={proc.returncode}: {err.strip()}')

        try:
            data = json.loads(out)
        except Exception as e:
            raise RuntimeError(f'bad worker output: {e}')

        return data

    def check_health(self, attempts: int = 2, backoff: float = 0.5) -> bool:
        """Check worker health by issuing a noop request.

        Returns True if a valid response is received within the allowed attempts.
        """
        if not self._worker_exists():
            return False

        for i in range(attempts):
            try:
                data = self._spawn_and_call('noop', {})
                # expect id to match or a result dict
                if data.get('id') in (None, 'health-check') or 'result' in data:
                    return True
            except Exception:
                time.sleep(backoff)
                backoff *= 2
        return False
