"""Tests for gateway_ctl.py  metrics subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered:  cmd_metrics (tools / top)  /  parser registration
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gateway_ctl  # noqa: E402
from gateway_ctl import cmd_metrics, _build_parser  # noqa: E402


def _subcommands(parser: argparse.ArgumentParser) -> dict:
    """Return the top-level subcommand name → parser map."""
    for action in parser._actions:
        if hasattr(action, '_name_parser_map'):
            return getattr(action, '_name_parser_map')
    return {}


def _args(**kwargs) -> argparse.Namespace:
    defaults = {'url': 'http://localhost:8080', 'token': 'tok', 'met_action': 'tools'}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_SAMPLE_NO_LAT = {
    'tools': [
        {'tool': 'file.read',  'calls': 50},
        {'tool': 'noop',       'calls': 12},
    ],
    'total': 62,
}

_SAMPLE_WITH_LAT = {
    'tools': [
        {'tool': 'file.read',  'calls': 50, 'p50_seconds': 0.025, 'mean_seconds': 0.030},
        {'tool': 'noop',       'calls': 12, 'p50_seconds': 0.001, 'mean_seconds': 0.002},
    ],
    'total': 62,
}

_SAMPLE_MANY = {
    'tools': [{'tool': f'tool_{i:02d}', 'calls': 100 - i} for i in range(10)],
    'total': sum(100 - i for i in range(10)),
}


# ---------------------------------------------------------------------------
# TestMetricsTools
# ---------------------------------------------------------------------------

class TestMetricsTools:

    def _run(self, result, captured=None):
        args = _args(met_action='tools')
        lines = [] if captured is None else captured
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value=result):
                with patch('builtins.print', side_effect=lambda *a, **kw: lines.append(str(a[0]) if a else '')):
                    cmd_metrics(args)
        return lines

    def test_calls_endpoint(self):
        args = _args(met_action='tools')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value=_SAMPLE_NO_LAT) as m:
                with patch('builtins.print'):
                    cmd_metrics(args)
        m.assert_called_once()
        assert '/admin/metrics/tools' in m.call_args[0][1]

    def test_empty_message(self):
        lines = self._run({'tools': [], 'total': 0})
        assert any('No tool calls recorded yet.' in l for l in lines)

    def test_tool_name_printed(self):
        lines = self._run(_SAMPLE_NO_LAT)
        output = '\n'.join(lines)
        assert 'file.read' in output
        assert 'noop' in output

    def test_call_count_printed(self):
        lines = self._run(_SAMPLE_NO_LAT)
        output = '\n'.join(lines)
        assert '50' in output
        assert '12' in output

    def test_p50_shown_when_present(self):
        lines = self._run(_SAMPLE_WITH_LAT)
        output = '\n'.join(lines)
        # 0.025 s → 25.0 ms; 0.001 s → 1.0 ms
        assert '25.0' in output
        assert '1.0' in output

    def test_mean_shown_when_present(self):
        lines = self._run(_SAMPLE_WITH_LAT)
        output = '\n'.join(lines)
        # 0.030 s → 30.0 ms; 0.002 s → 2.0 ms
        assert '30.0' in output
        assert '2.0' in output

    def test_p50_absent_shows_dash(self):
        lines = self._run(_SAMPLE_NO_LAT)
        output = '\n'.join(lines)
        # Unicode em-dash for missing latency
        assert '—' in output

    def test_total_line_printed(self):
        lines = self._run(_SAMPLE_NO_LAT)
        output = '\n'.join(lines)
        assert 'Total: 62' in output

    def test_tools_count_in_total_line(self):
        lines = self._run(_SAMPLE_NO_LAT)
        output = '\n'.join(lines)
        assert '2 tool' in output

    def test_header_line_present(self):
        lines = self._run(_SAMPLE_NO_LAT)
        output = '\n'.join(lines)
        assert 'Tool' in output
        assert 'Calls' in output
        assert 'p50 ms' in output
        assert 'Mean ms' in output


# ---------------------------------------------------------------------------
# TestMetricsTop
# ---------------------------------------------------------------------------

class TestMetricsTop:

    def _run(self, result, n=3):
        args = _args(met_action='top', n=n)
        lines = []
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value=result):
                with patch('builtins.print', side_effect=lambda *a, **kw: lines.append(str(a[0]) if a else '')):
                    cmd_metrics(args)
        return lines

    def test_limits_to_n(self):
        lines = self._run(_SAMPLE_MANY, n=3)
        output = '\n'.join(lines)
        assert 'tool_00' in output
        assert 'tool_01' in output
        assert 'tool_02' in output
        assert 'tool_03' not in output

    def test_top1(self):
        lines = self._run(_SAMPLE_MANY, n=1)
        output = '\n'.join(lines)
        assert 'tool_00' in output
        assert 'tool_01' not in output

    def test_top_n_default_uses_five(self):
        """Parser default must be 5 — builder uses n=5 in set_defaults."""
        parser = _build_parser()
        args = parser.parse_args(['--token', 't', 'metrics', 'top'])
        assert args.n == 5

    def test_empty_when_no_data(self):
        lines = self._run({'tools': [], 'total': 0}, n=5)
        assert any('No tool calls recorded yet.' in l for l in lines)

    def test_calls_same_endpoint_as_tools(self):
        args = _args(met_action='top', n=5)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value=_SAMPLE_MANY) as m:
                with patch('builtins.print'):
                    cmd_metrics(args)
        assert '/admin/metrics/tools' in m.call_args[0][1]


# ---------------------------------------------------------------------------
# TestMetricsParser
# ---------------------------------------------------------------------------

class TestMetricsParser:

    def setup_method(self):
        self.parser = _build_parser()

    def test_registered(self):
        assert 'metrics' in _subcommands(self.parser)

    def test_tools_action(self):
        args = self.parser.parse_args(['--token', 't', 'metrics', 'tools'])
        assert args.met_action == 'tools'

    def test_top_action(self):
        args = self.parser.parse_args(['--token', 't', 'metrics', 'top'])
        assert args.met_action == 'top'

    def test_top_n_flag(self):
        args = self.parser.parse_args(['--token', 't', 'metrics', 'top', '--n', '10'])
        assert args.n == 10

    def test_top_n_default_is_five(self):
        args = self.parser.parse_args(['--token', 't', 'metrics', 'top'])
        assert args.n == 5

    def test_func_wired_tools(self):
        args = self.parser.parse_args(['--token', 't', 'metrics', 'tools'])
        assert args.func is cmd_metrics

    def test_func_wired_top(self):
        args = self.parser.parse_args(['--token', 't', 'metrics', 'top'])
        assert args.func is cmd_metrics
