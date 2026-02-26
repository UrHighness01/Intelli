"""
canvas_manager.py — Live Canvas panel for Intelli agents.

The agent writes HTML into the canvas by calling the canvas_render tool,
which POSTs to /canvas/render. The canvas.html page subscribes via SSE
to /canvas/stream and updates its iframe whenever new HTML arrives.

Architecture:
  agent tool call → POST /canvas/render → canvas_manager.push(html)
                                        ↘ SSE /canvas/stream → canvas.html iframe
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Iterator


class CanvasManager:
    """Thread-safe canvas state + SSE publisher."""

    def __init__(self) -> None:
        self._html: str = _BLANK_CANVAS
        self._lock = threading.Lock()
        # Each active SSE listener registers its queue here
        self._queues: list[asyncio.Queue] = []
        self._q_lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────

    def render(self, html: str, title: str = '') -> None:
        """Update the canvas HTML and notify all SSE listeners."""
        with self._lock:
            self._html = html
        self._broadcast({'type': 'render', 'html': html, 'title': title,
                         'ts': int(time.time() * 1000)})

    def clear(self) -> None:
        """Reset the canvas to blank."""
        self.render(_BLANK_CANVAS, title='')

    def get_html(self) -> str:
        with self._lock:
            return self._html

    # ── SSE support ───────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE listener. Returns a queue that receives events."""
        q: asyncio.Queue = asyncio.Queue()
        with self._q_lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._q_lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict) -> None:
        """Put event onto every active SSE queue (non-blocking)."""
        with self._q_lock:
            for q in list(self._queues):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass


# ---------------------------------------------------------------------------
# Default blank canvas HTML
# ---------------------------------------------------------------------------

_BLANK_CANVAS = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {
    margin: 0; min-height: 100vh;
    background: #0f1117; color: #e2e4f0;
    font-family: 'Segoe UI', system-ui, sans-serif;
    display: flex; align-items: center; justify-content: center;
    flex-direction: column; gap: 16px; opacity: .5;
  }
  .icon { font-size: 3rem; }
  p { font-size: .9rem; }
</style>
</head>
<body>
  <div class="icon">🎨</div>
  <p>Canvas is empty. Ask the agent to draw something!</p>
</body>
</html>
"""

# Singleton
_canvas = CanvasManager()


def get_canvas() -> CanvasManager:
    return _canvas
