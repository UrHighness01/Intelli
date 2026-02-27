"""
addons.py — Lightweight addon manager for the Intelli browser shell.

Addons are small JavaScript snippets that agents can write and activate to
extend the behaviour of the active browser tab — similar in spirit to browser
extensions.  Unlike traditional extensions they live entirely within the
Intelli process and are injected at runtime via Electron's
``webContents.executeJavaScript``.

Storage
-------
Addons are persisted as JSON in ``<gateway_dir>/addons.json``.  The file is
created automatically on first write.

Injection queue
---------------
When an addon is activated, its ``code_js`` string is pushed onto an in-memory
FIFO queue.  The browser chrome polls ``GET /tab/inject-queue`` every few
seconds and executes each pending snippet inside the active BrowserView.
The queue is auto-purged after each poll so scripts run exactly once per
activation.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Persistence ───────────────────────────────────────────────────────────────
_DATA_FILE = Path(__file__).parent / 'addons.json'
_lock      = threading.Lock()

def _load() -> dict:
    if _DATA_FILE.exists():
        try:
            return json.loads(_DATA_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def _save(store: dict) -> None:
    _DATA_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2),
                          encoding='utf-8')

# ── Injection queue ───────────────────────────────────────────────────────────
_inject_queue: list[dict] = []   # [{ name, code_js }]

def _push_inject(name: str, code_js: str) -> None:
    _inject_queue.append({'name': name, 'code_js': code_js})

def pop_inject_queue() -> list[dict]:
    """Return and drain the entire injection queue."""
    items = list(_inject_queue)
    _inject_queue.clear()
    return items

def get_active_addons() -> list[dict]:
    """Return all currently active addons (name + code_js) without draining anything."""
    with _lock:
        store = _load()
    return [{'name': a['name'], 'code_js': a['code_js']}
            for a in store.values() if a.get('active')]

# ── CRUD helpers ──────────────────────────────────────────────────────────────

def list_addons() -> list[dict]:
    with _lock:
        store = _load()
    return list(store.values())


def get_addon(name: str) -> Optional[dict]:
    with _lock:
        return _load().get(name)


def create_addon(name: str, description: str, code_js: str) -> dict:
    with _lock:
        store = _load()
        if name in store:
            raise ValueError(f"Addon '{name}' already exists")
        addon = {
            'name':        name,
            'description': description,
            'code_js':     code_js,
            'active':      False,
            'created_at':  datetime.now(timezone.utc).isoformat(),
            'updated_at':  datetime.now(timezone.utc).isoformat(),
        }
        store[name] = addon
        _save(store)
        return addon


def update_addon(name: str, description: Optional[str] = None,
                 code_js: Optional[str] = None) -> dict:
    with _lock:
        store = _load()
        if name not in store:
            raise KeyError(name)
        if description is not None:
            store[name]['description'] = description
        if code_js is not None:
            store[name]['code_js'] = code_js
        store[name]['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(store)
        return store[name]


def delete_addon(name: str) -> None:
    with _lock:
        store = _load()
        if name not in store:
            raise KeyError(name)
        del store[name]
        _save(store)


def activate_addon(name: str) -> dict:
    with _lock:
        store = _load()
        if name not in store:
            raise KeyError(name)
        store[name]['active'] = True
        store[name]['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(store)
        addon = store[name]
    # Queue for injection into active tab
    _push_inject(name, addon['code_js'])
    return addon


def deactivate_addon(name: str) -> dict:
    with _lock:
        store = _load()
        if name not in store:
            raise KeyError(name)
        store[name]['active'] = False
        store[name]['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save(store)
        return store[name]
