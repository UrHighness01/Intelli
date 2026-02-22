"""Tests for gateway_ctl.py  rate-limits subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  status, set, reset-client, reset-user
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import call, patch

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
    'config': {
        'enabled': True,
        'max_requests': 60,
        'window_seconds': 60.0,
        'burst': 10,
        'user_max_requests': 30,
        'user_window_seconds': 60.0,
    },
    'usage': {
        'total_tracked': 4,
    },
}


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'tok',
        'rl_action': 'status',
        'max_requests': None,
        'window_seconds': None,
        'burst': None,
        'enabled': None,
        'user_max_requests': None,
        'user_window_seconds': None,
        'client': '',
        'username': '',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_rate_limits(args)
    return m


# ===========================================================================
# status
# ===========================================================================

class TestRateLimitsStatus:
    def test_calls_get_admin_rate_limits(self):
        m = _run(_args(rl_action='status'), ret=_SAMPLE_STATUS)
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/rate-limits', token='tok'
        )

    def test_prints_config_keys(self, capsys):
        _run(_args(rl_action='status'), ret=_SAMPLE_STATUS)
        out = capsys.readouterr().out
        assert 'max_requests' in out
        assert '60' in out

    def test_prints_active_clients(self, capsys):
        _run(_args(rl_action='status'), ret=_SAMPLE_STATUS)
        out = capsys.readouterr().out
        assert '4' in out

    def test_empty_config_does_not_crash(self, capsys):
        _run(_args(rl_action='status'), ret={})   # missing all keys
        capsys.readouterr()   # no assertion; just must not raise


# ===========================================================================
# set
# ===========================================================================

class TestRateLimitsSet:
    def test_calls_put_with_max_requests(self):
        m = _run(_args(rl_action='set', max_requests=100))
        m.assert_called_once_with(
            'PUT', 'http://localhost:8080/admin/rate-limits',
            token='tok', body={'max_requests': 100}
        )

    def test_calls_put_with_multiple_fields(self):
        m = _run(_args(rl_action='set', max_requests=50, window_seconds=30.0))
        _call = m.call_args
        body = _call.kwargs['body']
        assert body['max_requests'] == 50
        assert body['window_seconds'] == 30.0

    def test_skips_none_fields(self):
        m = _run(_args(rl_action='set', burst=5))
        body = m.call_args.kwargs['body']
        assert 'max_requests' not in body
        assert body['burst'] == 5

    def test_enabled_true(self):
        m = _run(_args(rl_action='set', enabled=True))
        assert m.call_args.kwargs['body'] == {'enabled': True}

    def test_enabled_false(self):
        m = _run(_args(rl_action='set', enabled=False))
        assert m.call_args.kwargs['body'] == {'enabled': False}

    def test_user_fields_sent(self):
        m = _run(_args(rl_action='set', user_max_requests=20, user_window_seconds=10.0))
        body = m.call_args.kwargs['body']
        assert body['user_max_requests'] == 20
        assert body['user_window_seconds'] == 10.0


# ===========================================================================
# reset-client
# ===========================================================================

class TestRateLimitsResetClient:
    def test_calls_delete_with_client(self):
        m = _run(_args(rl_action='reset-client', client='192.168.1.1'))
        m.assert_called_once_with(
            'DELETE',
            'http://localhost:8080/admin/rate-limits/clients/192.168.1.1',
            token='tok',
        )


# ===========================================================================
# reset-user
# ===========================================================================

class TestRateLimitsResetUser:
    def test_calls_delete_with_username(self):
        m = _run(_args(rl_action='reset-user', username='alice'))
        m.assert_called_once_with(
            'DELETE',
            'http://localhost:8080/admin/rate-limits/users/alice',
            token='tok',
        )


# ===========================================================================
# Parser
# ===========================================================================

class TestRateLimitsParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_rate_limits_registered(self):
        assert 'rate-limits' in _subcommands(self.parser)

    def test_status_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'rate-limits', 'status'])
        assert args.rl_action == 'status'
        assert args.func is gateway_ctl.cmd_rate_limits

    def test_set_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'rate-limits', 'set', '--max-requests', '100']
        )
        assert args.rl_action == 'set'
        assert args.max_requests == 100

    def test_reset_client_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'rate-limits', 'reset-client', '10.0.0.1']
        )
        assert args.rl_action == 'reset-client'
        assert args.client == '10.0.0.1'

    def test_reset_user_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'rate-limits', 'reset-user', 'alice']
        )
        assert args.rl_action == 'reset-user'
        assert args.username == 'alice'
