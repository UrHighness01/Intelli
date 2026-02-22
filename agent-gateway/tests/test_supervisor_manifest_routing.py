"""Tests for manifest-driven approval routing in supervisor.py.

The supervisor now reads ``requires_approval`` from capability manifests and
uses that as the authoritative routing decision, overriding the heuristic risk
score when a manifest is present.

Key behaviours verified:
  * Manifest ``requires_approval: false`` allows a call even when the heuristic
    risk scorer would rate it ``high`` (e.g. path-traversal args on file.read).
  * Manifest ``requires_approval: true`` queues a call even when args look safe.
  * Tools with no manifest fall back to the existing heuristic (unchanged).
  * ``approval_required()`` reflects the same logic as ``process_call()``.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from supervisor import Supervisor, compute_risk, load_schema_from_file
from tools.capability import ToolManifest

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "tool_schema.json"
_SCHEMA = load_schema_from_file(_SCHEMA_PATH)


def _sup():
    return Supervisor(_SCHEMA)


# ---------------------------------------------------------------------------
# Manifest present and requires_approval == False
# ---------------------------------------------------------------------------

class TestManifestAllowsCall:
    """When the manifest opts out of approval the call must be accepted, even
    if the heuristic risk scorer would have routed it to the queue."""

    def test_echo_safe_args_accepted(self):
        # echo.json: requires_approval=false, risk_level=low
        r = _sup().process_call({'tool': 'echo', 'args': {'text': 'hello'}})
        assert r['status'] == 'accepted'

    def test_file_read_traversal_accepted_by_manifest(self):
        """file.read manifest has requires_approval=false.
        The argument '../etc/passwd' scores arg_score >= 2 → heuristic says high.
        With the manifest present the call must still be accepted."""
        r = _sup().process_call({'tool': 'file.read', 'args': {'path': '../etc/passwd'}})
        assert r['status'] == 'accepted'

    def test_file_read_traversal_risk_still_reported_high(self):
        """Risk level is still computed and reported, even though approval is skipped."""
        r = _sup().process_call({'tool': 'file.read', 'args': {'path': '../etc/passwd'}})
        assert r.get('risk') == 'high'

    def test_file_read_traversal_message_indicates_auto_approved(self):
        r = _sup().process_call({'tool': 'file.read', 'args': {'path': '../etc/passwd'}})
        assert 'auto-approved' in r.get('message', '').lower()

    def test_browser_dom_accepted_always(self):
        # browser.dom manifest: requires_approval=false, risk_level=low
        r = _sup().process_call({'tool': 'browser.dom',
                                  'args': {'selector': '#content', 'action': 'read'}})
        assert r['status'] == 'accepted'

    def test_manifest_false_overrides_high_heuristic_via_mock(self):
        """Generic: mock any tool's manifest to requires_approval=False and
        pair it with args that the heuristic scores high; call must be accepted."""
        fake = MagicMock()
        fake.requires_approval = False
        with patch.object(ToolManifest, 'load', return_value=fake):
            # 'system.exec' is in HIGH_RISK_TOOLS → heuristic: high
            r = _sup().process_call({'tool': 'system.exec',
                                      'args': {'command': 'ls'}})
        assert r['status'] == 'accepted'


# ---------------------------------------------------------------------------
# Manifest present and requires_approval == True
# ---------------------------------------------------------------------------

class TestManifestForcesApproval:
    """When the manifest declares requires_approval=True the call is always
    queued, even if the heuristic would score it low/medium."""

    def test_system_exec_always_queued(self):
        # system.exec.json: requires_approval=true
        r = _sup().process_call({'tool': 'system.exec', 'args': {'command': 'ls'}})
        assert r['status'] == 'pending_approval'

    def test_file_delete_always_queued(self):
        # file.delete.json: requires_approval=true
        r = _sup().process_call({'tool': 'file.delete', 'args': {'path': '/tmp/safe.txt'}})
        assert r['status'] == 'pending_approval'

    def test_network_request_always_queued(self):
        # network.request.json: requires_approval=true
        r = _sup().process_call({'tool': 'network.request',
                                  'args': {'url': 'https://example.com', 'method': 'GET'}})
        assert r['status'] == 'pending_approval'

    def test_manifest_true_overrides_low_heuristic_via_mock(self):
        """Generic: mock a low-risk tool's manifest to requires_approval=True;
        the call must be queued despite safe args."""
        fake = MagicMock()
        fake.requires_approval = True
        with patch.object(ToolManifest, 'load', return_value=fake):
            # 'noop' is not high-risk by heuristic but manifest says must approve
            r = _sup().process_call({'tool': 'noop', 'args': {}})
        assert r['status'] == 'pending_approval'

    def test_pending_approval_id_is_integer(self):
        r = _sup().process_call({'tool': 'system.exec', 'args': {'command': 'echo hi'}})
        assert isinstance(r.get('id'), int)


# ---------------------------------------------------------------------------
# No manifest — heuristic fallback (regression / backward-compat)
# ---------------------------------------------------------------------------

class TestNoManifestFallback:
    """Tools without a capability manifest must continue to use the heuristic
    risk scorer (existing behaviour unchanged)."""

    def test_unknown_tool_high_risk_args_queued(self):
        # No manifest; traversal arg → heuristic: high → queued
        r = _sup().process_call({'tool': 'unknown.custom',
                                  'args': {'cmd': '../etc/passwd', 'script': 'evil'}})
        assert r['status'] == 'pending_approval'

    def test_unknown_tool_safe_args_accepted(self):
        # No manifest; safe args → heuristic: low → accepted
        r = _sup().process_call({'tool': 'unknown.safe',
                                  'args': {'text': 'hello world'}})
        assert r['status'] == 'accepted'

    def test_no_manifest_route_uses_compute_risk(self):
        """Explicit: patch ToolManifest.load to return None and verify that
        heuristic routing applies."""
        with patch.object(ToolManifest, 'load', return_value=None):
            r_high = _sup().process_call({'tool': 'system.exec',
                                           'args': {'command': 'ls'}})
            r_low = _sup().process_call({'tool': 'echo',
                                          'args': {'text': 'hi'}})
        assert r_high['status'] == 'pending_approval'
        assert r_low['status'] == 'accepted'


# ---------------------------------------------------------------------------
# approval_required() method
# ---------------------------------------------------------------------------

class TestApprovalRequired:
    """``Supervisor.approval_required()`` must reflect the manifest-first logic."""

    def test_false_for_manifest_no_approval(self):
        # echo.json: requires_approval=false
        assert _sup().approval_required({'tool': 'echo', 'args': {'text': 'hi'}}) is False

    def test_true_for_manifest_requires_approval(self):
        # system.exec.json: requires_approval=true
        assert _sup().approval_required({'tool': 'system.exec', 'args': {'command': 'ls'}}) is True

    def test_true_when_no_manifest_and_high_heuristic(self):
        with patch.object(ToolManifest, 'load', return_value=None):
            result = _sup().approval_required({'tool': 'system.exec', 'args': {'command': 'rm -rf /'}})
        assert result is True

    def test_false_when_no_manifest_and_low_heuristic(self):
        with patch.object(ToolManifest, 'load', return_value=None):
            result = _sup().approval_required({'tool': 'echo', 'args': {'text': 'safe'}})
        assert result is False

    def test_manifest_true_overrides_low_heuristic_in_approval_required(self):
        fake = MagicMock()
        fake.requires_approval = True
        with patch.object(ToolManifest, 'load', return_value=fake):
            assert _sup().approval_required({'tool': 'noop', 'args': {}}) is True

    def test_manifest_false_overrides_high_heuristic_in_approval_required(self):
        fake = MagicMock()
        fake.requires_approval = False
        with patch.object(ToolManifest, 'load', return_value=fake):
            assert _sup().approval_required({'tool': 'file.write', 'args': {'path': '../evil'}}) is False
