"""Tests for gateway_ctl.py  consent subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  export, erase (with --yes bypass), timeline
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
        'consent_action': 'export',
        'actor': 'alice',
        'yes': False,
        'n': 100,
        'origin': '',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_consent(args)
    return m


# ===========================================================================
# export
# ===========================================================================

class TestConsentExport:
    def test_calls_get_endpoint(self):
        m = _run(_args(consent_action='export', actor='alice'))
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/consent/export/alice', token='tok'
        )

    def test_uses_correct_actor(self):
        m = _run(_args(consent_action='export', actor='bob'))
        call_url = m.call_args[0][1]
        assert '/consent/export/bob' in call_url


# ===========================================================================
# erase
# ===========================================================================

class TestConsentErase:
    def test_calls_delete_with_yes_flag(self):
        m = _run(_args(consent_action='erase', actor='alice', yes=True))
        m.assert_called_once_with(
            'DELETE', 'http://localhost:8080/consent/export/alice', token='tok'
        )

    def test_aborts_when_user_declines(self, capsys):
        args = _args(consent_action='erase', actor='alice', yes=False)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request') as m, \
             patch('builtins.input', return_value='no'):
            gateway_ctl.cmd_consent(args)
        m.assert_not_called()
        out = capsys.readouterr().out
        assert 'Aborted' in out

    def test_proceeds_when_user_confirms(self):
        args = _args(consent_action='erase', actor='alice', yes=False)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={}) as m, \
             patch('builtins.input', return_value='yes'):
            gateway_ctl.cmd_consent(args)
        m.assert_called_once()

    def test_uses_correct_actor_in_url(self):
        m = _run(_args(consent_action='erase', actor='charlie', yes=True))
        call_url = m.call_args[0][1]
        assert 'charlie' in call_url


# ===========================================================================
# timeline
# ===========================================================================

class TestConsentTimeline:
    def test_calls_get_timeline(self):
        m = _run(_args(consent_action='timeline', n=50, origin=''))
        m.assert_called_once()
        call_url = m.call_args[0][1]
        assert '/consent/timeline' in call_url

    def test_includes_limit_in_query(self):
        m = _run(_args(consent_action='timeline', n=25))
        call_url = m.call_args[0][1]
        assert 'limit=25' in call_url

    def test_includes_origin_when_set(self):
        m = _run(_args(consent_action='timeline', n=100, origin='web'))
        call_url = m.call_args[0][1]
        assert 'origin=web' in call_url

    def test_no_origin_param_when_empty(self):
        m = _run(_args(consent_action='timeline', n=100, origin=''))
        call_url = m.call_args[0][1]
        assert 'origin' not in call_url


# ===========================================================================
# Parser
# ===========================================================================

class TestConsentParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_consent_registered(self):
        assert 'consent' in _subcommands(self.parser)

    def test_export_parsed(self):
        ns = self.parser.parse_args(['consent', 'export', 'alice'])
        assert ns.consent_action == 'export'
        assert ns.actor == 'alice'

    def test_erase_parsed(self):
        ns = self.parser.parse_args(['consent', 'erase', 'alice'])
        assert ns.consent_action == 'erase'
        assert ns.actor == 'alice'

    def test_erase_yes_flag(self):
        ns = self.parser.parse_args(['consent', 'erase', 'alice', '--yes'])
        assert ns.yes is True

    def test_timeline_parsed(self):
        ns = self.parser.parse_args(['consent', 'timeline'])
        assert ns.consent_action == 'timeline'

    def test_timeline_n_flag(self):
        ns = self.parser.parse_args(['consent', 'timeline', '--n', '50'])
        assert ns.n == 50

    def test_timeline_origin_flag(self):
        ns = self.parser.parse_args(['consent', 'timeline', '--origin', 'app'])
        assert ns.origin == 'app'

    def test_func_wired(self):
        ns = self.parser.parse_args(['consent', 'export', 'alice'])
        assert ns.func is gateway_ctl.cmd_consent
