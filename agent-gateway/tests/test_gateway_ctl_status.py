"""Tests for gateway_ctl.py  status subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered:  cmd_status  /  parser registration
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

_SAMPLE_STATUS = {
    'version': '1.2.3',
    'uptime_seconds': 4321.5,
    'kill_switch_active': False,
    'kill_switch_reason': None,
    'tool_calls_total': 88,
    'pending_approvals': 3,
    'scheduler_tasks': 5,
    'memory_agents': 7,
}


def _args(**kwargs) -> argparse.Namespace:
    defaults = {'url': 'http://localhost:8080', 'token': 'tok'}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(ret=None):
    """Run cmd_status with a mocked _request, return the mock."""
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_status(_args())
    return m


# ===========================================================================
# TestStatusShow
# ===========================================================================

class TestStatusShow:
    def test_calls_admin_status_endpoint(self, capsys):
        m = _run(ret=_SAMPLE_STATUS)
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/status', token='tok'
        )

    def test_prints_version(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert '1.2.3' in capsys.readouterr().out

    def test_prints_uptime(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert '4321.5' in capsys.readouterr().out

    def test_kill_switch_off(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert 'off' in capsys.readouterr().out

    def test_kill_switch_active_shows_armed(self, capsys):
        status = {**_SAMPLE_STATUS, 'kill_switch_active': True, 'kill_switch_reason': 'maintenance'}
        _run(ret=status)
        out = capsys.readouterr().out
        assert 'ACTIVE' in out

    def test_kill_switch_reason_shown(self, capsys):
        status = {**_SAMPLE_STATUS, 'kill_switch_active': True, 'kill_switch_reason': 'emergency stop'}
        _run(ret=status)
        assert 'emergency stop' in capsys.readouterr().out

    def test_prints_tool_calls_total(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert '88' in capsys.readouterr().out

    def test_prints_pending_approvals(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert '3' in capsys.readouterr().out

    def test_prints_scheduler_tasks(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert '5' in capsys.readouterr().out

    def test_prints_memory_agents(self, capsys):
        _run(ret=_SAMPLE_STATUS)
        assert '7' in capsys.readouterr().out

    def test_zero_values_shown(self, capsys):
        zero = {**_SAMPLE_STATUS, 'tool_calls_total': 0, 'pending_approvals': 0,
                'scheduler_tasks': 0, 'memory_agents': 0}
        _run(ret=zero)
        out = capsys.readouterr().out
        # Should still print the labels even if values are 0
        assert 'Tool calls' in out or 'tool' in out.lower()

    def test_missing_fields_do_not_crash(self, capsys):
        """Partial response (e.g. old gateway) must not raise an exception."""
        _run(ret={'version': '0.1.0', 'uptime_seconds': 1})  # most fields absent
        out = capsys.readouterr().out
        assert '0.1.0' in out


# ===========================================================================
# TestStatusParser
# ===========================================================================

class TestStatusParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_status_registered(self):
        assert 'status' in _subcommands(self.parser)

    def test_status_func(self):
        args = self.parser.parse_args(['--token', 'x', 'status'])
        assert args.func is gateway_ctl.cmd_status
