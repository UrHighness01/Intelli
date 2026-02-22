"""Tests for gateway_ctl.py  audit subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  tail, export-csv (HTTP fetch is separately patched)
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import patch, MagicMock

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
        'audit_action': 'tail',
        'n': 20,
        'actor': '',
        'action': '',
        'since': '',
        'until': '',
        'output': 'audit.csv',
        'interval': 5.0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run_tail(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {'entries': []}) as m:
        gateway_ctl.cmd_audit(args)
    return m


# ===========================================================================
# tail
# ===========================================================================

class TestAuditTail:
    def test_calls_get_audit(self):
        m = _run_tail(_args(audit_action='tail', n=20))
        m.assert_called_once()
        call_url = m.call_args[0][1]
        assert '/admin/audit' in call_url

    def test_includes_tail_param(self):
        m = _run_tail(_args(audit_action='tail', n=10))
        call_url = m.call_args[0][1]
        assert 'tail=10' in call_url

    def test_includes_actor_filter(self):
        m = _run_tail(_args(audit_action='tail', n=20, actor='alice'))
        call_url = m.call_args[0][1]
        assert 'actor=alice' in call_url

    def test_omits_actor_when_empty(self):
        m = _run_tail(_args(audit_action='tail', n=20, actor=''))
        call_url = m.call_args[0][1]
        assert 'actor' not in call_url

    def test_includes_action_filter(self):
        m = _run_tail(_args(audit_action='tail', n=20, action='approve'))
        call_url = m.call_args[0][1]
        assert 'action=approve' in call_url

    def test_includes_since_filter(self):
        m = _run_tail(_args(audit_action='tail', n=20, since='2025-01-01T00:00:00Z'))
        call_url = m.call_args[0][1]
        assert 'since=' in call_url

    def test_does_not_crash_on_empty_entries(self):
        _run_tail(_args(audit_action='tail'), ret={'entries': []})  # must not raise

    def test_prints_entry_count(self, capsys):
        ret = {'entries': [
            {'ts': '2025-01-01T00:00:00Z', 'event': 'approve', 'actor': 'admin', 'details': {}}
        ]}
        _run_tail(_args(audit_action='tail'), ret=ret)
        out = capsys.readouterr().out
        assert '1 entry' in out


# ===========================================================================
# export-csv  (only basic checks — raw urllib fetch is complex to mock fully)
# ===========================================================================

class TestAuditExportCsv:
    def test_calls_get_audit_export(self, tmp_path):
        """Verify _request is called for the initial request, and urllib fetch proceeds."""
        csv_content = 'ts,event,actor\n2025-01-01,approve,admin\n'
        fake_response = MagicMock()
        fake_response.read.return_value = csv_content.encode('utf-8')
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        out_file = str(tmp_path / 'out.csv')
        args = _args(audit_action='export-csv', n=1000, output=out_file)

        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value='') as m_req, \
             patch('urllib.request.urlopen', return_value=fake_response):
            gateway_ctl.cmd_audit(args)

        m_req.assert_called_once()
        call_url = m_req.call_args[0][1]
        assert '/admin/audit/export.csv' in call_url

    def test_writes_csv_to_file(self, tmp_path):
        csv_content = 'ts,event,actor\n2025-01-01,approve,admin\n'
        fake_response = MagicMock()
        fake_response.read.return_value = csv_content.encode('utf-8')
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        out_file = str(tmp_path / 'out.csv')
        args = _args(audit_action='export-csv', n=1000, output=out_file)

        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value=''), \
             patch('urllib.request.urlopen', return_value=fake_response):
            gateway_ctl.cmd_audit(args)

        assert os.path.exists(out_file)
        written = open(out_file).read()
        assert 'approve' in written


# ===========================================================================
# Parser
# ===========================================================================

class TestAuditParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_audit_registered(self):
        assert 'audit' in _subcommands(self.parser)

    def test_tail_parsed(self):
        ns = self.parser.parse_args(['audit', 'tail'])
        assert ns.audit_action == 'tail'

    def test_tail_n_flag(self):
        ns = self.parser.parse_args(['audit', 'tail', '--n', '50'])
        assert ns.n == 50

    def test_tail_actor_flag(self):
        ns = self.parser.parse_args(['audit', 'tail', '--actor', 'alice'])
        assert ns.actor == 'alice'

    def test_tail_since_flag(self):
        ns = self.parser.parse_args(['audit', 'tail', '--since', '2025-01-01T00:00:00Z'])
        assert ns.since == '2025-01-01T00:00:00Z'

    def test_export_csv_parsed(self):
        ns = self.parser.parse_args(['audit', 'export-csv'])
        assert ns.audit_action == 'export-csv'

    def test_export_csv_output_flag(self):
        ns = self.parser.parse_args(['audit', 'export-csv', '--output', 'report.csv'])
        assert ns.output == 'report.csv'

    def test_func_wired(self):
        ns = self.parser.parse_args(['audit', 'tail'])
        assert ns.func is gateway_ctl.cmd_audit

    def test_follow_parsed(self):
        ns = self.parser.parse_args(['audit', 'follow'])
        assert ns.audit_action == 'follow'

    def test_follow_interval_flag(self):
        ns = self.parser.parse_args(['audit', 'follow', '--interval', '10'])
        assert ns.interval == 10.0

    def test_follow_actor_flag(self):
        ns = self.parser.parse_args(['audit', 'follow', '--actor', 'bob'])
        assert ns.actor == 'bob'

    def test_follow_action_flag(self):
        ns = self.parser.parse_args(['audit', 'follow', '--action', 'approve'])
        assert ns.action == 'approve'


# ===========================================================================
# follow
# ===========================================================================

class TestAuditFollow:
    """Tests for the `audit follow` live-poll action."""

    def _run(self, entries, sleep_effect=None, **kwargs):
        """Run follow for one poll (KeyboardInterrupt from sleep by default)."""
        args = _args(audit_action='follow', interval=1.0, **kwargs)
        printed = []
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={'entries': entries}), \
             patch('time.sleep', side_effect=sleep_effect or KeyboardInterrupt), \
             patch('builtins.print',
                   side_effect=lambda *a, **kw: printed.append(str(a[0]) if a else '')):
            gateway_ctl.cmd_audit(args)
        return printed

    def test_calls_audit_endpoint(self):
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={'entries': []}) as m, \
             patch('time.sleep', side_effect=KeyboardInterrupt), \
             patch('builtins.print'):
            gateway_ctl.cmd_audit(_args(audit_action='follow', interval=1.0))
        assert '/admin/audit' in m.call_args[0][1]

    def test_prints_new_entries(self):
        entry = {'ts': '2026-01-01T00:00:00Z', 'event': 'approve', 'actor': 'admin', 'details': {}}
        lines = self._run([entry])
        assert any('approve' in line for line in lines)

    def test_prints_stopped_on_interrupt(self):
        lines = self._run([])
        assert any('Stopped' in line for line in lines)

    def test_uses_interval_argument(self):
        """The sleep call should receive the --interval value."""
        sleep_calls: list = []

        def fake_sleep(secs):
            sleep_calls.append(secs)
            raise KeyboardInterrupt

        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={'entries': []}), \
             patch('time.sleep', side_effect=fake_sleep), \
             patch('builtins.print'):
            gateway_ctl.cmd_audit(_args(audit_action='follow', interval=7.5))
        assert sleep_calls == [7.5]

    def test_actor_filter_forwarded_to_url(self):
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={'entries': []}) as m, \
             patch('time.sleep', side_effect=KeyboardInterrupt), \
             patch('builtins.print'):
            gateway_ctl.cmd_audit(_args(audit_action='follow', interval=1.0, actor='alice'))
        assert 'actor=alice' in m.call_args[0][1]

    def test_deduplicates_repeated_entries(self):
        """An entry seen in poll 1 must not be re-printed in poll 2."""
        entry = {'ts': '2026-01-01T00:00:00Z', 'event': 'login', 'actor': 'bob', 'details': {}}
        printed: list = []
        call_count = 0

        def fake_req(method, url, token=None, **kw):
            nonlocal call_count
            call_count += 1
            return {'entries': [entry]}

        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', side_effect=fake_req), \
             patch('time.sleep', side_effect=[None, KeyboardInterrupt]), \
             patch('builtins.print',
                   side_effect=lambda *a, **kw: printed.append(str(a[0]) if a else '')):
            gateway_ctl.cmd_audit(_args(audit_action='follow', interval=1.0))
        login_lines = [line for line in printed if 'login' in line]
        assert len(login_lines) == 1
