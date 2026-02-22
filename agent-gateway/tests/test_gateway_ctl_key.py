"""Tests for gateway_ctl.py  key subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  set, rotate, status, expiry, delete
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
        'key_action': 'status',
        'provider': 'openai',
        'key': 'sk-test',
        'ttl_days': None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_key(args)
    return m


# ===========================================================================
# set
# ===========================================================================

class TestKeySet:
    def test_calls_post_endpoint(self):
        m = _run(_args(key_action='set', provider='openai', key='sk-abc', ttl_days=None))
        m.assert_called_once_with(
            'POST', 'http://localhost:8080/admin/providers/openai/key',
            token='tok', body={'key': 'sk-abc'}
        )

    def test_includes_ttl_when_provided(self):
        m = _run(_args(key_action='set', provider='openai', key='sk-abc', ttl_days=30))
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert body['ttl_days'] == 30

    def test_omits_ttl_when_none(self):
        m = _run(_args(key_action='set', provider='openai', key='sk-abc', ttl_days=None))
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert 'ttl_days' not in body

    def test_uses_provider_in_url(self):
        m = _run(_args(key_action='set', provider='anthropic', key='sk-abc'))
        call_url = m.call_args[0][1]
        assert '/admin/providers/anthropic/key' in call_url


# ===========================================================================
# rotate
# ===========================================================================

class TestKeyRotate:
    def test_calls_rotate_endpoint(self):
        m = _run(_args(key_action='rotate', provider='openai', key='sk-new'))
        m.assert_called_once_with(
            'POST', 'http://localhost:8080/admin/providers/openai/key/rotate',
            token='tok', body={'key': 'sk-new'}
        )

    def test_includes_ttl_when_provided(self):
        m = _run(_args(key_action='rotate', provider='openai', key='sk-new', ttl_days=60))
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert body['ttl_days'] == 60


# ===========================================================================
# status
# ===========================================================================

class TestKeyStatus:
    def test_calls_status_endpoint(self):
        m = _run(_args(key_action='status', provider='openai'))
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/providers/openai/key/status', token='tok'
        )

    def test_uses_correct_provider(self):
        m = _run(_args(key_action='status', provider='anthropic'))
        call_url = m.call_args[0][1]
        assert '/admin/providers/anthropic/key/status' in call_url


# ===========================================================================
# expiry
# ===========================================================================

class TestKeyExpiry:
    def test_calls_expiry_endpoint(self):
        m = _run(_args(key_action='expiry', provider='openai'))
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/providers/openai/key/expiry', token='tok'
        )


# ===========================================================================
# delete
# ===========================================================================

class TestKeyDelete:
    def test_calls_delete_endpoint(self):
        m = _run(_args(key_action='delete', provider='openai'))
        m.assert_called_once_with(
            'DELETE', 'http://localhost:8080/admin/providers/openai/key', token='tok'
        )

    def test_uses_correct_provider(self):
        m = _run(_args(key_action='delete', provider='ollama'))
        call_url = m.call_args[0][1]
        assert '/admin/providers/ollama/key' in call_url


# ===========================================================================
# Parser
# ===========================================================================

class TestKeyParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_key_registered(self):
        assert 'key' in _subcommands(self.parser)

    def test_set_parsed(self):
        ns = self.parser.parse_args(['key', 'set', 'openai', 'sk-abc'])
        assert ns.key_action == 'set'
        assert ns.provider == 'openai'
        assert ns.key == 'sk-abc'

    def test_set_ttl_flag(self):
        ns = self.parser.parse_args(['key', 'set', 'openai', 'sk-abc', '--ttl-days', '30'])
        assert ns.ttl_days == 30

    def test_rotate_parsed(self):
        ns = self.parser.parse_args(['key', 'rotate', 'openai', 'sk-new'])
        assert ns.key_action == 'rotate'

    def test_status_parsed(self):
        ns = self.parser.parse_args(['key', 'status', 'openai'])
        assert ns.key_action == 'status'
        assert ns.provider == 'openai'

    def test_expiry_parsed(self):
        ns = self.parser.parse_args(['key', 'expiry', 'openai'])
        assert ns.key_action == 'expiry'

    def test_delete_parsed(self):
        ns = self.parser.parse_args(['key', 'delete', 'openai'])
        assert ns.key_action == 'delete'

    def test_func_wired(self):
        ns = self.parser.parse_args(['key', 'status', 'openai'])
        assert ns.func is gateway_ctl.cmd_key
