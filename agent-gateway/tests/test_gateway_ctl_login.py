"""Tests for gateway_ctl.py  login subcommand.

The login command differs from others: it does NOT call ``_get_token``
(it IS the token acquisition step) and it calls ``_save_token`` on success.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._save_token``.
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import patch, call

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
        'token': None,
        'username': 'admin',
        'password': 'secret',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_request', return_value=ret or {'token': 'cached-tok'}) as m_req, \
         patch.object(gateway_ctl, '_save_token') as m_save:
        gateway_ctl.cmd_login(args)
    return m_req, m_save


# ===========================================================================
# cmd_login
# ===========================================================================

class TestLogin:
    def test_calls_post_login(self):
        m_req, _ = _run(_args(username='admin', password='pass'))
        m_req.assert_called_once_with(
            'POST', 'http://localhost:8080/admin/login',
            body={'username': 'admin', 'password': 'pass'},
        )

    def test_saves_token_on_success(self):
        _, m_save = _run(_args(), ret={'token': 'my-token'})
        m_save.assert_called_once_with('my-token')

    def test_exits_when_no_token_in_response(self):
        with patch.object(gateway_ctl, '_request', return_value={}), \
             patch.object(gateway_ctl, '_save_token') as m_save:
            with pytest.raises(SystemExit):
                gateway_ctl.cmd_login(_args())
        m_save.assert_not_called()

    def test_prints_success_message(self, capsys):
        _run(_args(username='admin'), ret={'token': 'tok'})
        out = capsys.readouterr().out
        assert 'admin' in out

    def test_uses_correct_url(self):
        args = _args(url='http://my-gateway:9000')
        m_req, _ = _run(args, ret={'token': 'tok'})
        call_url = m_req.call_args[0][1]
        assert call_url == 'http://my-gateway:9000/admin/login'


# ===========================================================================
# Parser
# ===========================================================================

class TestLoginParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_login_registered(self):
        assert 'login' in _subcommands(self.parser)

    def test_username_required(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(['login', '-p', 'pass'])

    def test_password_required(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(['login', '-u', 'admin'])

    def test_full_parse(self):
        ns = self.parser.parse_args(['login', '-u', 'admin', '-p', 'pass'])
        assert ns.username == 'admin'
        assert ns.password == 'pass'

    def test_long_flags(self):
        ns = self.parser.parse_args(['login', '--username', 'admin', '--password', 'pass'])
        assert ns.username == 'admin'
        assert ns.password == 'pass'

    def test_func_wired(self):
        ns = self.parser.parse_args(['login', '-u', 'admin', '-p', 'pass'])
        assert ns.func is gateway_ctl.cmd_login
