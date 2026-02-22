"""Tests for gateway_ctl.py  capabilities subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered sub-actions:
  list, show
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gateway_ctl  # noqa: E402


def _subcommands(parser: argparse.ArgumentParser) -> dict:
    """Return the top-level subcommand name → parser map."""
    for action in parser._actions:
        if hasattr(action, '_name_parser_map'):
            return getattr(action, '_name_parser_map')
    return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_TOOLS = [
    {
        "tool": "echo",
        "display_name": "Echo",
        "description": "Returns its inputs verbatim.",
        "risk_level": "low",
        "requires_approval": False,
        "required_capabilities": [],
        "optional_capabilities": [],
    },
    {
        "tool": "file.write",
        "display_name": "File Write",
        "description": "Creates or overwrites a file.",
        "risk_level": "high",
        "requires_approval": True,
        "required_capabilities": ["fs.write"],
        "optional_capabilities": [],
    },
    {
        "tool": "network.request",
        "display_name": "HTTP Request",
        "description": "Makes an outbound HTTP request.",
        "risk_level": "high",
        "requires_approval": True,
        "required_capabilities": ["net.http"],
        "optional_capabilities": ["net.socket"],
    },
]


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'fake-token',
        'cap_action': 'list',
        'tool': '',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_capabilities(args)
        return m


# ===========================================================================
# capabilities list
# ===========================================================================

class TestCapabilitiesList:
    def test_calls_get_tools_capabilities(self, capsys):
        m = _run(_args(cap_action='list'), ret={'tools': _SAMPLE_TOOLS})
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/tools/capabilities', token='fake-token'
        )

    def test_prints_all_tool_names(self, capsys):
        _run(_args(cap_action='list'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'echo' in out
        assert 'file.write' in out
        assert 'network.request' in out

    def test_prints_risk_levels(self, capsys):
        _run(_args(cap_action='list'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'low' in out
        assert 'high' in out

    def test_prints_approval_required(self, capsys):
        _run(_args(cap_action='list'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'yes' in out
        assert 'no' in out

    def test_prints_capabilities(self, capsys):
        _run(_args(cap_action='list'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'fs.write' in out
        assert 'net.http' in out

    def test_empty_manifests_message(self, capsys):
        _run(_args(cap_action='list'), ret={'tools': []})
        out = capsys.readouterr().out
        assert 'No capability manifests' in out or 'no' in out.lower()

    def test_prints_count_at_end(self, capsys):
        _run(_args(cap_action='list'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert '3' in out


# ===========================================================================
# capabilities show
# ===========================================================================

class TestCapabilitiesShow:
    def test_calls_get_tools_capabilities(self, capsys):
        m = _run(_args(cap_action='show', tool='echo'), ret={'tools': _SAMPLE_TOOLS})
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/tools/capabilities', token='fake-token'
        )

    def test_shows_tool_details(self, capsys):
        _run(_args(cap_action='show', tool='file.write'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'file.write' in out
        assert 'File Write' in out
        assert 'high' in out.lower()
        assert 'fs.write' in out

    def test_shows_optional_capabilities(self, capsys):
        _run(_args(cap_action='show', tool='network.request'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'net.http' in out
        assert 'net.socket' in out

    def test_case_insensitive_lookup(self, capsys):
        _run(_args(cap_action='show', tool='FILE.WRITE'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'file.write' in out.lower()

    def test_unknown_tool_exits_with_error(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request', return_value={'tools': _SAMPLE_TOOLS}):
            with pytest.raises(SystemExit) as exc_info:
                gateway_ctl.cmd_capabilities(_args(cap_action='show', tool='nomanifest.tool'))
            assert exc_info.value.code == 1

    def test_shows_requires_approval(self, capsys):
        _run(_args(cap_action='show', tool='file.write'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'yes' in out.lower()

    def test_shows_no_approval_for_echo(self, capsys):
        _run(_args(cap_action='show', tool='echo'), ret={'tools': _SAMPLE_TOOLS})
        out = capsys.readouterr().out
        assert 'no' in out.lower()


# ===========================================================================
# Parser
# ===========================================================================

class TestCapabilitiesParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_capabilities_registered(self):
        assert 'capabilities' in _subcommands(self.parser)

    def test_list_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'capabilities', 'list'])
        assert args.cap_action == 'list'
        assert args.func is gateway_ctl.cmd_capabilities

    def test_show_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'capabilities', 'show', 'file.write'])
        assert args.cap_action == 'show'
        assert args.tool == 'file.write'
