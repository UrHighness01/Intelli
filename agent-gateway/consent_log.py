"""Consent / context-sharing timeline.

Every time a tab snapshot is shared with an agent (via /tab/preview) the
gateway records an append-only entry so users can audit exactly what was
sent, when, and by which actor.

Each entry has the fields:
  ts        – ISO-8601 UTC timestamp
  url       – page URL whose context was shared
  origin    – origin (scheme + host)
  actor     – Bearer token prefix (first 6 chars + '…') or 'anonymous'
  fields    – list of input-field names included in the snapshot
  redacted  – list of field names that were redacted before sending
  selected_text_len – character count of the selected text (if any), 0 otherwise

Sensitive values (field *values*, not names) are NOT stored here — only the
field name inventory is logged, preserving privacy while giving the user a
meaningful timeline.

Storage: one JSON-Lines file per gateway instance (CONSENT_TIMELINE_PATH,
default ``agent-gateway/consent_timeline.jsonl``).  The file is append-only.

API
---
    from consent_log import log_context_share, get_timeline

    # Record a share event
    log_context_share(url='https://example.com/login',
                      origin='https://example.com',
                      snapshot=tab_snap,
                      actor='abc123...')

    # Retrieve the last 100 entries (optionally filtered by origin)
    entries = get_timeline(origin='https://example.com', limit=100)
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONSENT_TIMELINE_PATH = Path(
    os.environ.get(
        'AGENT_GATEWAY_CONSENT_PATH',
        str(Path(__file__).with_name('consent_timeline.jsonl')),
    )
)

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def log_context_share(
    url: str,
    origin: str,
    snapshot: Dict[str, Any],
    actor: Optional[str] = None,
    redacted_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Record a context-share event and return the entry written to disk.

    Parameters
    ----------
    url:
        Full page URL.
    origin:
        Origin string (scheme + host).  If empty, derived from *url*.
    snapshot:
        The sanitized tab snapshot dict returned by TabContextBridge.snapshot().
    actor:
        Opaque identifier for the requester (e.g. first 6 chars of a token).
    redacted_fields:
        List of input-field names that were redacted *before* the snapshot
        was returned to the caller.
    """
    if not origin:
        parsed = urlparse(url)
        origin = f'{parsed.scheme}://{parsed.netloc}' if parsed.netloc else url

    # Collect only field *names* (not values)
    inputs = snapshot.get('inputs', [])
    field_names = [inp.get('name') or inp.get('id') or '' for inp in inputs if isinstance(inp, dict)]

    selected_len = 0
    if snapshot.get('selected_text'):
        selected_len = len(str(snapshot['selected_text']))

    entry: Dict[str, Any] = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'url': url,
        'origin': origin,
        'actor': actor or 'anonymous',
        'fields': field_names,
        'redacted': redacted_fields or [],
        'selected_text_len': selected_len,
        'title': snapshot.get('title', ''),
    }

    _append(entry)
    return entry


def _append(entry: Dict[str, Any]) -> None:
    with _lock:
        try:
            CONSENT_TIMELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CONSENT_TIMELINE_PATH.open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            pass


def get_timeline(
    origin: Optional[str] = None,
    limit: int = 100,
    actor: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return up to *limit* entries, newest first.

    Parameters
    ----------
    origin:
        If given, only return entries whose ``origin`` matches exactly.
    limit:
        Maximum number of entries to return.
    actor:
        If given, only return entries by this actor.
    """
    entries: List[Dict[str, Any]] = []
    try:
        with CONSENT_TIMELINE_PATH.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if origin and entry.get('origin') != origin:
                    continue
                if actor and entry.get('actor') != actor:
                    continue
                entries.append(entry)
    except FileNotFoundError:
        pass

    # Newest first
    entries.reverse()
    return entries[:limit]


def clear_timeline(origin: Optional[str] = None) -> int:
    """Remove entries from the timeline.

    If *origin* is given, rewrites the file without entries matching that origin
    and returns the count of removed entries.  If *origin* is None, truncates
    the whole file and returns the total count removed.
    """
    removed = 0
    with _lock:
        if origin is None:
            try:
                all_lines = CONSENT_TIMELINE_PATH.read_text(encoding='utf-8').splitlines()
                removed = len([l for l in all_lines if l.strip()])
                CONSENT_TIMELINE_PATH.write_text('', encoding='utf-8')
            except FileNotFoundError:
                pass
        else:
            kept: List[str] = []
            try:
                for line in CONSENT_TIMELINE_PATH.read_text(encoding='utf-8').splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        kept.append(line)
                        continue
                    if entry.get('origin') == origin:
                        removed += 1
                    else:
                        kept.append(line)
                CONSENT_TIMELINE_PATH.write_text(
                    '\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8'
                )
            except FileNotFoundError:
                pass
    return removed


# ---------------------------------------------------------------------------
# GDPR / data-subject access & erasure
# ---------------------------------------------------------------------------

def export_actor_data(actor: str) -> List[Dict[str, Any]]:
    """Return *all* timeline entries for *actor*, oldest first.

    This implements the GDPR right of access (Art. 15): the complete record of
    context-share events attributable to a specific actor token prefix.  The
    result is intentionally unbounded (no ``limit``) so the export is complete.
    """
    entries: List[Dict[str, Any]] = []
    try:
        with CONSENT_TIMELINE_PATH.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get('actor') == actor:
                    entries.append(entry)
    except FileNotFoundError:
        pass
    return entries  # oldest first (file order)


def erase_actor_data(actor: str) -> int:
    """Delete all timeline entries for *actor* and return the count removed.

    Implements the GDPR right to erasure (Art. 17).  The timeline file is
    rewritten without any lines that match the given actor.
    """
    removed = 0
    with _lock:
        kept: List[str] = []
        try:
            for line in CONSENT_TIMELINE_PATH.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    kept.append(line)
                    continue
                if entry.get('actor') == actor:
                    removed += 1
                else:
                    kept.append(line)
            CONSENT_TIMELINE_PATH.write_text(
                '\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8'
            )
        except FileNotFoundError:
            pass
    return removed
