"""Tool capability model for the Agent Gateway.

Every tool declares a set of capabilities (e.g., "fs.write", "net.http")
in a sidecar JSON manifest.  Before a call is dispatched the CapabilityVerifier
checks those declared capabilities against a per-deployment allow-list.  Any
call that requires a capability not in the allow-list is rejected with a
structured error before it ever reaches the sandbox.

Manifest schema (schemas/capabilities/{tool}.json)
---------------------------------------------------
{
  "tool": "file.write",
  "display_name": "File Write",
  "description": "Writes content to a file on disk.",
  "required_capabilities": ["fs.write"],
  "optional_capabilities": [],
  "risk_level": "high",
  "requires_approval": true,
  "allowed_arg_keys": ["path", "content", "mode"]
}

Known capability tokens
-----------------------
  fs.read          Read from the filesystem
  fs.write         Create or overwrite files
  fs.delete        Delete files or directories
  fs.list          List directory contents

  net.http         Make outbound HTTP requests
  net.socket       Open raw TCP/UDP sockets

  sys.exec         Execute arbitrary OS processes
  sys.env          Read environment variables

  clipboard.read   Read the system clipboard
  clipboard.write  Write to the system clipboard

  browser.dom      Access / mutate the live DOM
  browser.nav      Navigate the browser to a URL
  browser.cookies  Access browser cookies

Environment variables
---------------------
AGENT_GATEWAY_ALLOWED_CAPS
    Comma-separated list of capability tokens that are permitted in this
    deployment.  Defaults to a conservative set: fs.read, browser.dom.
    Set to "ALL" to allow everything (dev/testing only).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_ALLOWED = frozenset({'fs.read', 'browser.dom'})

_MANIFEST_DIR = Path(__file__).parent.parent / 'schemas' / 'capabilities'

def _parse_allowed_caps() -> FrozenSet[str]:
    raw = os.environ.get('AGENT_GATEWAY_ALLOWED_CAPS', '').strip()
    if not raw:
        return _DEFAULT_ALLOWED
    if raw.upper() == 'ALL':
        return frozenset({'ALL'})
    return frozenset(c.strip() for c in raw.split(',') if c.strip())


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

class ToolManifest:
    """Parsed capability manifest for a single tool."""

    def __init__(self, data: Dict[str, Any]):
        self.tool: str = data.get('tool', '')
        self.display_name: str = data.get('display_name', self.tool)
        self.description: str = data.get('description', '')
        self.required: FrozenSet[str] = frozenset(data.get('required_capabilities', []))
        self.optional: FrozenSet[str] = frozenset(data.get('optional_capabilities', []))
        self.risk_level: str = data.get('risk_level', 'low')
        self.requires_approval: bool = bool(data.get('requires_approval', False))
        self.allowed_arg_keys: Optional[FrozenSet[str]] = (
            frozenset(data['allowed_arg_keys']) if 'allowed_arg_keys' in data else None
        )

    @classmethod
    def load(cls, tool: str) -> Optional['ToolManifest']:
        """Load a manifest from disk.  Returns None if no manifest exists."""
        # tool ids use dots (e.g. "file.write") — map to path segments
        rel = tool.replace('.', '/') + '.json'
        candidate = _MANIFEST_DIR / rel
        if candidate.exists():
            try:
                return cls(json.loads(candidate.read_text(encoding='utf-8')))
            except Exception:
                return None
        return None

    def all_required_caps(self) -> FrozenSet[str]:
        return self.required


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class CapabilityVerifier:
    """Check tool calls against the deployment's allowed-capability policy.

    Usage::

        verifier = CapabilityVerifier()
        ok, denied = verifier.check('file.write', args={...})
        if not ok:
            return {'error': 'capability_denied', 'denied': denied}
    """

    def __init__(self, allowed: Optional[FrozenSet[str]] = None):
        self._allowed: FrozenSet[str] = allowed if allowed is not None else _parse_allowed_caps()

    def _is_allowed(self, cap: str) -> bool:
        if 'ALL' in self._allowed:
            return True
        return cap in self._allowed

    def check(self, tool: str, args: Dict[str, Any] | None = None) -> tuple[bool, List[str]]:
        """Return (allowed: bool, denied_caps: List[str]).

        Loads the manifest for *tool*.  If no manifest exists the call is
        permitted (unknown tools are risk-scored separately by the supervisor).
        """
        manifest = ToolManifest.load(tool)
        if manifest is None:
            return True, []

        denied = [cap for cap in manifest.required if not self._is_allowed(cap)]

        # Also check allowed_arg_keys if declared — skipped when ALL caps are granted
        if manifest.allowed_arg_keys is not None and args and 'ALL' not in self._allowed:
            extra_keys = set(args.keys()) - manifest.allowed_arg_keys
            if extra_keys:
                denied.append(f'arg_keys_not_allowed:{",".join(sorted(extra_keys))}')

        return len(denied) == 0, denied

    def manifest_for(self, tool: str) -> Optional[ToolManifest]:
        return ToolManifest.load(tool)


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

default_verifier = CapabilityVerifier()
