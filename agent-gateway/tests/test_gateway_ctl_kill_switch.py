"""Tests for gateway_ctl.py  kill-switch subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  status, on, off
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

def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'tok',
        'ks_action': 'status',
        'reason': '',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_kill_switch(args)
    return m


# ===========================================================================
# status
# ===========================================================================

class TestKillSwitchStatus:
    def test_calls_get_kill_switch(self):
        m = _run(_args(ks_action='status'), ret={'active': False})
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/kill-switch', token='tok'
        )

    def test_does_not_crash_on_empty_response(self):
        _run(_args(ks_action='status'), ret={})  # must not raise


# ===========================================================================
# on
# ===========================================================================

class TestKillSwitchOn:
    def test_calls_post_with_reason(self):
        m = _run(_args(ks_action='on', reason='maintenance'))
        m.assert_called_once_with(
            'POST', 'http://localhost:8080/admin/kill-switch',
            token='tok', body={'reason': 'maintenance'}
        )

    def test_empty_reason_still_calls(self):
        m = _run(_args(ks_action='on', reason=''))
        body = m.call_args.kwargs['body']
        assert body['reason'] == ''

    def test_no_reason_attribute_uses_empty_string(self):
        # reason defaults to '' in _args; test explicitly
        m = _run(_args(ks_action='on'))
        assert m.call_args.kwargs['body']['reason'] == ''


# ===========================================================================
# off
# ===========================================================================

class TestKillSwitchOff:
    def test_calls_delete_kill_switch(self):
        m = _run(_args(ks_action='off'))
        m.assert_called_once_with(
            'DELETE', 'http://localhost:8080/admin/kill-switch', token='tok'
        )


# ===========================================================================
# Parser
# ===========================================================================

class TestKillSwitchParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_kill_switch_registered(self):
        assert 'kill-switch' in _subcommands(self.parser)

    def test_status_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'kill-switch', 'status'])
        assert args.ks_action == 'status'
        assert args.func is gateway_ctl.cmd_kill_switch

    def test_on_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'kill-switch', 'on', '--reason', 'test']
        )
        assert args.ks_action == 'on'
        assert args.reason == 'test'

    def test_off_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'kill-switch', 'off'])
        assert args.ks_action == 'off'
