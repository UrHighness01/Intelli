"""Tests for gateway_ctl.py  permissions subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  get, set, clear
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
        'perm_action': 'get',
        'username': 'alice',
        'tools': '',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_permissions(args)
    return m


# ===========================================================================
# get
# ===========================================================================

class TestPermissionsGet:
    def test_calls_get_endpoint(self):
        m = _run(_args(perm_action='get', username='alice'))
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/users/alice/permissions', token='tok'
        )

    def test_uses_correct_username(self):
        m = _run(_args(perm_action='get', username='bob'))
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/users/bob/permissions', token='tok'
        )


# ===========================================================================
# set
# ===========================================================================

class TestPermissionsSet:
    def test_calls_put_with_tool_list(self):
        m = _run(_args(perm_action='set', username='alice', tools='file.write,echo'))
        m.assert_called_once_with(
            'PUT', 'http://localhost:8080/admin/users/alice/permissions',
            token='tok', body={'allowed_tools': ['file.write', 'echo']}
        )

    def test_parses_comma_separated_tools(self):
        m = _run(_args(perm_action='set', username='alice', tools='a,b,c'))
        _, call_kwargs = m.call_args[0], m.call_args[1] if m.call_args[1] else {}
        body = m.call_args[1].get('body') or m.call_args.kwargs.get('body')
        assert body['allowed_tools'] == ['a', 'b', 'c']  # type: ignore[index]

    def test_strips_whitespace_from_tools(self):
        m = _run(_args(perm_action='set', username='alice', tools=' file.read , echo '))
        body = m.call_args.kwargs.get('body') or m.call_args[1].get('body')
        assert body['allowed_tools'] == ['file.read', 'echo']

    def test_single_tool(self):
        m = _run(_args(perm_action='set', username='alice', tools='noop'))
        body = m.call_args.kwargs.get('body') or m.call_args[1].get('body')
        assert body['allowed_tools'] == ['noop']


# ===========================================================================
# clear
# ===========================================================================

class TestPermissionsClear:
    def test_calls_put_with_null_allowed_tools(self):
        m = _run(_args(perm_action='clear', username='alice'))
        m.assert_called_once_with(
            'PUT', 'http://localhost:8080/admin/users/alice/permissions',
            token='tok', body={'allowed_tools': None}
        )

    def test_uses_correct_username(self):
        m = _run(_args(perm_action='clear', username='charlie'))
        args_called = m.call_args[0]
        assert '/admin/users/charlie/permissions' in args_called[1]


# ===========================================================================
# Parser
# ===========================================================================

class TestPermissionsParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_permissions_registered(self):
        assert 'permissions' in _subcommands(self.parser)

    def test_get_parsed(self):
        ns = self.parser.parse_args(['permissions', 'get', 'alice'])
        assert ns.perm_action == 'get'
        assert ns.username == 'alice'

    def test_set_parsed(self):
        ns = self.parser.parse_args(['permissions', 'set', 'alice', 'file.read,echo'])
        assert ns.perm_action == 'set'
        assert ns.username == 'alice'
        assert ns.tools == 'file.read,echo'

    def test_clear_parsed(self):
        ns = self.parser.parse_args(['permissions', 'clear', 'alice'])
        assert ns.perm_action == 'clear'
        assert ns.username == 'alice'

    def test_func_wired(self):
        ns = self.parser.parse_args(['permissions', 'get', 'alice'])
        assert ns.func is gateway_ctl.cmd_permissions
