"""A2A — Agent-to-Agent session routing for Intelli Gateway.

Allows the agent (or the user) to dispatch a sub-task to a *different persona*,
running a fully isolated tool-loop under that persona's system prompt.  Results
are stored and retrievable asynchronously so the originating chat is never
blocked.

Architecture
------------
Tasks are tracked in a lightweight in-memory dict + persisted to a JSONL file
at ``~/.intelli/a2a_tasks.jsonl``.

Each task transitions through:

    pending → running → done | error

A background thread executes the destination persona's tool-loop and writes the
result back into the task record.

Public API
----------
    submit(from_persona, to_persona, task, context)  → dict   (task record)
    get_task(task_id)                                → Optional[dict]
    list_tasks(limit)                                → list[dict]
    cancel(task_id)                                  → bool
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TASKS_FILE = Path(os.environ.get('INTELLI_A2A_TASKS_FILE', Path.home() / '.intelli' / 'a2a_tasks.jsonl'))
_MAX_IN_MEMORY = int(os.environ.get('INTELLI_A2A_MAX_TASKS', '200'))
_WORKER_TIMEOUT = int(os.environ.get('INTELLI_A2A_TIMEOUT', '120'))  # seconds per task

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_tasks: Dict[str, Dict[str, Any]] = {}
_order: Deque[str] = deque(maxlen=_MAX_IN_MEMORY)   # newest last
_cancel_flags: Dict[str, threading.Event] = {}


def _persist(record: Dict[str, Any]) -> None:
    try:
        _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _TASKS_FILE.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record) + '\n')
    except Exception:
        pass


def _ts() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

def _run_task(task_id: str) -> None:
    """Execute the A2A task in a background thread."""
    with _lock:
        record = _tasks.get(task_id)
    if record is None:
        return

    cancel_event = _cancel_flags.get(task_id, threading.Event())

    with _lock:
        _tasks[task_id]['status'] = 'running'
        _tasks[task_id]['started_at'] = _ts()

    try:
        # Import gateway modules
        gw = os.path.dirname(__file__)
        if gw not in sys.path:
            sys.path.insert(0, gw)

        from workspace_manager import load_agents_md
        from providers.adapters import get_adapter, available_providers
        from tools.tool_runner import run_tool_loop, build_tool_system_block

        if cancel_event.is_set():
            raise InterruptedError('Task was cancelled before it started.')

        # Resolve target persona system prompt
        agents_md = load_agents_md()
        to_persona = record['to_persona']
        persona_prompt = _extract_persona_prompt(agents_md, to_persona)

        # Build messages
        messages: List[Dict[str, str]] = []
        if record.get('context'):
            messages.append({'role': 'user', 'content': f'Context:\n{record["context"]}'})
            messages.append({'role': 'assistant', 'content': 'Understood.'})
        messages.append({'role': 'user', 'content': record['task']})

        # Determine provider
        prov = available_providers()[0] if available_providers() else 'openai'
        adpt = get_adapter(prov)

        system = '\n\n'.join(filter(None, [persona_prompt, build_tool_system_block()]))

        # Run tool loop
        result = run_tool_loop(
            adpt, messages,
            temperature=0.7, max_tokens=2048,
            model='', system=system,
            max_rounds=5,
        )
        content = result.get('content', '').strip() or '(no response)'

        with _lock:
            _tasks[task_id]['status'] = 'done'
            _tasks[task_id]['result'] = content
            _tasks[task_id]['finished_at'] = _ts()

        _persist({**_tasks[task_id]})

    except InterruptedError as exc:
        with _lock:
            _tasks[task_id]['status'] = 'cancelled'
            _tasks[task_id]['error'] = str(exc)
            _tasks[task_id]['finished_at'] = _ts()
        _persist({**_tasks[task_id]})

    except Exception as exc:
        with _lock:
            _tasks[task_id]['status'] = 'error'
            _tasks[task_id]['error'] = str(exc)
            _tasks[task_id]['finished_at'] = _ts()
        _persist({**_tasks[task_id]})

    finally:
        with _lock:
            _cancel_flags.pop(task_id, None)


def _extract_persona_prompt(agents_md: str, persona_name: str) -> str:
    """Extract the system prompt section for *persona_name* from AGENTS.md.

    Falls back to a generic researcher prompt if the persona isn't found.
    """
    lines = agents_md.splitlines()
    in_section = False
    section_lines: List[str] = []
    search = persona_name.lower()

    for line in lines:
        if line.startswith('## ') and search in line.lower():
            in_section = True
            continue
        if in_section:
            if line.startswith('## '):
                break
            section_lines.append(line)

    if section_lines:
        return '\n'.join(section_lines).strip()

    # Generic fallback
    return (
        f'You are {persona_name}, a specialised AI assistant. '
        'Complete the assigned task thoroughly and return a clear, structured result.'
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit(
    from_persona: str,
    to_persona: str,
    task: str,
    context: str = '',
) -> Dict[str, Any]:
    """Submit a task to *to_persona* and return the task record immediately.

    Execution is asynchronous — poll :func:`get_task` for results.

    Parameters
    ----------
    from_persona:
        Name of the originating persona / caller (for audit purposes).
    to_persona:
        Name of the target persona that should handle the task.
    task:
        Natural-language task description.
    context:
        Optional background context for the target persona.

    Returns a task record dict with ``id``, ``status`` = 'pending', etc.
    """
    task_id = str(uuid.uuid4())
    record: Dict[str, Any] = {
        'id':           task_id,
        'from_persona': from_persona,
        'to_persona':   to_persona,
        'task':         task,
        'context':      context,
        'status':       'pending',
        'result':       None,
        'error':        None,
        'created_at':   _ts(),
        'started_at':   None,
        'finished_at':  None,
    }

    cancel_ev = threading.Event()
    with _lock:
        if len(_order) >= _MAX_IN_MEMORY:
            oldest = _order[0]
            _tasks.pop(oldest, None)
        _tasks[task_id] = record
        _order.append(task_id)
        _cancel_flags[task_id] = cancel_ev

    worker = threading.Thread(target=_run_task, args=(task_id,), daemon=True, name=f'a2a-{task_id[:8]}')
    worker.start()

    return dict(record)


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Return the task record for *task_id*, or None."""
    with _lock:
        rec = _tasks.get(task_id)
        return dict(rec) if rec else None


def list_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent tasks, newest first."""
    limit = max(1, min(limit, _MAX_IN_MEMORY))
    with _lock:
        ids = list(reversed(list(_order)))[:limit]
        return [dict(_tasks[i]) for i in ids if i in _tasks]


def cancel(task_id: str) -> bool:
    """Request cancellation of a pending or running task.

    Returns True if the task existed and the cancel flag was set.
    """
    with _lock:
        ev = _cancel_flags.get(task_id)
        if ev is None:
            return False
        ev.set()
        if task_id in _tasks and _tasks[task_id]['status'] == 'pending':
            _tasks[task_id]['status'] = 'cancelled'
            _tasks[task_id]['finished_at'] = _ts()
    return True
