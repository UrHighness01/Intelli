"""Page Diff Watcher — monitors URLs for content changes.

Polls configured URLs in a background daemon thread. When the Readability-
extracted text changes beyond a configurable threshold, all pending alerts are
queued and the latest diff stored per watcher.

Storage:  ~/.intelli/watchers.json  (persists across restarts)

Environment variables:
  INTELLI_WATCHERS_FILE – override default storage path.
  INTELLI_WATCHER_TICK  – poll loop interval in seconds (default 30).
"""
from __future__ import annotations

import difflib
import json
import os
import pathlib
import threading
import time
import uuid
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_WATCHERS_FILE = pathlib.Path(
    os.environ.get(
        'INTELLI_WATCHERS_FILE',
        str(pathlib.Path.home() / '.intelli' / 'watchers.json'),
    )
)
_TICK = int(os.environ.get('INTELLI_WATCHER_TICK', '30'))  # seconds between poll cycles
_MAX_ALERTS_PER_WATCHER = 20
_MAX_CONTENT_CHARS = 80_000  # store at most 80 KB of text per baseline

# ---------------------------------------------------------------------------
# In-memory state (rebuilt from disk on startup)
# ---------------------------------------------------------------------------
_lock: threading.Lock = threading.Lock()
_watchers: dict[str, dict]  = {}   # id → watcher dict
_alerts:   dict[str, list]  = {}   # id → list of alert dicts


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _load() -> None:
    global _watchers
    _WATCHERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _WATCHERS_FILE.exists():
        return
    try:
        data = json.loads(_WATCHERS_FILE.read_text(encoding='utf-8'))
        _watchers = {w['id']: w for w in data if isinstance(w, dict) and 'id' in w}
    except Exception:
        _watchers = {}


def _save() -> None:
    try:
        _WATCHERS_FILE.write_text(
            json.dumps(list(_watchers.values()), indent=2, ensure_ascii=False),
            encoding='utf-8',
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Readability-lite: strip tags and collapse whitespace
# ---------------------------------------------------------------------------
import re as _re

_TAG_RE    = _re.compile(r'<[^>]+>')
_SPACE_RE  = _re.compile(r'\s+')
_SCRIPT_RE = _re.compile(r'<(script|style)[^>]*>.*?</\1>', _re.DOTALL | _re.IGNORECASE)


def _extract_text(html: str) -> str:
    html = _SCRIPT_RE.sub('', html)
    text = _TAG_RE.sub(' ', html)
    return _SPACE_RE.sub(' ', text).strip()[:_MAX_CONTENT_CHARS]


# ---------------------------------------------------------------------------
# Fetch helper (uses httpx if available, falls back to urllib)
# ---------------------------------------------------------------------------
def _fetch(url: str, timeout: int = 15) -> Optional[str]:
    try:
        import httpx
        r = httpx.get(url, follow_redirects=True, timeout=timeout,
                      headers={'User-Agent': 'IntelliWatcher/1.0'})
        r.raise_for_status()
        return r.text
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'IntelliWatcher/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------
def _similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity via SequenceMatcher (1.0 = identical)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).quick_ratio()


def _unified_diff(a: str, b: str, n: int = 3) -> str:
    lines_a = a.splitlines(keepends=True)
    lines_b = b.splitlines(keepends=True)
    diff = list(difflib.unified_diff(lines_a, lines_b, fromfile='before', tofile='after', n=n))
    return ''.join(diff[:200])  # cap at 200 lines


# ---------------------------------------------------------------------------
# Background poll loop
# ---------------------------------------------------------------------------
def _poll_one(watcher: dict) -> None:
    """Check one watcher, queue alert if content changed enough."""
    wid      = watcher['id']
    url      = watcher['url']
    threshold = float(watcher.get('notify_threshold', 0.02))  # fraction changed

    html = _fetch(url)
    if html is None:
        watcher['last_error'] = f'fetch failed at {time.strftime("%H:%M:%S")}'
        return

    text = _extract_text(html)
    watcher.pop('last_error', None)
    watcher['last_checked'] = time.time()

    baseline = watcher.get('baseline_text', '')
    if not baseline:
        # First fetch — just store baseline
        watcher['baseline_text'] = text
        watcher['baseline_at']   = time.time()
        return

    sim = _similarity(baseline, text)
    changed_fraction = 1.0 - sim
    if changed_fraction >= threshold:
        diff = _unified_diff(baseline, text)
        alert = {
            'watcher_id':       wid,
            'url':              url,
            'ts':               time.time(),
            'changed_fraction': round(changed_fraction, 4),
            'diff_snippet':     diff[:2000],
        }
        with _lock:
            _alerts.setdefault(wid, [])
            _alerts[wid].append(alert)
            if len(_alerts[wid]) > _MAX_ALERTS_PER_WATCHER:
                _alerts[wid] = _alerts[wid][-_MAX_ALERTS_PER_WATCHER:]
        # Update baseline to latest so we diff future changes against now
        watcher['baseline_text'] = text
        watcher['baseline_at']   = time.time()
        watcher['last_alert_ts'] = time.time()


def _poll_loop() -> None:
    _load()
    while True:
        now = time.time()
        with _lock:
            ids = list(_watchers.keys())
        for wid in ids:
            with _lock:
                w = _watchers.get(wid)
            if not w or not w.get('enabled', True):
                continue
            interval = int(w.get('interval_minutes', 60)) * 60
            last = w.get('last_checked', 0)
            if now - last >= interval:
                try:
                    _poll_one(w)
                    with _lock:
                        _save()
                except Exception:
                    pass
        time.sleep(_TICK)


# ---------------------------------------------------------------------------
# Start background thread (idempotent)
# ---------------------------------------------------------------------------
_started = False


def start() -> None:
    global _started
    if _started:
        return
    _load()
    t = threading.Thread(target=_poll_loop, daemon=True, name='watcher-poll')
    t.start()
    _started = True


# ---------------------------------------------------------------------------
# Public CRUD API
# ---------------------------------------------------------------------------
def add_watcher(
    url: str,
    label: str = '',
    interval_minutes: int = 60,
    notify_threshold: float = 0.02,
) -> dict:
    wid = str(uuid.uuid4())
    w: dict = {
        'id':               wid,
        'url':              url,
        'label':            label or url,
        'interval_minutes': max(1, int(interval_minutes)),
        'notify_threshold': max(0.001, float(notify_threshold)),
        'enabled':          True,
        'created_at':       time.time(),
        'last_checked':     0,
        'baseline_text':    '',
    }
    with _lock:
        _watchers[wid] = w
        _save()
    return _public(w)


def list_watchers() -> list[dict]:
    with _lock:
        ws = list(_watchers.values())
    return [_public(w) for w in sorted(ws, key=lambda x: x.get('created_at', 0), reverse=True)]


def get_watcher(wid: str) -> Optional[dict]:
    with _lock:
        w = _watchers.get(wid)
    return _public(w) if w else None


def update_watcher(wid: str, **kwargs) -> Optional[dict]:
    with _lock:
        w = _watchers.get(wid)
        if not w:
            return None
        for key in ('label', 'interval_minutes', 'notify_threshold', 'enabled'):
            if key in kwargs:
                w[key] = kwargs[key]
        _save()
    return _public(w)


def delete_watcher(wid: str) -> bool:
    with _lock:
        if wid not in _watchers:
            return False
        del _watchers[wid]
        _alerts.pop(wid, None)
        _save()
    return True


def get_alerts(wid: str, clear: bool = False) -> list[dict]:
    with _lock:
        al = list(_alerts.get(wid, []))
        if clear:
            _alerts[wid] = []
    return al


def get_all_alerts(limit: int = 50) -> list[dict]:
    with _lock:
        all_alerts = []
        for al in _alerts.values():
            all_alerts.extend(al)
    all_alerts.sort(key=lambda x: x.get('ts', 0), reverse=True)
    return all_alerts[:limit]


def trigger_watcher(wid: str) -> dict:
    """Force an immediate poll of a watcher (resets last_checked to 0)."""
    with _lock:
        w = _watchers.get(wid)
        if not w:
            return {'error': f'watcher {wid!r} not found'}
        w['last_checked'] = 0
    return {'triggered': wid}


def _public(w: dict) -> dict:
    """Return a copy of watcher dict without the (potentially large) baseline_text."""
    out = {k: v for k, v in w.items() if k != 'baseline_text'}
    out['has_baseline'] = bool(w.get('baseline_text'))
    with _lock:
        out['pending_alerts'] = len(_alerts.get(w['id'], []))
    return out
