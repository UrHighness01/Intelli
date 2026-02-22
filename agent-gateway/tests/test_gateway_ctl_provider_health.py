"""Tests for gateway_ctl.py  provider-health subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  check, list, expiring
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
        'token': 'tok',
        'ph_action': 'check',
        'provider': 'openai',
        'within_days': 7,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, side_effect=None, ret=None):
    """Run cmd_provider_health with mocked _request; returns the mock."""
    with patch.object(gateway_ctl, '_get_token', return_value='tok'):
        if side_effect is not None:
            with patch.object(gateway_ctl, '_request', side_effect=side_effect) as m:
                gateway_ctl.cmd_provider_health(args)
        else:
            with patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
                gateway_ctl.cmd_provider_health(args)
    return m


# ===========================================================================
# check
# ===========================================================================

class TestProviderHealthCheck:
    def test_calls_correct_url(self):
        m = _run(_args(ph_action='check', provider='openai'),
                 ret={'status': 'ok', 'configured': True, 'available': True})
        m.assert_called_once_with('GET', 'http://localhost:8080/admin/providers/openai/health',
                                  token='tok')

    def test_calls_correct_url_anthropic(self):
        m = _run(_args(ph_action='check', provider='anthropic'),
                 ret={'status': 'no_key', 'configured': False, 'available': False})
        call_url = m.call_args[0][1]
        assert '/admin/providers/anthropic/health' in call_url

    def test_prints_status_icon_ok(self, capsys):
        _run(_args(ph_action='check', provider='openai'),
             ret={'status': 'ok', 'configured': True, 'available': True})
        out = capsys.readouterr().out
        assert '✓' in out

    def test_prints_status_icon_no_key(self, capsys):
        _run(_args(ph_action='check', provider='openai'),
             ret={'status': 'no_key', 'configured': False, 'available': False})
        out = capsys.readouterr().out
        assert '✗' in out

    def test_prints_status_icon_unavailable(self, capsys):
        _run(_args(ph_action='check', provider='openai'),
             ret={'status': 'unavailable', 'configured': True, 'available': False})
        out = capsys.readouterr().out
        assert '!' in out

    def test_prints_provider_name(self, capsys):
        _run(_args(ph_action='check', provider='ollama'),
             ret={'status': 'ok', 'configured': True, 'available': True})
        out = capsys.readouterr().out
        assert 'ollama' in out

    def test_does_not_crash_on_empty_response(self):
        _run(_args(ph_action='check', provider='openai'), ret={})  # must not raise


# ===========================================================================
# list
# ===========================================================================

class TestProviderHealthList:
    _PROVIDERS = ('openai', 'anthropic', 'openrouter', 'ollama')

    def test_calls_all_four_providers(self):
        responses = [
            {'status': 'ok', 'configured': True, 'available': True},
            {'status': 'no_key', 'configured': False, 'available': False},
            {'status': 'ok', 'configured': True, 'available': True},
            {'status': 'unavailable', 'configured': True, 'available': False},
        ]
        m = _run(_args(ph_action='list'), side_effect=responses)
        assert m.call_count == 4

    def test_checks_each_provider_url(self):
        responses = [{'status': 'ok', 'configured': True, 'available': True}] * 4
        m = _run(_args(ph_action='list'), side_effect=responses)
        called_urls = [c[0][1] for c in m.call_args_list]
        for prov in self._PROVIDERS:
            assert any(f'/admin/providers/{prov}/health' in url for url in called_urls)

    def test_prints_all_provider_names(self, capsys):
        responses = [{'status': 'ok', 'configured': True, 'available': True}] * 4
        _run(_args(ph_action='list'), side_effect=responses)
        out = capsys.readouterr().out
        for prov in self._PROVIDERS:
            assert prov in out

    def test_does_not_crash_on_empty_responses(self):
        _run(_args(ph_action='list'), side_effect=[{}, {}, {}, {}])  # must not raise


# ===========================================================================
# expiring
# ===========================================================================

class TestProviderHealthExpiring:
    def test_calls_expiring_endpoint(self):
        m = _run(_args(ph_action='expiring', within_days=7), ret={'expiring': []})
        call_url = m.call_args[0][1]
        assert '/admin/providers/expiring' in call_url

    def test_default_within_days_in_url(self):
        m = _run(_args(ph_action='expiring', within_days=7), ret={'expiring': []})
        call_url = m.call_args[0][1]
        assert 'within_days=7' in call_url

    def test_custom_within_days_in_url(self):
        m = _run(_args(ph_action='expiring', within_days=30), ret={'expiring': []})
        call_url = m.call_args[0][1]
        assert 'within_days=30' in call_url

    def test_no_expiring_message_printed(self, capsys):
        _run(_args(ph_action='expiring', within_days=7), ret={'expiring': []})
        out = capsys.readouterr().out
        assert 'No keys expiring' in out

    def test_prints_expiring_provider_and_date(self, capsys):
        ret = {'expiring': [
            {'provider': 'openai', 'expires_at': '2026-03-01T00:00:00'},
        ]}
        _run(_args(ph_action='expiring', within_days=7), ret=ret)
        out = capsys.readouterr().out
        assert 'openai' in out
        assert '2026-03-01' in out

    def test_prints_multiple_expiring_rows(self, capsys):
        ret = {'expiring': [
            {'provider': 'openai', 'expires_at': '2026-03-01T00:00:00'},
            {'provider': 'anthropic', 'expires_at': '2026-03-05T00:00:00'},
        ]}
        _run(_args(ph_action='expiring', within_days=7), ret=ret)
        out = capsys.readouterr().out
        assert 'openai' in out
        assert 'anthropic' in out

    def test_does_not_crash_on_empty_response(self):
        _run(_args(ph_action='expiring', within_days=7), ret={})  # must not raise


# ===========================================================================
# Parser
# ===========================================================================

class TestProviderHealthParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_provider_health_registered(self):
        assert 'provider-health' in _subcommands(self.parser)

    def test_check_subcommand_parsed(self):
        ns = self.parser.parse_args(['provider-health', 'check', 'openai'])
        assert ns.ph_action == 'check'
        assert ns.provider == 'openai'

    def test_check_all_providers_accepted(self):
        for prov in ('openai', 'anthropic', 'openrouter', 'ollama'):
            ns = self.parser.parse_args(['provider-health', 'check', prov])
            assert ns.provider == prov

    def test_list_subcommand_parsed(self):
        ns = self.parser.parse_args(['provider-health', 'list'])
        assert ns.ph_action == 'list'

    def test_expiring_subcommand_parsed(self):
        ns = self.parser.parse_args(['provider-health', 'expiring'])
        assert ns.ph_action == 'expiring'

    def test_expiring_within_days_default(self):
        ns = self.parser.parse_args(['provider-health', 'expiring'])
        assert ns.within_days == 7

    def test_expiring_within_days_custom(self):
        ns = self.parser.parse_args(['provider-health', 'expiring', '--within-days', '14'])
        assert ns.within_days == 14

    def test_func_wired(self):
        ns = self.parser.parse_args(['provider-health', 'list'])
        assert ns.func is gateway_ctl.cmd_provider_health
