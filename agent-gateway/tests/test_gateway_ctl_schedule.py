"""Tests for gateway_ctl.py  schedule subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  list, get, create, delete, enable, disable, trigger, history
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

_TASK_A = {
    'id': 'aaaabbbb-0000-0000-0000-000000000001',
    'name': 'Daily Backup',
    'tool': 'file.write',
    'args': {'path': '/tmp/bak'},
    'interval_seconds': 86400,
    'enabled': True,
    'run_count': 5,
}

_TASK_B = {
    'id': 'aaaabbbb-0000-0000-0000-000000000002',
    'name': 'Hourly Probe',
    'tool': 'echo',
    'args': {},
    'interval_seconds': 3600,
    'enabled': False,
    'run_count': 0,
}

_HISTORY = [
    {'run': 1, 'timestamp': '2026-02-21T00:00:00Z', 'ok': True, 'duration_seconds': 0.12, 'error': None},
    {'run': 2, 'timestamp': '2026-02-21T01:00:00Z', 'ok': False, 'duration_seconds': 0.01, 'error': 'timeout'},
]


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'tok',
        'sched_action': 'list',
        'task_id': '',
        'name': '',
        'tool': '',
        'args': '{}',
        'interval': 3600,
        'disabled': False,
        'n': None,
        'next': False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, ret=None):
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_schedule(args)
    return m


# ===========================================================================
# list
# ===========================================================================

class TestScheduleList:
    def test_calls_get_admin_schedule(self):
        m = _run(_args(sched_action='list'), ret={'tasks': [_TASK_A]})
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/schedule', token='tok'
        )

    def test_prints_task_name_and_tool(self, capsys):
        _run(_args(sched_action='list'), ret={'tasks': [_TASK_A, _TASK_B]})
        out = capsys.readouterr().out
        assert 'Daily Backup' in out
        assert 'file.write' in out
        assert 'Hourly Probe' in out

    def test_prints_empty_message_when_no_tasks(self, capsys):
        _run(_args(sched_action='list'), ret={'tasks': []})
        assert 'No scheduled tasks' in capsys.readouterr().out

    def test_enabled_indicator_present(self, capsys):
        _run(_args(sched_action='list'), ret={'tasks': [_TASK_A, _TASK_B]})
        out = capsys.readouterr().out
        # enabled rendered differently from disabled (bullet vs circle)
        assert out.count('●') + out.count('○') >= 2  # any mix of both symbols

    def test_lists_multiple_tasks(self, capsys):
        _run(_args(sched_action='list'), ret={'tasks': [_TASK_A, _TASK_B]})
        out = capsys.readouterr().out
        assert 'aaaabbbb' in out   # ID prefix


# ===========================================================================
# get
# ===========================================================================

class TestScheduleGet:
    def test_calls_get_task_endpoint(self):
        m = _run(_args(sched_action='get', task_id='abc-123'), ret=_TASK_A)
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/schedule/abc-123', token='tok'
        )


# ===========================================================================
# create
# ===========================================================================

class TestScheduleCreate:
    def test_calls_post_admin_schedule(self):
        m = _run(_args(sched_action='create', name='Test', tool='echo',
                       args='{}', interval=60, disabled=False))
        m.assert_called_once_with(
            'POST', 'http://localhost:8080/admin/schedule',
            token='tok',
            body={'name': 'Test', 'tool': 'echo', 'args': {}, 'interval_seconds': 60},
        )

    def test_disabled_flag_sets_enabled_false(self):
        m = _run(_args(sched_action='create', name='T', tool='echo',
                       args='{}', interval=600, disabled=True))
        body = m.call_args.kwargs['body']
        assert body.get('enabled') is False

    def test_invalid_args_json_does_not_crash(self, capsys):
        # must print error and return without calling _request
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request') as m:
            gateway_ctl.cmd_schedule(
                _args(sched_action='create', name='T', tool='echo', args='NOT JSON')
            )
        m.assert_not_called()

    def test_args_json_parsed(self):
        m = _run(_args(sched_action='create', name='T', tool='echo',
                       args='{"x": 1}', interval=60, disabled=False))
        body = m.call_args.kwargs['body']
        assert body['args'] == {'x': 1}


# ===========================================================================
# delete
# ===========================================================================

class TestScheduleDelete:
    def test_calls_delete_endpoint(self):
        m = _run(_args(sched_action='delete', task_id='abc-123'))
        m.assert_called_once_with(
            'DELETE', 'http://localhost:8080/admin/schedule/abc-123', token='tok'
        )


# ===========================================================================
# enable / disable
# ===========================================================================

class TestScheduleEnableDisable:
    def test_enable_sends_patch_enabled_true(self):
        m = _run(_args(sched_action='enable', task_id='abc-123'))
        m.assert_called_once_with(
            'PATCH', 'http://localhost:8080/admin/schedule/abc-123',
            token='tok', body={'enabled': True}
        )

    def test_disable_sends_patch_enabled_false(self):
        m = _run(_args(sched_action='disable', task_id='abc-123'))
        m.assert_called_once_with(
            'PATCH', 'http://localhost:8080/admin/schedule/abc-123',
            token='tok', body={'enabled': False}
        )


# ===========================================================================
# trigger
# ===========================================================================

class TestScheduleTrigger:
    def test_calls_trigger_endpoint(self):
        m = _run(_args(sched_action='trigger', task_id='abc-123'))
        m.assert_called_once_with(
            'POST', 'http://localhost:8080/admin/schedule/abc-123/trigger',
            token='tok'
        )


# ===========================================================================
# history
# ===========================================================================

class TestScheduleHistory:
    def test_calls_history_endpoint(self):
        m = _run(_args(sched_action='history', task_id='abc-123', n=None),
                 ret={'history': _HISTORY})
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/schedule/abc-123/history',
            token='tok'
        )

    def test_limit_n_appended_to_url(self):
        m = _run(_args(sched_action='history', task_id='abc-123', n=10),
                 ret={'history': _HISTORY})
        url = m.call_args.args[1]
        assert '?limit=10' in url

    def test_prints_history_records(self, capsys):
        _run(_args(sched_action='history', task_id='abc-123', n=None),
             ret={'history': _HISTORY})
        out = capsys.readouterr().out
        assert '2026-02-21' in out
        assert 'timeout' in out

    def test_empty_history_message(self, capsys):
        _run(_args(sched_action='history', task_id='abc-123', n=None),
             ret={'history': []})
        assert 'No history' in capsys.readouterr().out


# ===========================================================================
# Parser
# ===========================================================================

class TestScheduleParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_schedule_registered(self):
        assert 'schedule' in _subcommands(self.parser)

    def test_list_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'schedule', 'list'])
        assert args.sched_action == 'list'
        assert args.func is gateway_ctl.cmd_schedule

    def test_create_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'schedule', 'create', 'MyTask', 'echo',
             '--interval', '120', '--args', '{"q": 1}', '--disabled']
        )
        assert args.sched_action == 'create'
        assert args.name == 'MyTask'
        assert args.tool == 'echo'
        assert args.interval == 120
        assert args.disabled is True

    def test_history_limit_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'schedule', 'history', 'abc-123', '--n', '25']
        )
        assert args.task_id == 'abc-123'
        assert args.n == 25

    def test_enable_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'schedule', 'enable', 'xyz']
        )
        assert args.sched_action == 'enable'
        assert args.task_id == 'xyz'

    def test_trigger_parsed(self):
        args = self.parser.parse_args(
            ['--token', 'x', 'schedule', 'trigger', 'xyz']
        )
        assert args.sched_action == 'trigger'

    def test_list_next_not_set_by_default(self):
        args = self.parser.parse_args(['--token', 'x', 'schedule', 'list'])
        assert not getattr(args, 'next', False)

    def test_list_next_flag_parsed(self):
        args = self.parser.parse_args(['--token', 'x', 'schedule', 'list', '--next'])
        assert args.next is True


# ===========================================================================
# list --next  (behaviour)
# ===========================================================================

class TestScheduleListNext:
    """Tests for the schedule list --next countdown annotation."""

    @staticmethod
    def _future(seconds: float) -> str:
        from datetime import datetime, timezone, timedelta
        return (datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)).isoformat()

    @staticmethod
    def _past(seconds: float) -> str:
        from datetime import datetime, timezone, timedelta
        return (datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)).isoformat()

    @staticmethod
    def _task(name: str = 'T', next_run_at: str = '', **kwargs) -> dict:
        from copy import deepcopy
        t = deepcopy(_TASK_A)
        t['name'] = name
        if next_run_at:
            t['next_run_at'] = next_run_at
        else:
            t.pop('next_run_at', None)
        t.update(kwargs)
        return t

    def test_overdue_annotation(self, capsys):
        task = self._task(next_run_at=self._past(300))
        _run(_args(sched_action='list', next=True), ret={'tasks': [task]})
        assert '(overdue)' in capsys.readouterr().out

    def test_seconds_annotation(self, capsys):
        task = self._task(next_run_at=self._future(30))
        _run(_args(sched_action='list', next=True), ret={'tasks': [task]})
        out = capsys.readouterr().out
        assert 'in ' in out and 's' in out

    def test_minutes_annotation(self, capsys):
        task = self._task(next_run_at=self._future(600))   # 10 minutes
        _run(_args(sched_action='list', next=True), ret={'tasks': [task]})
        out = capsys.readouterr().out
        assert 'in ' in out and 'm' in out

    def test_hours_annotation(self, capsys):
        task = self._task(next_run_at=self._future(7200))  # 2 hours
        _run(_args(sched_action='list', next=True), ret={'tasks': [task]})
        out = capsys.readouterr().out
        assert 'in ' in out and 'h' in out

    def test_no_next_run_at_does_not_crash(self):
        task = self._task()   # no next_run_at key
        _run(_args(sched_action='list', next=True), ret={'tasks': [task]})  # must not raise

    def test_without_next_flag_no_annotation(self, capsys):
        task = self._task(next_run_at=self._past(300))
        _run(_args(sched_action='list', next=False), ret={'tasks': [task]})
        assert 'overdue' not in capsys.readouterr().out

    def test_sorts_ascending_by_next_run(self, capsys):
        t_soon  = self._task('AlphaSoon',  next_run_at=self._future(10))
        t_later = self._task('ZetaLater', next_run_at=self._future(7200))
        # pass in reverse order; --next should sort by next_run_at ascending
        _run(_args(sched_action='list', next=True), ret={'tasks': [t_later, t_soon]})
        out = capsys.readouterr().out
        assert out.index('AlphaSoon') < out.index('ZetaLater')
