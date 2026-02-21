"""Agent task scheduler.

Allows recurring tool-call tasks to be registered and executed automatically
by a background thread at a configurable interval.

Each *scheduled task* describes a tool call (tool name + args) that should be
run every ``interval_seconds`` seconds.  A lightweight background daemon thread
wakes up every second and fires any tasks whose ``next_run_at`` has passed.

Public API
----------
add_task(name, tool, args, interval_seconds, enabled=True)  -> dict
list_tasks()                                                 -> list[dict]
get_task(task_id)                                            -> dict | None
delete_task(task_id)                                         -> bool
set_enabled(task_id, enabled)                                -> bool
set_executor(fn)                                             -> None
    Register the callable used to actually execute tool calls.  Expected
    signature: ``fn({"tool": str, "args": dict}) -> dict``.  Must be called
    once (from app startup) before any tasks can actually run.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import metrics as _metrics

SCHEDULE_PATH = Path(__file__).with_name('schedule.json')

# Maximum run-history records kept per task (in-memory only, not persisted)
_HISTORY_MAX = 50

_lock: threading.Lock = threading.Lock()
_tasks: Dict[str, Dict[str, Any]] = {}   # task_id -> task dict
_history: Dict[str, Deque[Dict[str, Any]]] = {}  # task_id -> run records
_executor: Optional[Callable[[Dict], Dict]] = None
_loaded: bool = False


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> None:
    global _loaded, _tasks
    if _loaded:
        return
    try:
        if SCHEDULE_PATH.exists():
            raw = json.loads(SCHEDULE_PATH.read_text(encoding='utf-8'))
            _tasks = {t['id']: t for t in raw.get('tasks', [])}
    except Exception:
        _tasks = {}
    _loaded = True


def _save() -> None:
    try:
        SCHEDULE_PATH.write_text(
            json.dumps({'tasks': list(_tasks.values())}, indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_executor(fn: Callable[[Dict], Dict]) -> None:
    """Register the tool-call execution callback (typically supervisor.process_call)."""
    global _executor
    _executor = fn


def add_task(
    name: str,
    tool: str,
    args: Dict[str, Any],
    interval_seconds: int,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Create a new scheduled task and return its full dict.

    Raises ``ValueError`` if *interval_seconds* is < 1 or *name* is empty.
    """
    if not name or not name.strip():
        raise ValueError('name must not be empty')
    if interval_seconds < 1:
        raise ValueError('interval_seconds must be >= 1')
    if not tool or not tool.strip():
        raise ValueError('tool must not be empty')

    with _lock:
        _load()
        task_id = secrets.token_hex(8)
        now = time.time()
        task: Dict[str, Any] = {
            'id': task_id,
            'name': name,
            'tool': tool,
            'args': args if args is not None else {},
            'interval_seconds': interval_seconds,
            'enabled': bool(enabled),
            'created_at': _now_iso(),
            'last_run_at': None,
            'next_run_at': now + interval_seconds,  # unix timestamp
            'run_count': 0,
            'last_result': None,
            'last_error': None,
        }
        _tasks[task_id] = task
        _save()
        _metrics.gauge('scheduler_tasks_total', len(_tasks))
        return _task_view(task)


def list_tasks() -> List[Dict[str, Any]]:
    """Return all scheduled tasks (serialisable view)."""
    with _lock:
        _load()
        return [_task_view(t) for t in _tasks.values()]


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Return a single task or ``None`` if not found."""
    with _lock:
        _load()
        t = _tasks.get(task_id)
        return _task_view(t) if t else None


def delete_task(task_id: str) -> bool:
    """Remove a task. Returns ``False`` if task_id is not found."""
    with _lock:
        _load()
        if task_id not in _tasks:
            return False
        del _tasks[task_id]
        _history.pop(task_id, None)
        _save()
        _metrics.gauge('scheduler_tasks_total', len(_tasks))
        return True


def get_history(task_id: str, limit: int = 50) -> Optional[List[Dict[str, Any]]]:
    """Return the most-recent run records for *task_id*, newest first.

    Returns ``None`` if the task does not exist. Returns an empty list if
    the task exists but has never run.
    """
    with _lock:
        _load()
        if task_id not in _tasks:
            return None
        records = list(_history.get(task_id, deque()))
        # newest first, capped by limit
        return records[-min(len(records), limit):][::-1]


def trigger_task(task_id: str) -> bool:
    """Immediately trigger a task on the next scheduler tick (set next_run_at to now-1).

    Returns ``False`` if the task is not found.
    """
    with _lock:
        _load()
        t = _tasks.get(task_id)
        if t is None:
            return False
        t['next_run_at'] = time.time() - 1
        _save()
        return True


def set_enabled(task_id: str, enabled: bool) -> bool:
    """Enable or disable a task. Returns ``False`` if not found."""
    with _lock:
        _load()
        t = _tasks.get(task_id)
        if t is None:
            return False
        t['enabled'] = bool(enabled)
        _save()
        return True


def update_task(task_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Update mutable fields (name, args, interval_seconds, enabled).

    Returns the updated task view or ``None`` if not found.
    Raises ``ValueError`` for invalid values.
    """
    allowed = {'name', 'args', 'interval_seconds', 'enabled'}
    bad = set(kwargs) - allowed
    if bad:
        raise ValueError(f'unknown fields: {bad}')

    with _lock:
        _load()
        t = _tasks.get(task_id)
        if t is None:
            return None
        if 'name' in kwargs:
            if not kwargs['name'] or not str(kwargs['name']).strip():
                raise ValueError('name must not be empty')
            t['name'] = str(kwargs['name'])
        if 'args' in kwargs:
            t['args'] = kwargs['args'] if kwargs['args'] is not None else {}
        if 'interval_seconds' in kwargs:
            iv = int(kwargs['interval_seconds'])
            if iv < 1:
                raise ValueError('interval_seconds must be >= 1')
            t['interval_seconds'] = iv
        if 'enabled' in kwargs:
            t['enabled'] = bool(kwargs['enabled'])
        _save()
        return _task_view(t)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _task_view(t: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a task with ``next_run_at`` formatted as ISO string."""
    v = dict(t)
    nra = v.get('next_run_at')
    if isinstance(nra, (int, float)):
        v['next_run_at'] = datetime.fromtimestamp(nra, tz=timezone.utc).isoformat()
    return v


def _run_task(t: Dict[str, Any]) -> None:
    """Execute a single task in the calling thread (called from the scheduler loop)."""
    fn = _executor
    if fn is None:
        return
    payload = {'tool': t['tool'], 'args': t.get('args', {})}
    task_label = {'task': t['name']}
    _start = time.time()
    ok = True
    result = None
    error: Optional[str] = None
    try:
        result = fn(payload)
        t['last_result'] = result
        t['last_error'] = None
        _metrics.inc('scheduler_runs_total', labels=task_label)
    except Exception as exc:
        ok = False
        error = str(exc)
        t['last_result'] = None
        t['last_error'] = error
        _metrics.inc('scheduler_runs_total', labels=task_label)
        _metrics.inc('scheduler_errors_total', labels=task_label)
    finally:
        duration = time.time() - _start
        _metrics.observe('scheduler_run_duration_seconds', duration, labels=task_label)
        now = time.time()
        t['last_run_at'] = _now_iso()
        t['next_run_at'] = now + t['interval_seconds']
        t['run_count'] = t.get('run_count', 0) + 1
    # Record in per-task history (outside the finally so it runs cleanly)
    record: Dict[str, Any] = {
        'run': t['run_count'],
        'timestamp': t['last_run_at'],
        'duration_seconds': round(duration, 4),
        'ok': ok,
        'result': result,
        'error': error,
    }
    task_id = t['id']
    if task_id not in _history:
        _history[task_id] = deque(maxlen=_HISTORY_MAX)
    _history[task_id].append(record)
    _save()


# ---------------------------------------------------------------------------
# Background scheduler thread
# ---------------------------------------------------------------------------

def _scheduler_loop() -> None:
    while True:
        try:
            time.sleep(1)
            with _lock:
                _load()
                now = time.time()
                due = [
                    t for t in _tasks.values()
                    if t.get('enabled') and isinstance(t.get('next_run_at'), (int, float))
                    and t['next_run_at'] <= now
                ]
            # Run outside the lock (each execution may take time)
            for t in due:
                _run_task(t)
        except Exception:
            pass  # never crash the daemon


_scheduler_thread = threading.Thread(
    target=_scheduler_loop,
    name='intelli-scheduler',
    daemon=True,
)
_scheduler_thread.start()
