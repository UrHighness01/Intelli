"""Local knowledge-base / notes system for Intelli Gateway.

Notes are stored as Markdown files under ``INTELLI_NOTES_DIR``
(default: ``~/.intelli/notes/``), one file per day: ``YYYY-MM-DD.md``.

Each note entry is appended with an H2 heading derived from the title and a
small frontmatter block so entries remain human-readable.

Public API
----------
    save(content, url, title, tags) -> dict
    list_notes(max_days)            -> list[dict]
    search(query)                   -> str
    get_note_file(date_str)         -> str
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_NOTES_DIR = Path(os.environ.get('INTELLI_NOTES_DIR', Path.home() / '.intelli' / 'notes'))
_MAX_SEARCH_RESULTS = int(os.environ.get('INTELLI_NOTES_SEARCH_MAX', '50'))


def _dir() -> Path:
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return _NOTES_DIR


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save(
    content: str,
    url: str = '',
    title: str = '',
    tags: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Append a note entry to today's file.

    Parameters
    ----------
    content:
        Markdown body text.
    url:
        Optional source URL.
    title:
        Optional note title (used as H2 heading).
    tags:
        Optional list of tag strings.

    Returns a dict with ``path``, ``date``, ``title``, ``byte_offset``.
    """
    tags = tags or []
    today = date.today().isoformat()  # YYYY-MM-DD
    filename = _dir() / f'{today}.md'

    ts = time.strftime('%H:%M:%S')
    # Build entry
    lines: List[str] = []
    lines.append(f'\n## {title or "Note"} — {ts}\n')
    if url:
        lines.append(f'> Source: <{url}>\n')
    if tags:
        lines.append(f'> Tags: {", ".join(tags)}\n')
    lines.append('\n')
    lines.append(content.strip())
    lines.append('\n')

    entry = '\n'.join(lines)

    # Write header if file is new
    if not filename.exists():
        header = f'# Notes · {today}\n'
        filename.write_text(header, encoding='utf-8')

    byte_offset = filename.stat().st_size
    with filename.open('a', encoding='utf-8') as fh:
        fh.write(entry)

    return {
        'ok': True,
        'path': str(filename),
        'date': today,
        'title': title or 'Note',
        'byte_offset': byte_offset,
    }


def list_notes(max_days: int = 7) -> List[Dict[str, object]]:
    """Return metadata for recent note files, newest first.

    Parameters
    ----------
    max_days:
        How many calendar days back to look (default 7).

    Returns a list of dicts with ``date``, ``path``, ``size_bytes``,
    ``entry_count`` (approximate H2 heading count).
    """
    d = _dir()
    results: List[Dict[str, object]] = []
    today = date.today()
    for offset in range(max_days):
        day = today - timedelta(days=offset)
        fp = d / f'{day.isoformat()}.md'
        if fp.exists():
            text = fp.read_text(encoding='utf-8', errors='replace')
            entry_count = text.count('\n## ')
            results.append({
                'date': day.isoformat(),
                'path': str(fp),
                'size_bytes': fp.stat().st_size,
                'entry_count': entry_count,
            })
    return results


def search(query: str, max_results: int = _MAX_SEARCH_RESULTS) -> str:
    """Case-insensitive grep across all note files.

    Parameters
    ----------
    query:
        Search term(s).  Multiple words are AND-searched.
    max_results:
        Maximum number of matching lines to return.

    Returns a Markdown-formatted string with matching snippets.
    """
    if not query.strip():
        return '(empty query)'

    terms = query.lower().split()
    d = _dir()
    hits: List[str] = []
    for fp in sorted(d.glob('*.md'), reverse=True):
        for lineno, line in enumerate(fp.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if all(t in line.lower() for t in terms):
                hits.append(f'**{fp.name}:{lineno}** {line.strip()}')
                if len(hits) >= max_results:
                    break
        if len(hits) >= max_results:
            break

    if not hits:
        return f'No notes matching "{query}".'
    return '\n'.join(hits)


def get_note_file(date_str: str = '') -> str:
    """Return the raw Markdown content of a note file.

    Parameters
    ----------
    date_str:
        ISO date string ``YYYY-MM-DD``.  Defaults to today.
    """
    if not date_str:
        date_str = date.today().isoformat()
    try:
        date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return 'Invalid date format — expected YYYY-MM-DD.'
    # os.path.basename used directly as the path-join operand (no intermediate
    # variable) so CodeQL sees the sanitiser output as the join operand.
    fp = _dir() / (os.path.basename(date_str) + '.md')
    if not fp.exists():
        return 'No notes file for ' + os.path.basename(date_str) + '.'
    return fp.read_text(encoding='utf-8', errors='replace')
