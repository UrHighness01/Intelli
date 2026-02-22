"""
tab_snapshot.py — In-memory store for the active browser tab's HTML snapshot.

The Electron browser shell pushes a snapshot (url, title, html) to
``PUT /tab/snapshot`` immediately after each page finishes loading.  Agents
can then retrieve the current page content via ``GET /tab/snapshot`` or use
the ``browser.tab_snapshot`` tool, enabling them to read and reason about
whatever the user is looking at without needing direct DOM access.

Thread-safety: a plain ``threading.Lock`` guards all reads and writes because
FastAPI may call these helpers from worker threads.
"""
import threading
from datetime import datetime, timezone

_lock     = threading.Lock()
_snapshot: dict = {}     # keys: url, title, html, timestamp


def set_snapshot(url: str, title: str, html: str) -> None:
    """Store a new snapshot, replacing any previous one."""
    with _lock:
        _snapshot.clear()
        _snapshot.update({
            'url':       url,
            'title':     title,
            'html':      html,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'length':    len(html),
        })


def get_snapshot() -> dict:
    """Return a copy of the current snapshot (empty dict if none yet)."""
    with _lock:
        return dict(_snapshot)


def clear_snapshot() -> None:
    """Discard the stored snapshot."""
    with _lock:
        _snapshot.clear()
