"""Tests for gateway_ctl.py  approvals subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered sub-actions:
  list, approve, reject, timeout get, timeout set
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
    """Return the top-level subcommand name → parser map without triggering
    Pylance errors caused by accessing private argparse internals."""
    for action in parser._actions:
        if hasattr(action, '_name_parser_map'):
            return getattr(action, '_name_parser_map')
    return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(**kwargs) -> argparse.Namespace:
    """Build a fake Namespace with sensible defaults for the approvals command."""
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'fake-token',
        'appr_action': 'list',
        'id': 1,
        'timeout_action': 'get',
        'seconds': 0.0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run_approvals(args, request_return=None):
    """Invoke cmd_approvals with mocked _request and _get_token."""
    with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
         patch.object(gateway_ctl, '_request', return_value=request_return or {}) as mock_req:
        gateway_ctl.cmd_approvals(args)
        return mock_req


# ===========================================================================
# approvals list
# ===========================================================================

class TestApprovalsList:
    def test_calls_get_approvals_endpoint(self, capsys):
        mock_req = _run_approvals(
            _args(appr_action='list'),
            request_return={'pending': {}},
        )
        mock_req.assert_called_once_with(
            'GET', 'http://localhost:8080/approvals', token='fake-token'
        )

    def test_empty_queue_prints_message(self, capsys):
        _run_approvals(
            _args(appr_action='list'),
            request_return={'pending': {}},
        )
        out = capsys.readouterr().out
        assert 'empty' in out.lower() or 'no pending' in out.lower()

    def test_lists_pending_items(self, capsys):
        pending = {
            '1': {'payload': {'tool': 'file.write', 'args': {}}, 'risk': 'high', 'enqueued_at': None},
            '2': {'payload': {'tool': 'system.exec', 'args': {}}, 'risk': 'high', 'enqueued_at': None},
        }
        _run_approvals(
            _args(appr_action='list'),
            request_return={'pending': pending},
        )
        out = capsys.readouterr().out
        assert '#1' in out
        assert '#2' in out
        assert 'file.write' in out
        assert 'system.exec' in out

    def test_shows_count_in_header(self, capsys):
        pending = {
            '3': {'payload': {'tool': 'network.request', 'args': {}}, 'risk': 'high', 'enqueued_at': None},
        }
        _run_approvals(
            _args(appr_action='list'),
            request_return={'pending': pending},
        )
        out = capsys.readouterr().out
        assert '1' in out  # count
        assert 'Pending' in out


# ===========================================================================
# approvals approve
# ===========================================================================

class TestApprovalsApprove:
    def test_posts_approve_endpoint(self, capsys):
        mock_req = _run_approvals(
            _args(appr_action='approve', id=42),
            request_return={'status': 'approved', 'id': 42},
        )
        mock_req.assert_called_once_with(
            'POST', 'http://localhost:8080/approvals/42/approve', token='fake-token'
        )

    def test_prints_approved_message(self, capsys):
        _run_approvals(
            _args(appr_action='approve', id=7),
            request_return={'status': 'approved', 'id': 7},
        )
        out = capsys.readouterr().out
        assert '7' in out
        assert 'approv' in out.lower()

    def test_different_id_used(self, capsys):
        mock_req = _run_approvals(
            _args(appr_action='approve', id=99),
            request_return={'status': 'approved', 'id': 99},
        )
        assert '99' in mock_req.call_args[0][1]


# ===========================================================================
# approvals reject
# ===========================================================================

class TestApprovalsReject:
    def test_posts_reject_endpoint(self, capsys):
        mock_req = _run_approvals(
            _args(appr_action='reject', id=5),
            request_return={'status': 'rejected', 'id': 5},
        )
        mock_req.assert_called_once_with(
            'POST', 'http://localhost:8080/approvals/5/reject', token='fake-token'
        )

    def test_prints_rejected_message(self, capsys):
        _run_approvals(
            _args(appr_action='reject', id=5),
            request_return={'status': 'rejected', 'id': 5},
        )
        out = capsys.readouterr().out
        assert '5' in out
        assert 'reject' in out.lower()


# ===========================================================================
# approvals timeout get
# ===========================================================================

class TestApprovalsTimeoutGet:
    def test_calls_get_config_endpoint(self, capsys):
        mock_req = _run_approvals(
            _args(appr_action='timeout', timeout_action='get'),
            request_return={'timeout_seconds': 0},
        )
        mock_req.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/approvals/config', token='fake-token'
        )

    def test_prints_disabled_when_zero(self, capsys):
        _run_approvals(
            _args(appr_action='timeout', timeout_action='get'),
            request_return={'timeout_seconds': 0},
        )
        out = capsys.readouterr().out
        assert 'disabled' in out.lower()

    def test_prints_seconds_when_nonzero(self, capsys):
        _run_approvals(
            _args(appr_action='timeout', timeout_action='get'),
            request_return={'timeout_seconds': 120},
        )
        out = capsys.readouterr().out
        assert '120' in out
        assert 'disabled' not in out.lower()

    def test_output_contains_timeout_seconds_key(self, capsys):
        _run_approvals(
            _args(appr_action='timeout', timeout_action='get'),
            request_return={'timeout_seconds': 60},
        )
        out = capsys.readouterr().out
        assert 'timeout_seconds' in out


# ===========================================================================
# approvals timeout set
# ===========================================================================

class TestApprovalsTimeoutSet:
    def test_calls_put_config_endpoint(self, capsys):
        mock_req = _run_approvals(
            _args(appr_action='timeout', timeout_action='set', seconds=30.0),
            request_return={'timeout_seconds': 30},
        )
        mock_req.assert_called_once_with(
            'PUT', 'http://localhost:8080/admin/approvals/config',
            token='fake-token', body={'timeout_seconds': 30.0},
        )

    def test_prints_updated_value(self, capsys):
        _run_approvals(
            _args(appr_action='timeout', timeout_action='set', seconds=45.0),
            request_return={'timeout_seconds': 45},
        )
        out = capsys.readouterr().out
        assert '45' in out

    def test_zero_shown_as_disabled(self, capsys):
        _run_approvals(
            _args(appr_action='timeout', timeout_action='set', seconds=0.0),
            request_return={'timeout_seconds': 0},
        )
        out = capsys.readouterr().out
        assert 'disabled' in out.lower()

    def test_negative_exits_with_error(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request') as mock_req:
            with pytest.raises(SystemExit) as exc_info:
                gateway_ctl.cmd_approvals(
                    _args(appr_action='timeout', timeout_action='set', seconds=-1.0)
                )
            assert exc_info.value.code == 1
            mock_req.assert_not_called()


# ===========================================================================
# Parser registration
# ===========================================================================

class TestApprovalsParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_approvals_subcommand_registered(self):
        assert 'approvals' in _subcommands(self.parser)

    def test_approvals_list_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'approvals', 'list']
        )
        assert args.appr_action == 'list'
        assert args.func is gateway_ctl.cmd_approvals

    def test_approvals_approve_id_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'approvals', 'approve', '17']
        )
        assert args.appr_action == 'approve'
        assert args.id == 17

    def test_approvals_reject_id_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'approvals', 'reject', '3']
        )
        assert args.appr_action == 'reject'
        assert args.id == 3

    def test_approvals_timeout_get_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'approvals', 'timeout', 'get']
        )
        assert args.appr_action == 'timeout'
        assert args.timeout_action == 'get'

    def test_approvals_timeout_set_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'approvals', 'timeout', 'set', '90']
        )
        assert args.appr_action == 'timeout'
        assert args.timeout_action == 'set'
        assert args.seconds == 90.0
