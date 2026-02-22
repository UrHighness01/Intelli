"""Tests for gateway_ctl.py  memory subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered sub-actions:
  agents, list, get, set, delete, prune, clear, export (stdout + file), import
"""
from __future__ import annotations

import argparse
import json
import sys
import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gateway_ctl  # noqa: E402 — imported after sys.path fix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(**kwargs) -> argparse.Namespace:
    """Build a fake Namespace with sensible defaults for the memory command."""
    defaults = {
        'url': 'http://localhost:8080',
        'token': 'fake-token',
        'mem_action': 'agents',
        'agent_id': 'agent-1',
        'key': 'greeting',
        'value': '"hello"',
        'ttl': None,
        'output': '',
        'file': '',
        'replace': False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(args, request_return=None, capsys=None):
    """Invoke cmd_memory with mocked _request and _get_token."""
    with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
         patch.object(gateway_ctl, '_request', return_value=request_return or {}) as mock_req:
        gateway_ctl.cmd_memory(args)
        return mock_req


# ===========================================================================
# Tests
# ===========================================================================

class TestMemoryCLI:
    def test_agents_calls_get_agents(self, capsys):
        mock_req = _run(_args(mem_action='agents'),
                        request_return={'agents': ['bot-1', 'bot-2']})
        call = mock_req.call_args
        assert call[0][0] == 'GET'
        assert '/agents' in call[0][1]

    def test_agents_prints_each_agent(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request', return_value={'agents': ['bot-1', 'bot-2']}):
            gateway_ctl.cmd_memory(_args(mem_action='agents'))
        out = capsys.readouterr().out
        assert 'bot-1' in out
        assert 'bot-2' in out

    def test_agents_prints_empty_message(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request', return_value={'agents': []}):
            gateway_ctl.cmd_memory(_args(mem_action='agents'))
        out = capsys.readouterr().out
        assert 'No agents' in out

    def test_list_calls_correct_endpoint(self):
        mock_req = _run(_args(mem_action='list', agent_id='my-bot'),
                        request_return={'memory': {}})
        call = mock_req.call_args
        assert '/agents/my-bot/memory' in call[0][1]

    def test_list_prints_keys(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request',
                          return_value={'memory': {'k1': 'v1', 'k2': 42}}):
            gateway_ctl.cmd_memory(_args(mem_action='list', agent_id='my-bot'))
        out = capsys.readouterr().out
        assert 'k1' in out
        assert 'k2' in out

    def test_get_calls_correct_endpoint(self):
        mock_req = _run(_args(mem_action='get', agent_id='my-bot', key='color'),
                        request_return={'value': 'blue'})
        call = mock_req.call_args
        assert '/agents/my-bot/memory/color' in call[0][1]

    def test_set_posts_key_value(self):
        mock_req = _run(_args(mem_action='set', agent_id='my-bot', key='x', value='"hello"'),
                        request_return={'key': 'x', 'value': 'hello'})
        call = mock_req.call_args
        assert call[0][0] == 'POST'
        assert '/agents/my-bot/memory' in call[0][1]
        body = call[1].get('body') or call[0][3] if len(call[0]) > 3 else None
        # body is passed as kwarg
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body is not None
        assert body['key'] == 'x'
        assert body['value'] == 'hello'

    def test_set_parses_json_value(self):
        mock_req = _run(_args(mem_action='set', agent_id='a', key='k', value='{"x":1}'),
                        request_return={})
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body['value'] == {'x': 1}

    def test_set_plain_string_value(self):
        mock_req = _run(_args(mem_action='set', agent_id='a', key='k', value='notjson'),
                        request_return={})
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body['value'] == 'notjson'

    def test_set_with_ttl(self):
        mock_req = _run(_args(mem_action='set', agent_id='a', key='k', value='"v"', ttl=300),
                        request_return={})
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body.get('ttl_seconds') == 300

    def test_delete_calls_correct_endpoint(self):
        mock_req = _run(_args(mem_action='delete', agent_id='bot', key='foo'),
                        request_return={})
        call = mock_req.call_args
        assert call[0][0] == 'DELETE'
        assert '/agents/bot/memory/foo' in call[0][1]

    def test_prune_prints_count(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request', return_value={'pruned': 5}):
            gateway_ctl.cmd_memory(_args(mem_action='prune', agent_id='bot'))
        out = capsys.readouterr().out
        assert '5' in out

    def test_clear_calls_delete_agent_memory(self):
        mock_req = _run(_args(mem_action='clear', agent_id='bot'), request_return={})
        call = mock_req.call_args
        assert call[0][0] == 'DELETE'
        assert '/agents/bot/memory' in call[0][1]

    def test_export_prints_to_stdout(self, capsys):
        export_data = {'agents': {}, 'agent_count': 0, 'key_count': 0, 'exported_at': 'now'}
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request', return_value=export_data):
            gateway_ctl.cmd_memory(_args(mem_action='export', output=''))
        out = capsys.readouterr().out
        assert 'agent_count' in out or len(out) > 0

    def test_export_writes_file(self, tmp_path):
        export_data = {'agents': {'bot': {'k': 'v'}}, 'agent_count': 1, 'key_count': 1, 'exported_at': 'now'}
        out_file = str(tmp_path / 'backup.json')
        with patch.object(gateway_ctl, '_get_token', return_value='fake-token'), \
             patch.object(gateway_ctl, '_request', return_value=export_data):
            gateway_ctl.cmd_memory(_args(mem_action='export', output=out_file))
        written = json.loads(Path(out_file).read_text())
        assert written['agent_count'] == 1

    def test_import_reads_file_and_posts(self, tmp_path):
        import_file = tmp_path / 'mem.json'
        mem_data = {'agents': {'a': {'x': '1'}}, 'agent_count': 1}
        import_file.write_text(json.dumps(mem_data))
        mock_req = _run(_args(mem_action='import', file=str(import_file)),
                        request_return={'imported_agents': 1})
        call = mock_req.call_args
        assert call[0][0] == 'POST'
        assert '/admin/memory/import' in call[0][1]
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body['merge'] is True   # default — no --replace

    def test_import_replace_flag(self, tmp_path):
        import_file = tmp_path / 'mem.json'
        import_file.write_text(json.dumps({'agents': {}}))
        mock_req = _run(_args(mem_action='import', file=str(import_file), replace=True),
                        request_return={})
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body['merge'] is False


# ===========================================================================
# Test webhooks add --secret in CLI parser
# ===========================================================================

class TestWebhooksAddSecret:
    def test_parser_accepts_secret(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args([
            'webhooks', 'add', 'https://hook.test/',
            '--secret', 'mysecret',
        ])
        assert args.secret == 'mysecret'

    def test_parser_secret_defaults_empty(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args(['webhooks', 'add', 'https://hook.test/'])
        assert args.secret == ''

    def test_cmd_webhooks_passes_secret_in_body(self):
        a = argparse.Namespace(
            url='http://localhost:8080',
            token='tok',
            wh_action='add',
            wh_url='https://hook.test/',
            events='',
            secret='supersecret',
        )
        # Need wh_add's positional arg to be accessible via args.url in cmd_webhooks
        # The add handler uses args.url for the webhook URL
        a.url_gw = 'http://localhost:8080'  # gateway URL is stored in args.url by _build_parser
        # Simulate: args.url is the webhook URL (positional), and the gateway URL is in args.url
        # Actually in the real parser args.url is the positional "url" for the webhook, and the
        # gateway base url comes from --url global option stored as args.url — let's inspect:
        parser = gateway_ctl._build_parser()
        ns = parser.parse_args([
            '--url', 'http://localhost:8080',
            'webhooks', 'add', 'https://hook.test/',
            '--secret', 'sec123',
        ])
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={'id': 'x', 'url': 'https://hook.test/'}) as mock_req:
            gateway_ctl.cmd_webhooks(ns)
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        assert body.get('secret') == 'sec123'

    def test_cmd_webhooks_no_secret_key_when_empty(self):
        parser = gateway_ctl._build_parser()
        ns = parser.parse_args([
            '--url', 'http://localhost:8080',
            'webhooks', 'add', 'https://hook.test/',
        ])
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', return_value={}) as mock_req:
            gateway_ctl.cmd_webhooks(ns)
        body = mock_req.call_args.kwargs.get('body') or mock_req.call_args[1].get('body')
        # empty secret should NOT be forwarded (falsy guard)
        assert 'secret' not in body


# ===========================================================================
# Tests for memory list --meta
# ===========================================================================

class TestMemoryListMeta:
    """Tests for the --meta flag on memory list (shows per-key expiry info)."""

    def _list_with_meta(self, list_return, key_returns: dict):
        """Run cmd_memory list --meta; key_returns maps key -> _request response."""
        call_responses = {}

        def _side_effect(method, url, **kwargs):
            # Match the per-key metadata requests
            for key, resp in key_returns.items():
                if f'/memory/{key}' in url:
                    return resp
            return list_return

        args = _args(mem_action='list', agent_id='bot', meta=True)
        lines = []
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', side_effect=_side_effect):
            with patch('builtins.print',
                       side_effect=lambda *a, **kw: lines.append(str(a[0]) if a else '')):
                gateway_ctl.cmd_memory(args)
        return lines

    def test_without_meta_no_expiry_shown(self, capsys):
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request',
                          return_value={'memory': {'k': 'v'}}):
            gateway_ctl.cmd_memory(_args(mem_action='list', agent_id='bot'))
        out = capsys.readouterr().out
        assert 'expir' not in out.lower()
        assert 'no expiry' not in out

    def test_with_meta_fetches_per_key(self):
        """With --meta, a second _request call per key is made to get metadata."""
        request_mock = MagicMock(return_value={'memory': {'k': 'v'}})
        # Subsequent calls return metadata
        request_mock.side_effect = [
            {'memory': {'k': 'v'}},                    # list call
            {'key': 'k', 'value': 'v', 'expires_at': None},  # meta call
        ]
        args = _args(mem_action='list', agent_id='bot', meta=True)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', request_mock):
            with patch('builtins.print'):
                gateway_ctl.cmd_memory(args)
        # At minimum 2 _request calls: list + 1 meta per key
        assert request_mock.call_count >= 2

    def test_no_expiry_shown_as_no_expiry(self, capsys):
        list_r = {'memory': {'greeting': 'hello'}}
        meta_r = {'key': 'greeting', 'value': 'hello', 'expires_at': None}

        def _side(method, url, **kw):
            if '/memory/greeting' in url and method == 'GET':
                return meta_r
            return list_r

        args = _args(mem_action='list', agent_id='bot', meta=True)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', side_effect=_side):
            gateway_ctl.cmd_memory(args)
        out = capsys.readouterr().out
        assert 'no expiry' in out

    def test_future_expiry_shown_as_expires_in(self, capsys):
        import time as _t
        future_ts = _t.time() + 7200   # 2 hours from now
        list_r = {'memory': {'tok': 'abc'}}
        meta_r = {'key': 'tok', 'value': 'abc', 'expires_at': future_ts}

        def _side(method, url, **kw):
            if '/memory/tok' in url and method == 'GET':
                return meta_r
            return list_r

        args = _args(mem_action='list', agent_id='bot', meta=True)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', side_effect=_side):
            gateway_ctl.cmd_memory(args)
        out = capsys.readouterr().out
        assert 'expires in' in out

    def test_expired_key_labeled_expired(self, capsys):
        import time as _t
        past_ts = _t.time() - 10   # already elapsed
        list_r = {'memory': {'old': 'val'}}
        meta_r = {'key': 'old', 'value': 'val', 'expires_at': past_ts}

        def _side(method, url, **kw):
            if '/memory/old' in url and method == 'GET':
                return meta_r
            return list_r

        args = _args(mem_action='list', agent_id='bot', meta=True)
        with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
             patch.object(gateway_ctl, '_request', side_effect=_side):
            gateway_ctl.cmd_memory(args)
        out = capsys.readouterr().out
        assert 'EXPIRED' in out

    def test_parser_meta_flag_exists(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args([
            '--token', 't', 'memory', 'list', 'my-agent', '--meta',
        ])
        assert args.meta is True

    def test_parser_meta_flag_default_false(self):
        parser = gateway_ctl._build_parser()
        args = parser.parse_args(['--token', 't', 'memory', 'list', 'my-agent'])
        assert args.meta is False
