"""Tests for gateway_ctl.py  alerts subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered sub-actions:
  status, set
"""
from __future__ import annotations

import argparse
import sys
import os
from io import StringIO
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gateway_ctl  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(**kwargs) -> argparse.Namespace:
    """Build a fake Namespace with sensible defaults for the alerts command."""
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'fake-token',
        'alert_action': 'status',
        'threshold': 0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run_alerts(args, request_return=None, capsys=None):
    """Invoke cmd_alerts with mocked _request and _get_token."""
    with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
         patch.object(gateway_ctl, '_request', return_value=request_return or {}) as mock_req:
        gateway_ctl.cmd_alerts(args)
        return mock_req


# ===========================================================================
# alerts status
# ===========================================================================

class TestAlertsStatus:
    def test_calls_get_endpoint(self, capsys):
        mock_req = _run_alerts(
            _args(alert_action='status'),
            request_return={'approval_queue_threshold': 0},
        )
        mock_req.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/alerts/config', token='fake-token'
        )

    def test_prints_disabled_when_zero(self, capsys):
        _run_alerts(
            _args(alert_action='status'),
            request_return={'approval_queue_threshold': 0},
        )
        out = capsys.readouterr().out
        assert 'disabled' in out.lower()
        assert '0' in out

    def test_prints_active_when_nonzero(self, capsys):
        _run_alerts(
            _args(alert_action='status'),
            request_return={'approval_queue_threshold': 5},
        )
        out = capsys.readouterr().out
        assert '5' in out
        assert 'disabled' not in out.lower()

    def test_output_contains_threshold_key(self, capsys):
        _run_alerts(
            _args(alert_action='status'),
            request_return={'approval_queue_threshold': 10},
        )
        out = capsys.readouterr().out
        assert 'approval_queue_threshold' in out
        assert '10' in out


# ===========================================================================
# alerts set
# ===========================================================================

class TestAlertsSet:
    def test_calls_put_endpoint_with_threshold(self, capsys):
        mock_req = _run_alerts(
            _args(alert_action='set', threshold=3),
            request_return={'approval_queue_threshold': 3},
        )
        mock_req.assert_called_once_with(
            'PUT', 'http://localhost:8080/admin/alerts/config',
            token='fake-token',
            body={'approval_queue_threshold': 3},
        )

    def test_prints_updated_threshold(self, capsys):
        _run_alerts(
            _args(alert_action='set', threshold=7),
            request_return={'approval_queue_threshold': 7},
        )
        out = capsys.readouterr().out
        assert '7' in out

    def test_set_zero_prints_disabled(self, capsys):
        _run_alerts(
            _args(alert_action='set', threshold=0),
            request_return={'approval_queue_threshold': 0},
        )
        out = capsys.readouterr().out
        assert 'disabled' in out.lower()

    def test_negative_threshold_exits(self, capsys):
        with pytest.raises(SystemExit):
            _run_alerts(
                _args(alert_action='set', threshold=-1),
                request_return={},
            )


# ===========================================================================
# Parser round-trip
# ===========================================================================

class TestAlertsParser:
    def test_parser_registers_alerts_subcommand(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args(['alerts', 'status'])
        assert args.alert_action == 'status'
        assert args.func == gateway_ctl.cmd_alerts

    def test_parser_alerts_set_parses_threshold(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args(['alerts', 'set', '5'])
        assert args.alert_action == 'set'
        assert args.threshold == 5

    def test_parser_alerts_set_zero(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args(['alerts', 'set', '0'])
        assert args.threshold == 0
