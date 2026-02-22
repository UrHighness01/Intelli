"""Tests for gateway_ctl.py  providers subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  list, expiring
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
        'prov_action': 'list',
        'within_days': 7,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_providers(args)
    return m


# ===========================================================================
# list
# ===========================================================================

class TestProvidersList:
    def test_calls_get_providers(self):
        m = _run(_args(prov_action='list'), ret={'providers': []})
        m.assert_called_once_with('GET', 'http://localhost:8080/providers', token='tok')

    def test_does_not_crash_on_empty_list(self):
        _run(_args(prov_action='list'), ret={'providers': []})  # must not raise

    def test_does_not_crash_on_empty_response(self):
        _run(_args(prov_action='list'), ret={})  # must not raise

    def test_prints_provider_names(self, capsys):
        ret = {'providers': [
            {'name': 'openai', 'configured': True},
            {'name': 'anthropic', 'configured': False},
        ]}
        _run(_args(prov_action='list'), ret=ret)
        out = capsys.readouterr().out
        assert 'openai' in out
        assert 'anthropic' in out


# ===========================================================================
# expiring
# ===========================================================================

class TestProvidersExpiring:
    def test_calls_expiring_endpoint(self):
        m = _run(_args(prov_action='expiring', within_days=14))
        call_url = m.call_args[0][1]
        assert '/admin/providers/expiring' in call_url
        assert 'within_days=14' in call_url

    def test_default_within_days(self):
        m = _run(_args(prov_action='expiring', within_days=7))
        call_url = m.call_args[0][1]
        assert 'within_days=7' in call_url


# ===========================================================================
# Parser
# ===========================================================================

class TestProvidersParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_providers_registered(self):
        assert 'providers' in _subcommands(self.parser)

    def test_list_parsed(self):
        ns = self.parser.parse_args(['providers', 'list'])
        assert ns.prov_action == 'list'

    def test_expiring_parsed(self):
        ns = self.parser.parse_args(['providers', 'expiring'])
        assert ns.prov_action == 'expiring'

    def test_expiring_within_days(self):
        ns = self.parser.parse_args(['providers', 'expiring', '--within-days', '30'])
        assert ns.within_days == 30.0

    def test_func_wired(self):
        ns = self.parser.parse_args(['providers', 'list'])
        assert ns.func is gateway_ctl.cmd_providers
