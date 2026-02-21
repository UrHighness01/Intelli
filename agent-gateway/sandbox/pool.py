"""Persistent worker pool with line-oriented IPC.

Maintains a pool of long-lived subprocess workers.  Each worker reads requests
and writes responses as newline-delimited JSON over stdin/stdout, avoiding the
overhead of spawning a new process per request.

The pool handles:
  - Pool size configured via SANDBOX_POOL_SIZE (default 2).
  - Per-worker health tracking and automatic restart with exponential backoff.
  - Thread-safe checkout/return of workers via a queue.
  - Graceful shutdown on pool.shutdown().

IPC wire format (newline-delimited JSON):
  Request:  {"id": "<uuid>", "action": "<action>", "params": {…}}\n
  Response: {"id": "<uuid>", "result": {…}}\n   or   {"id": "<uuid>", "error": "<msg>"}\n
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, Optional

WORKER_PATH = os.path.join(os.path.dirname(__file__), 'worker_persistent.py')
POOL_SIZE = int(os.environ.get('SANDBOX_POOL_SIZE', '2'))
WORKER_TIMEOUT = int(os.environ.get('SANDBOX_WORKER_TIMEOUT', '5'))


class WorkerProcess:
    """A single persistent worker subprocess."""

    def __init__(self, worker_path: str):
        self._path = worker_path
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._fail_count = 0
        self._spawn()

    def _spawn(self):
        self._proc = subprocess.Popen(
            [sys.executable, '-u', self._path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def restart(self):
        try:
            if self._proc:
                self._proc.kill()
        except Exception:
            pass
        self._fail_count += 1
        backoff = min(2 ** self._fail_count, 30)
        time.sleep(backoff)
        self._spawn()

    def call(self, action: str, params: Dict[str, Any], timeout: int = WORKER_TIMEOUT) -> Dict[str, Any]:
        with self._lock:
            if not self.alive():
                self.restart()
            req_id = str(uuid.uuid4())
            req = json.dumps({'id': req_id, 'action': action, 'params': params}) + '\n'
            try:
                self._proc.stdin.write(req)  # type: ignore
                self._proc.stdin.flush()  # type: ignore
            except Exception as e:
                self.restart()
                raise RuntimeError(f'worker stdin write failed: {e}')

            # read response with timeout using a thread
            result_holder: list = []
            error_holder: list = []

            def _read():
                try:
                    line = self._proc.stdout.readline()  # type: ignore
                    result_holder.append(line)
                except Exception as e:
                    error_holder.append(str(e))

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                self.restart()
                raise RuntimeError('worker timeout')
            if error_holder:
                self.restart()
                raise RuntimeError(f'worker read error: {error_holder[0]}')
            if not result_holder or not result_holder[0].strip():
                self.restart()
                raise RuntimeError('empty worker response')

            data = json.loads(result_holder[0])
            if data.get('id') != req_id:
                self.restart()
                raise RuntimeError('response id mismatch')
            if 'error' in data:
                raise RuntimeError(data['error'])
            return data.get('result', data)


class WorkerPool:
    """Thread-safe pool of WorkerProcess instances."""

    def __init__(self, size: int = POOL_SIZE, worker_path: str = WORKER_PATH):
        self._path = worker_path
        self._pool: queue.Queue = queue.Queue(maxsize=size)
        self._size = size
        self._lock = threading.Lock()
        self._running = True
        self._workers: list[WorkerProcess] = []
        if os.path.exists(worker_path):
            for _ in range(size):
                w = WorkerProcess(worker_path)
                self._workers.append(w)
                self._pool.put(w)

    @property
    def available(self) -> bool:
        return os.path.exists(self._path) and self._size > 0

    def execute(self, action: str, params: Dict[str, Any], timeout: int = WORKER_TIMEOUT) -> Dict[str, Any]:
        if not self.available:
            raise RuntimeError('worker pool not available (persistent worker not found)')
        try:
            worker = self._pool.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError('all workers busy')
        try:
            return worker.call(action, params, timeout=timeout)
        finally:
            self._pool.put(worker)

    def health(self) -> Dict[str, Any]:
        alive = sum(1 for w in self._workers if w.alive())
        return {'size': self._size, 'alive': alive, 'available': self.available}

    def shutdown(self):
        self._running = False
        for w in self._workers:
            try:
                if w._proc:
                    w._proc.kill()
            except Exception:
                pass


# Module-level singleton
_pool: Optional[WorkerPool] = None
_pool_lock = threading.Lock()


def get_pool() -> WorkerPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = WorkerPool()
    return _pool
