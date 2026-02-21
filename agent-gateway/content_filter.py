"""Content moderation / policy filter for the Agent Gateway.

Checks text content against a configurable deny-list of patterns and raises
an HTTP 403 exception when a match is found.  Patterns can be plain literals
or regular expressions.

Configuration sources (applied in order, all merged)
-----------------------------------------------------
1. ``AGENT_GATEWAY_CONTENT_FILTER_PATTERNS`` env var — comma-separated literal
   strings to deny.  Applied on every startup / reload.
2. ``CONTENT_FILTER_PATH`` JSON file — list of rule objects persisted by the
   admin API.  Modified at runtime via ``add_rule()`` / ``delete_rule()``.

Rule object schema
------------------
    {
      "pattern": "<string or regex>",
      "mode":    "literal" | "regex",     // default: "literal"
      "label":   "<human-readable name>"  // default: first 40 chars of pattern
    }

Enforcement points
------------------
- ``POST /tools/call``     — all string values (recursive) in ``ToolCall.args``
- ``POST /chat/complete``  — all ``message.content`` strings in the request

Admin API endpoints (see app.py)
---------------------------------
- ``GET  /admin/content-filter/rules``          — list active rules
- ``POST /admin/content-filter/rules``          — add a rule
- ``DELETE /admin/content-filter/rules/{idx}``  — remove a rule by index

Environment variables
---------------------
AGENT_GATEWAY_CONTENT_FILTER_PATH
    Path to the persisted rules JSON file.
    Default: ``agent-gateway/content_filter_rules.json``.
AGENT_GATEWAY_CONTENT_FILTER_PATTERNS
    Comma-separated literal deny patterns (no persistence needed).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FILTER_PATH = Path(
    os.environ.get(
        'AGENT_GATEWAY_CONTENT_FILTER_PATH',
        str(Path(__file__).with_name('content_filter_rules.json')),
    )
)

_lock = threading.Lock()

# In-memory state: list of (compiled_pattern, label, raw_pattern_str, mode)
_compiled: List[tuple] = []
# Mirror of rule dicts for the API (excludes ephemeral env-var rules)
_file_rules: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compile_rule(rule: Dict[str, Any]) -> tuple:
    pat = rule.get('pattern', '')
    mode = rule.get('mode', 'literal')
    label = rule.get('label') or pat[:40]
    if mode == 'regex':
        try:
            compiled = re.compile(pat, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            raise ValueError(f'invalid regex {pat!r}: {exc}') from exc
    else:
        compiled = re.compile(re.escape(pat), re.IGNORECASE)
    return (compiled, label, pat, mode)


def _load() -> None:
    """(Re)load all rules from env var + file.  Must be called under _lock."""
    global _compiled, _file_rules

    compiled: List[tuple] = []

    # Source 1: env var (literal strings)
    for pat in os.environ.get('AGENT_GATEWAY_CONTENT_FILTER_PATTERNS', '').split(','):
        pat = pat.strip()
        if pat:
            compiled.append(_compile_rule({'pattern': pat, 'mode': 'literal', 'label': f'env:{pat[:20]}'}))

    # Source 2: persisted file rules
    file_rules: List[Dict[str, Any]] = []
    try:
        raw = json.loads(FILTER_PATH.read_text(encoding='utf-8'))
        if isinstance(raw, list):
            file_rules = raw
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    for rule in file_rules:
        try:
            compiled.append(_compile_rule(rule))
        except ValueError:
            pass  # skip broken rules silently

    _compiled = compiled
    _file_rules = file_rules


# ---------------------------------------------------------------------------
# Module-level initialisation
# ---------------------------------------------------------------------------

with _lock:
    _load()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reload() -> int:
    """Reload rules from disk/env.  Returns the count of active patterns."""
    with _lock:
        _load()
        return len(_compiled)


def get_rules() -> List[Dict[str, Any]]:
    """Return a copy of the persisted rule list (excludes env-var rules)."""
    with _lock:
        return list(_file_rules)


def add_rule(pattern: str, mode: str = 'literal', label: str = '') -> None:
    """Append a new rule and persist it to the filter file.

    Parameters
    ----------
    pattern:
        The literal string or regex pattern to deny.
    mode:
        ``'literal'`` (default) or ``'regex'``.
    label:
        Human-readable name.  Defaults to first 40 chars of the pattern.

    Raises
    ------
    ValueError
        If *mode* is ``'regex'`` and *pattern* is not a valid regular expression.
    """
    rule: Dict[str, Any] = {
        'pattern': pattern,
        'mode': mode,
        'label': label or pattern[:40],
    }
    # Validate before persisting
    _compile_rule(rule)

    with _lock:
        _load()
        _file_rules.append(rule)
        _persist()
        _load()


def delete_rule(index: int) -> bool:
    """Remove the rule at *index* in the persisted rule list.

    Returns ``True`` if a rule was removed, ``False`` if the index was
    out of range (so callers can return 404 without catching exceptions).
    """
    with _lock:
        _load()
        if index < 0 or index >= len(_file_rules):
            return False
        _file_rules.pop(index)
        _persist()
        _load()
        return True


def _persist() -> None:
    """Write _file_rules to disk.  Must be called under _lock."""
    FILTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    FILTER_PATH.write_text(
        json.dumps(_file_rules, indent=2, ensure_ascii=False), encoding='utf-8'
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _extract_strings(obj: Any) -> List[str]:
    """Recursively collect all string values from *obj*."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        result: List[str] = []
        for v in obj.values():
            result.extend(_extract_strings(v))
        return result
    if isinstance(obj, list):
        result = []
        for v in obj:
            result.extend(_extract_strings(v))
        return result
    return []


def check(obj: Any) -> None:
    """Raise HTTP 403 if *obj* contains text matching any active deny rule.

    The check is applied recursively to all string values in dicts/lists.
    If no rules are configured, this is a no-op.

    Parameters
    ----------
    obj:
        Any JSON-compatible value (str, dict, list, …).

    Raises
    ------
    fastapi.HTTPException (403)
        With ``detail.error == 'content_policy_violation'`` and the matching
        rule's label and pattern.
    """
    with _lock:
        rules = list(_compiled)

    if not rules:
        return

    for text in _extract_strings(obj):
        for compiled_re, label, raw, _mode in rules:
            if compiled_re.search(text):
                raise HTTPException(
                    status_code=403,
                    detail={
                        'error': 'content_policy_violation',
                        'matched_rule': label,
                        'pattern': raw,
                    },
                )
