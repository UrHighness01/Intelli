"""Agent persona management.

Personas are named personality profiles stored under ~/.intelli/personas/<slug>/.
Each persona directory contains:

  config.json  – name, avatar emoji, preferred model/provider, created_at
  SOUL.md      – the system-prompt personality text injected before every reply

A built-in "intelli" persona is always available and cannot be deleted.

Environment variables:
  INTELLI_PERSONAS_DIR – override the default storage directory.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Storage location
# ---------------------------------------------------------------------------
_PERSONAS_DIR: pathlib.Path = pathlib.Path(
    os.environ.get(
        'INTELLI_PERSONAS_DIR',
        str(pathlib.Path.home() / '.intelli' / 'personas'),
    )
)

# ---------------------------------------------------------------------------
# Built-in default persona (always present, immutable)
# ---------------------------------------------------------------------------
_DEFAULT_PERSONA: dict = {
    'slug':       'intelli',
    'name':       'Intelli',
    'avatar':     '🤖',
    'model':      '',
    'provider':   '',
    'builtin':    True,
    'created_at': 0,
    'soul': (
        'You are Intelli, an AI-native browser assistant.\n'
        'You are helpful, concise, and deeply integrated with the user\'s browser.\n'
        'You can browse pages, run code, search the web, and manage the user\'s workspace.\n'
        'Always be transparent about what you are doing and why.'
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slug(name: str) -> str:
    """Convert a persona name to a URL-safe, filesystem-safe slug."""
    return re.sub(r'[^a-z0-9_-]+', '-', name.lower().strip()).strip('-') or 'unnamed'


def _load_dir(d: pathlib.Path) -> Optional[dict]:
    cfg_path  = d / 'config.json'
    soul_path = d / 'SOUL.md'
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text(encoding='utf-8'))
        data['slug']     = d.name
        data['builtin']  = False
        data.setdefault('avatar',   '🤖')
        data.setdefault('model',    '')
        data.setdefault('provider', '')
        data['soul'] = soul_path.read_text(encoding='utf-8') if soul_path.exists() else ''
        return data
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def list_personas() -> list[dict]:
    """Return all personas — built-in first, then user-created sorted by name."""
    out: list[dict] = [_DEFAULT_PERSONA]
    if not _PERSONAS_DIR.exists():
        return out
    for d in sorted(_PERSONAS_DIR.iterdir()):
        if d.is_dir() and d.name != 'intelli':
            p = _load_dir(d)
            if p:
                out.append(p)
    return out


def _safe_slug(slug: str) -> Optional[str]:
    """Sanitize *slug* for use as a filesystem component.

    Applies the slug regex then ``os.path.basename()`` (CodeQL-recognised taint
    sanitiser) to strip any directory separators.  Returns None for empty result.
    """
    sanitized = _slug(slug)                     # strips to a-z0-9_-
    sanitized = os.path.basename(sanitized)     # taint barrier
    return sanitized if sanitized else None


def get_persona(slug: str) -> Optional[dict]:
    """Return a persona by slug, or None if not found."""
    if slug in ('', 'intelli'):
        return _DEFAULT_PERSONA
    safe = _safe_slug(slug)
    if safe is None:
        return None
    # Apply os.path.basename() at the join site so CodeQL sees the taint barrier
    # in the same scope as the path expression.
    return _load_dir(_PERSONAS_DIR / os.path.basename(safe))


def create_persona(
    name: str,
    soul: str,
    avatar: str = '🤖',
    model: str = '',
    provider: str = '',
) -> dict:
    """Create a new persona and persist it to disk. Returns the full persona dict."""
    slug = _safe_slug(name)
    if slug is None:
        raise ValueError(f'Invalid persona name: {name!r}')
    d = _PERSONAS_DIR / os.path.basename(slug)  # basename at join site: taint barrier
    d.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        'name':       name,
        'avatar':     avatar,
        'model':      model,
        'provider':   provider,
        'created_at': time.time(),
    }
    (d / 'config.json').write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    (d / 'SOUL.md').write_text(soul, encoding='utf-8')
    return {**cfg, 'slug': slug, 'soul': soul, 'builtin': False}


def update_persona(slug: str, **kwargs) -> Optional[dict]:
    """Update fields of an existing persona. Pass soul= to update SOUL.md."""
    if slug in ('', 'intelli'):
        return None  # built-in is immutable
    safe = _safe_slug(slug)
    if safe is None:
        return None
    d = _PERSONAS_DIR / os.path.basename(safe)  # basename at join site: taint barrier
    cfg_path = d / 'config.json'
    if not cfg_path.exists():
        return None
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    if 'soul' in kwargs:
        (d / 'SOUL.md').write_text(kwargs.pop('soul'), encoding='utf-8')
    for key in ('name', 'avatar', 'model', 'provider'):
        if key in kwargs:
            cfg[key] = kwargs[key]
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    return _load_dir(d)


def delete_persona(slug: str) -> bool:
    """Delete a persona. Cannot delete the built-in 'intelli' persona."""
    if slug in ('', 'intelli'):
        return False
    safe = _safe_slug(slug)
    if safe is None:
        return False
    d = _PERSONAS_DIR / os.path.basename(safe)  # basename at join site: taint barrier
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def build_system_prompt(slug: str) -> str:
    """Return the formatted system-prompt block for a given persona slug."""
    p = get_persona(slug) if slug else _DEFAULT_PERSONA
    if not p:
        return ''
    soul   = p.get('soul', '').strip()
    header = f'# Persona: {p["name"]} {p.get("avatar", "")}'
    return f'{header}\n\n{soul}' if soul else header
