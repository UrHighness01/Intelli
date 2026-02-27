"""Per-agent persistent key-value memory store.

Each agent gets an isolated JSON file on disk under MEMORY_DIR.  All
operations are thread-safe.  The agent_id is sanitised (only ``[A-Za-z0-9_-]``
allowed) to prevent path traversal.

The store supports optional per-key TTL (time-to-live).  When a TTL is set,
expired keys are silently pruned on the next read or list operation.

Environment variables
---------------------
AGENT_GATEWAY_MEMORY_DIR
    Directory for agent memory files.
    Default: ``agent-gateway/agent_memories/``

API
---
    import agent_memory

    agent_memory.memory_set('my-agent', 'key', 'value')
    agent_memory.memory_set('my-agent', 'session', 'tok', ttl_seconds=3600)
    val  = agent_memory.memory_get('my-agent', 'key')     # → 'value' | None
    all_ = agent_memory.memory_list('my-agent')           # → {'key': 'value', ...}
    ok   = agent_memory.memory_delete('my-agent', 'key')  # → bool
    n    = agent_memory.memory_clear('my-agent')          # → int (keys removed)
    n    = agent_memory.memory_prune('my-agent')          # → int (expired keys)
    agents = agent_memory.list_agents()                   # → ['agent1', ...]
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MEMORY_DIR = Path(
    os.environ.get(
        'AGENT_GATEWAY_MEMORY_DIR',
        str(Path(__file__).with_name('agent_memories')),
    )
)

_SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal storage format
# ---------------------------------------------------------------------------
# Values are stored in one of two forms:
#   - bare JSON value  (legacy / no TTL):  stored directly
#   - wrapped dict:  {"__v": <value>, "__exp": <unix_float>}  (with TTL)
#
# _unwrap() converts either form to (value, expires_at | None)
# _wrap()   produces the on-disk representation

_WRAP_KEY = '__v'
_EXP_KEY  = '__exp'


def _is_wrapped(raw: Any) -> bool:
    return isinstance(raw, dict) and _WRAP_KEY in raw


def _unwrap(raw: Any) -> Tuple[Any, Optional[float]]:
    """Return (value, expires_at_unix | None)."""
    if _is_wrapped(raw):
        return raw[_WRAP_KEY], raw.get(_EXP_KEY)
    return raw, None


def _wrap(value: Any, ttl_seconds: Optional[float]) -> Any:
    """Return the on-disk representation of *value* with optional TTL."""
    if ttl_seconds is None:
        return value
    return {_WRAP_KEY: value, _EXP_KEY: time.time() + ttl_seconds}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_id(agent_id: str) -> None:
    if not _SAFE_ID.match(agent_id):
        raise ValueError(
            f'agent_id must match [A-Za-z0-9_-]{{1,128}}, got: {agent_id!r}'
        )


def _agent_path(agent_id: str) -> Path:
    _validate_id(agent_id)
    # Use os.path.realpath (CodeQL PathNormalization) + startswith (CodeQL
    # SafeAccessCheck) for a taint barrier that satisfies py/path-injection.
    base = os.path.realpath(str(MEMORY_DIR))
    joined = os.path.realpath(os.path.join(str(MEMORY_DIR), agent_id + '.json'))
    if not joined.startswith(base + os.sep):
        raise PermissionError(f'agent_id {agent_id!r} escapes memory directory')
    return Path(joined)


def _load(agent_id: str) -> Dict[str, Any]:
    path = _agent_path(agent_id)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(agent_id: str, data: Dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _agent_path(agent_id).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
    )


def _load_active(agent_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load raw data and split into (active_user_values, raw_data_for_save).

    Returns a tuple of:
    - ``live``:  {key: user_value}  (only non-expired entries)
    - ``raw``:   {key: raw_entry}   (only non-expired entries, ready to save)
    """
    now = time.time()
    raw_all = _load(agent_id)
    live: Dict[str, Any] = {}
    raw: Dict[str, Any] = {}
    for k, raw_v in raw_all.items():
        v, exp = _unwrap(raw_v)
        if exp is not None and exp <= now:
            continue  # expired — drop silently
        live[k] = v
        raw[k] = raw_v
    return live, raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def memory_get(agent_id: str, key: str) -> Optional[Any]:
    """Return the value stored for *key* under *agent_id*, or ``None``.

    Expired keys are treated as absent and pruned automatically.
    """
    with _lock:
        live, raw = _load_active(agent_id)
        if key not in live:
            return None
        # Persist pruned state if we dropped anything
        raw_all = _load(agent_id)
        if len(raw) < len(raw_all):
            _save(agent_id, raw)
        return live[key]


def memory_set(agent_id: str, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
    """Upsert *key* → *value* in the agent's memory.

    Parameters
    ----------
    ttl_seconds:
        If given, the key will be treated as absent (and pruned) after this
        many seconds.  ``None`` means no expiry.
    """
    with _lock:
        raw = _load(agent_id)  # preserve all raw entries; reads filter expired via _load_active
        raw[key] = _wrap(value, ttl_seconds)
        _save(agent_id, raw)


def memory_delete(agent_id: str, key: str) -> bool:
    """Remove *key* from the agent's memory.  Returns ``True`` if it existed."""
    with _lock:
        live, raw = _load_active(agent_id)
        if key not in live:
            return False
        del raw[key]
        _save(agent_id, raw)
        return True


def memory_list(agent_id: str) -> Dict[str, Any]:
    """Return the full {key: value} dict for *agent_id* (expired keys excluded)."""
    with _lock:
        live, raw = _load_active(agent_id)
        # Persist pruned state
        raw_all = _load(agent_id)
        if len(raw) < len(raw_all):
            _save(agent_id, raw)
        return live


def memory_clear(agent_id: str) -> int:
    """Erase all keys for *agent_id* (including expired).  Returns count removed."""
    with _lock:
        data = _load(agent_id)
        count = len(data)
        _save(agent_id, {})
        return count


def memory_prune(agent_id: str) -> int:
    """Remove only expired keys for *agent_id*.  Returns the count pruned."""
    with _lock:
        raw_all = _load(agent_id)
        _, raw_live = _load_active(agent_id)
        pruned = len(raw_all) - len(raw_live)
        if pruned:
            _save(agent_id, raw_live)
        return pruned


def memory_get_meta(agent_id: str, key: str) -> Optional[Dict[str, Any]]:
    """Return ``{value, expires_at}`` for *key*, or ``None`` if absent/expired."""
    with _lock:
        live, raw = _load_active(agent_id)
        if key not in live:
            return None
        raw_v = raw[key]
        _, exp = _unwrap(raw_v)
        return {'value': live[key], 'expires_at': exp}


def list_agents() -> List[str]:
    """Return a sorted list of all agent IDs that have at least one key."""
    try:
        return sorted(p.stem for p in MEMORY_DIR.glob('*.json'))
    except FileNotFoundError:
        return []


def export_all() -> Dict[str, Any]:
    """Return a full snapshot of all agents' live (non-expired) memory.

    Returns a dict with the structure::

        {
          "agents":       {"agent-id": {"key": value, ...}, ...},
          "agent_count":  int,
          "key_count":    int,
          "exported_at":  "2025-01-01T00:00:00Z",
        }

    Suitable for passing back to :func:`import_all` to restore a backup.
    """
    agents = list_agents()
    snapshot: Dict[str, Dict[str, Any]] = {}
    for agent_id in agents:
        snapshot[agent_id] = memory_list(agent_id)  # thread-safe per-call
    total_keys = sum(len(v) for v in snapshot.values())
    return {
        'agents': snapshot,
        'agent_count': len(agents),
        'key_count': total_keys,
        'exported_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }


def import_all(data: Dict[str, Dict[str, Any]], merge: bool = True) -> Dict[str, int]:
    """Import agent memories from a backup dict.

    Parameters
    ----------
    data:
        Mapping of ``agent_id -> {key: value}`` (bare values without TTL
        wrappers; values are stored without expiry).
    merge:
        If ``True`` (default) the imported keys are *merged* into the
        existing memory for each agent — existing keys not present in *data*
        are kept, and keys present in *data* overwrite the stored value.
        If ``False``, the entire memory for each imported agent is replaced.

    Returns
    -------
    dict
        ``{"imported_agents": N, "imported_keys": M}``

    Raises
    ------
    ValueError
        If any agent_id fails the safe-ID check.
    """
    imported_agents = 0
    imported_keys = 0
    for agent_id, keys in data.items():
        _validate_id(agent_id)          # raises ValueError on bad ID
        if not isinstance(keys, dict):
            continue
        if not keys:                    # nothing to import for this agent
            continue
        with _lock:
            if merge:
                _, existing_raw = _load_active(agent_id)
                for key, value in keys.items():
                    existing_raw[key] = value   # store bare (no TTL)
                _save(agent_id, existing_raw)
            else:
                _save(agent_id, dict(keys))
        imported_agents += 1
        imported_keys += len(keys)
    return {'imported_agents': imported_agents, 'imported_keys': imported_keys}
