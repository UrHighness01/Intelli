"""Tests for gateway_ctl.py  content-filter subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered:  cmd_content_filter (list / add / delete / reload)  /  parser registration
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import patch, call as mock_call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gateway_ctl  # noqa: E402
from gateway_ctl import cmd_content_filter, _build_parser  # noqa: E402


def _subcommands(parser: argparse.ArgumentParser) -> dict:
    """Return the top-level subcommand name → parser map."""
    for action in parser._actions:
        if hasattr(action, '_name_parser_map'):
            return getattr(action, '_name_parser_map')
    return {}


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'tok',
        'cf_action': 'list',
        'pattern': '',
        'mode': 'literal',
        'label': '',
        'index': 0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(result, **kwargs):
    """Run cmd_content_filter with mocked _request and collect printed lines."""
    args = _args(**kwargs)
    lines = []
    with patch.object(gateway_ctl, '_get_token', return_value='tok'):
        with patch.object(gateway_ctl, '_request', return_value=result):
            with patch('builtins.print',
                       side_effect=lambda *a, **kw: lines.append(str(a[0]) if a else '')):
                cmd_content_filter(args)
    return lines


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_RULES_EMPTY = {'rules': []}

_RULES_ONE = {'rules': [
    {'pattern': 'badword', 'mode': 'literal', 'label': 'profanity'},
]}

_RULES_MANY = {'rules': [
    {'pattern': 'badword',    'mode': 'literal', 'label': 'profanity'},
    {'pattern': r'\bsecret\b', 'mode': 'regex',   'label': 'secrets'},
    {'pattern': 'spam',       'mode': 'literal', 'label': ''},
]}


# ---------------------------------------------------------------------------
# TestContentFilterList
# ---------------------------------------------------------------------------

class TestContentFilterList:

    def test_calls_get_endpoint(self):
        args = _args(cf_action='list')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value=_RULES_ONE) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        m.assert_called_once()
        assert '/admin/content-filter/rules' in m.call_args[0][1]

    def test_empty_message(self):
        lines = _run(_RULES_EMPTY, cf_action='list')
        assert any('No content-filter rules' in l for l in lines)

    def test_pattern_printed(self):
        lines = _run(_RULES_ONE, cf_action='list')
        output = '\n'.join(lines)
        assert 'badword' in output

    def test_mode_printed(self):
        lines = _run(_RULES_MANY, cf_action='list')
        output = '\n'.join(lines)
        assert 'literal' in output
        assert 'regex' in output

    def test_label_printed(self):
        lines = _run(_RULES_ONE, cf_action='list')
        output = '\n'.join(lines)
        assert 'profanity' in output

    def test_rule_count_in_footer(self):
        lines = _run(_RULES_MANY, cf_action='list')
        output = '\n'.join(lines)
        assert '3 rule' in output

    def test_index_numbers_shown(self):
        lines = _run(_RULES_MANY, cf_action='list')
        output = '\n'.join(lines)
        assert '0' in output
        assert '1' in output
        assert '2' in output

    def test_header_columns(self):
        lines = _run(_RULES_ONE, cf_action='list')
        output = '\n'.join(lines)
        assert 'Mode' in output
        assert 'Label' in output
        assert 'Pattern' in output


# ---------------------------------------------------------------------------
# TestContentFilterAdd
# ---------------------------------------------------------------------------

class TestContentFilterAdd:

    def test_calls_post_endpoint(self):
        args = _args(cf_action='add', pattern='evil', mode='literal', label='')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={'ok': True}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        m.assert_called_once()
        assert '/admin/content-filter/rules' in m.call_args[0][1]
        assert m.call_args[0][0] == 'POST'

    def test_pattern_in_body(self):
        args = _args(cf_action='add', pattern='evil', mode='literal', label='')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        body = m.call_args[1].get('body') or m.call_args[0][3] if len(m.call_args[0]) > 3 else m.call_args[1].get('body')
        # check via kwargs
        sent = m.call_args
        body = sent.kwargs.get('body') or (sent.args[3] if len(sent.args) > 3 else None)
        assert body is not None
        assert body.get('pattern') == 'evil'

    def test_mode_literal_sent(self):
        args = _args(cf_action='add', pattern='x', mode='literal', label='')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        sent = m.call_args
        body = sent.kwargs.get('body') or (sent.args[3] if len(sent.args) > 3 else None)
        assert body is not None
        assert body.get('mode') == 'literal'

    def test_mode_regex_sent(self):
        args = _args(cf_action='add', pattern=r'\btest\b', mode='regex', label='')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        sent = m.call_args
        body = sent.kwargs.get('body') or (sent.args[3] if len(sent.args) > 3 else None)
        assert body is not None
        assert body.get('mode') == 'regex'

    def test_label_included_when_set(self):
        args = _args(cf_action='add', pattern='spam', mode='literal', label='junk mail')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        sent = m.call_args
        body = sent.kwargs.get('body') or (sent.args[3] if len(sent.args) > 3 else None)
        assert body is not None
        assert body.get('label') == 'junk mail'

    def test_label_omitted_when_empty(self):
        args = _args(cf_action='add', pattern='spam', mode='literal', label='')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        sent = m.call_args
        body = sent.kwargs.get('body') or (sent.args[3] if len(sent.args) > 3 else None)
        assert body is not None
        assert 'label' not in body


# ---------------------------------------------------------------------------
# TestContentFilterDelete
# ---------------------------------------------------------------------------

class TestContentFilterDelete:

    def test_calls_delete_endpoint(self):
        args = _args(cf_action='delete', index=0)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={'deleted': True}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        m.assert_called_once()
        assert m.call_args[0][0] == 'DELETE'
        assert '/admin/content-filter/rules/0' in m.call_args[0][1]

    def test_index_in_url(self):
        args = _args(cf_action='delete', index=3)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        assert '/admin/content-filter/rules/3' in m.call_args[0][1]


# ---------------------------------------------------------------------------
# TestContentFilterReload
# ---------------------------------------------------------------------------

class TestContentFilterReload:

    def test_calls_reload_endpoint(self):
        args = _args(cf_action='reload')
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={'rules_loaded': 4}) as m:
                with patch('builtins.print'):
                    cmd_content_filter(args)
        m.assert_called_once()
        assert m.call_args[0][0] == 'POST'
        assert '/admin/content-filter/reload' in m.call_args[0][1]

    def test_count_printed_rules_loaded(self):
        lines = _run({'rules_loaded': 4}, cf_action='reload')
        assert any('4' in l for l in lines)

    def test_count_printed_count_fallback(self):
        lines = _run({'count': 7}, cf_action='reload')
        assert any('7' in l for l in lines)

    def test_reload_message(self):
        lines = _run({'rules_loaded': 2}, cf_action='reload')
        output = '\n'.join(lines)
        assert 'Reload' in output or 'reload' in output.lower()


# ---------------------------------------------------------------------------
# TestContentFilterParser
# ---------------------------------------------------------------------------

class TestContentFilterParser:

    def setup_method(self):
        self.parser = _build_parser()

    def test_registered(self):
        assert 'content-filter' in _subcommands(self.parser)

    def test_list_action(self):
        args = self.parser.parse_args(['--token', 't', 'content-filter', 'list'])
        assert args.cf_action == 'list'

    def test_add_action(self):
        args = self.parser.parse_args(['--token', 't', 'content-filter', 'add', 'bad'])
        assert args.cf_action == 'add'
        assert args.pattern == 'bad'

    def test_add_mode_default(self):
        args = self.parser.parse_args(['--token', 't', 'content-filter', 'add', 'bad'])
        assert args.mode == 'literal'

    def test_add_mode_regex(self):
        args = self.parser.parse_args(
            ['--token', 't', 'content-filter', 'add', r'\btest\b', '--mode', 'regex'])
        assert args.mode == 'regex'

    def test_add_label_flag(self):
        args = self.parser.parse_args(
            ['--token', 't', 'content-filter', 'add', 'spam', '--label', 'junk'])
        assert args.label == 'junk'

    def test_delete_action(self):
        args = self.parser.parse_args(['--token', 't', 'content-filter', 'delete', '2'])
        assert args.cf_action == 'delete'
        assert args.index == 2

    def test_reload_action(self):
        args = self.parser.parse_args(['--token', 't', 'content-filter', 'reload'])
        assert args.cf_action == 'reload'

    def test_func_wired(self):
        args = self.parser.parse_args(['--token', 't', 'content-filter', 'list'])
        assert args.func is cmd_content_filter
