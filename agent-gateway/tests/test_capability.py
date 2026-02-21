"""Tests for agent-gateway/tools/capability.py.

Covers:
  - ToolManifest.load – found / not found
  - CapabilityVerifier.check – allowed, denied, ALL wildcard, extra args
  - _parse_allowed_caps env-var parsing
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tools.capability import CapabilityVerifier, ToolManifest, _MANIFEST_DIR


# ---------------------------------------------------------------------------
# ToolManifest
# ---------------------------------------------------------------------------

class TestToolManifest:
    def test_load_known_tool(self):
        m = ToolManifest.load('file.read')
        assert m is not None
        assert 'fs.read' in m.required

    def test_load_high_risk_tool(self):
        m = ToolManifest.load('file.write')
        assert m is not None
        assert m.risk_level == 'high'
        assert m.requires_approval is True
        assert 'fs.write' in m.required

    def test_load_noop_tool(self):
        m = ToolManifest.load('noop')
        assert m is not None
        assert len(m.required) == 0
        assert m.risk_level == 'low'

    def test_load_echo_tool_has_allowed_arg_keys(self):
        m = ToolManifest.load('echo')
        assert m is not None
        assert m.allowed_arg_keys is not None
        assert 'text' in m.allowed_arg_keys

    def test_load_missing_tool_returns_none(self):
        m = ToolManifest.load('does.not.exist')
        assert m is None

    def test_load_system_exec(self):
        m = ToolManifest.load('system.exec')
        assert m is not None
        assert 'sys.exec' in m.required
        assert m.requires_approval is True

    def test_load_browser_dom(self):
        m = ToolManifest.load('browser.dom')
        assert m is not None
        assert 'browser.dom' in m.required
        assert m.risk_level == 'low'


# ---------------------------------------------------------------------------
# CapabilityVerifier
# ---------------------------------------------------------------------------

class TestCapabilityVerifier:
    # --- allowed ---

    def test_allowed_when_cap_in_set(self):
        v = CapabilityVerifier(allowed=frozenset({'fs.read'}))
        ok, denied = v.check('file.read', {})
        assert ok is True
        assert denied == []

    def test_allowed_noop_always_passes(self):
        """Tools with no required capabilities pass regardless of allow-list."""
        v = CapabilityVerifier(allowed=frozenset())  # nothing allowed
        ok, denied = v.check('noop', {})
        assert ok is True
        assert denied == []

    def test_allowed_with_all_wildcard(self):
        v = CapabilityVerifier(allowed=frozenset({'ALL'}))
        ok, denied = v.check('file.write', {})
        assert ok is True
        assert denied == []

    def test_unknown_tool_is_permitted(self):
        """If there's no manifest for a tool, the verifier allows it through."""
        v = CapabilityVerifier(allowed=frozenset())
        ok, denied = v.check('some.unknown.tool', {})
        assert ok is True
        assert denied == []

    # --- denied ---

    def test_denied_when_cap_missing(self):
        v = CapabilityVerifier(allowed=frozenset({'browser.dom'}))  # no fs.write
        ok, denied = v.check('file.write', {})
        assert ok is False
        assert 'fs.write' in denied

    def test_denied_multiple_caps(self):
        v = CapabilityVerifier(allowed=frozenset())
        ok, denied = v.check('file.write', {})
        assert ok is False
        assert any('fs.write' in d for d in denied)

    # --- arg key enforcement ---

    def test_extra_args_denied_for_echo(self):
        # Use a real cap (echo has no required caps, so any non-ALL allowed set still
        # triggers the arg-key guard).  The guard is skipped only when ALL is set.
        v = CapabilityVerifier(allowed=frozenset({'browser.dom'}))
        ok, denied = v.check('echo', {'text': 'hi', 'secret': 'bad'})
        assert ok is False
        assert any('arg_keys_not_allowed' in d for d in denied)

    def test_all_wildcard_bypasses_arg_key_check(self):
        """When ALL caps are granted, arg-key restrictions are disabled (dev mode)."""
        v = CapabilityVerifier(allowed=frozenset({'ALL'}))
        ok, denied = v.check('echo', {'text': 'hi', 'secret': 'bad'})
        assert ok is True
        assert denied == []

    def test_valid_args_allowed_for_echo(self):
        v = CapabilityVerifier(allowed=frozenset({'ALL'}))
        ok, denied = v.check('echo', {'text': 'hello'})
        assert ok is True
        assert denied == []

    def test_no_args_always_ok_for_echo(self):
        v = CapabilityVerifier(allowed=frozenset({'ALL'}))
        ok, denied = v.check('echo', {})
        assert ok is True

    # --- manifest_for helper ---

    def test_manifest_for_returns_manifest(self):
        v = CapabilityVerifier(allowed=frozenset({'ALL'}))
        m = v.manifest_for('file.read')
        assert m is not None
        assert 'fs.read' in m.required

    def test_manifest_for_returns_none_for_unknown(self):
        v = CapabilityVerifier(allowed=frozenset())
        m = v.manifest_for('unknown.tool.xyz')
        assert m is None


# ---------------------------------------------------------------------------
# _parse_allowed_caps env-var
# ---------------------------------------------------------------------------

class TestParseAllowedCaps:
    def test_defaults_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv('AGENT_GATEWAY_ALLOWED_CAPS', raising=False)
        import importlib, tools.capability as cap_mod
        importlib.reload(cap_mod)
        caps = cap_mod._parse_allowed_caps()
        assert 'fs.read' in caps
        assert 'browser.dom' in caps

    def test_all_wildcard(self, monkeypatch):
        monkeypatch.setenv('AGENT_GATEWAY_ALLOWED_CAPS', 'ALL')
        import importlib, tools.capability as cap_mod
        importlib.reload(cap_mod)
        caps = cap_mod._parse_allowed_caps()
        assert 'ALL' in caps

    def test_custom_caps_parsed(self, monkeypatch):
        monkeypatch.setenv('AGENT_GATEWAY_ALLOWED_CAPS', 'fs.write, net.http, sys.exec')
        import importlib, tools.capability as cap_mod
        importlib.reload(cap_mod)
        caps = cap_mod._parse_allowed_caps()
        assert 'fs.write' in caps
        assert 'net.http' in caps
        assert 'sys.exec' in caps
        assert 'fs.read' not in caps
