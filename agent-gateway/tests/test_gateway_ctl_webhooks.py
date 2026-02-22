"""Tests for gateway_ctl.py  webhooks subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered actions:  list, add, delete
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
        'wh_action': 'list',
        'url_arg': '',
        'events': '',
        'secret': '',
        'id': '',
    }
    defaults.update(kwargs)
    # 'url' is both the gateway base URL and the webhook URL arg — keep them separate
    ns = argparse.Namespace(**{k: v for k, v in defaults.items() if k != 'url_arg'})
    return ns


def _args_add(url_val='https://example.com/hook', events='', secret='', **kwargs):
    """Namespace for 'webhooks add' — url is the webhook URL positional arg."""
    base = {
        'url': 'http://localhost:8080',
        'token': 'tok',
        'wh_action': 'add',
        'events': events,
        'secret': secret,
    }
    base.update(kwargs)
    ns = argparse.Namespace(**base)
    # The parsed positional 'url' for 'webhooks add' is stored as ns.url; we need
    # the gateway base URL separately. Use a wrapper approach: set ns.url to the
    # webhook URL and patch _url to use a fixed base.
    # Actually in the real parser both are 'url', but the gateway URL is on the
    # parent namespace and webhooks add captures the positional as 'url' on its
    # own namespace — after parse_args both land in the same Namespace.
    # To keep tests simple we patch _url directly.
    ns.url = 'http://localhost:8080'          # gateway base (used by _url())
    ns._webhook_url = url_val                 # webhook target URL
    return ns


def _run_list(ret=None):
    args = argparse.Namespace(
        url='http://localhost:8080', token='tok', wh_action='list'
    )
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_webhooks(args)
    return m


def _run_add(webhook_url, events='', secret='', ret=None):
    args = argparse.Namespace(
        url='http://localhost:8080', token='tok', wh_action='add',
        events=events, secret=secret,
    )
    # Patch _url so we can intercept both the base url and the positional url arg.
    # In the real CLI 'webhooks add <url>' sets args.url = <webhook_url> AFTER
    # parse_args overwrites the --url default. We simulate that by patching _url
    # to return a deterministic string and verifying the POST body directly.
    real_url = f'http://localhost:8080/admin/webhooks'
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_url', return_value=real_url), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        # Attach the positional 'url' arg that the parser normally provides
        args.url = webhook_url
        gateway_ctl.cmd_webhooks(args)
    return m


def _run_delete(hook_id, ret=None):
    args = argparse.Namespace(
        url='http://localhost:8080', token='tok', wh_action='delete', id=hook_id
    )
    with patch.object(gateway_ctl, '_get_token', return_value='tok'), \
         patch.object(gateway_ctl, '_request', return_value=ret or {}) as m:
        gateway_ctl.cmd_webhooks(args)
    return m


# ===========================================================================
# list
# ===========================================================================

class TestWebhooksList:
    def test_calls_get_endpoint(self):
        m = _run_list(ret={'webhooks': []})
        m.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/webhooks', token='tok'
        )

    def test_prints_no_webhooks_message_when_empty(self, capsys):
        _run_list(ret={'webhooks': []})
        out = capsys.readouterr().out
        assert 'No webhooks' in out

    def test_does_not_crash_on_empty_response(self):
        _run_list(ret={})  # must not raise


# ===========================================================================
# add
# ===========================================================================

class TestWebhooksAdd:
    def test_calls_post_with_url(self):
        m = _run_add('https://example.com/hook')
        m.assert_called_once()
        call_args = m.call_args
        assert call_args[0][0] == 'POST'
        body = call_args[1].get('body') or call_args[0][2] if len(call_args[0]) > 2 else None
        body = call_args.kwargs.get('body', call_args[1].get('body') if call_args[1] else None)
        assert body is not None
        assert body['url'] == 'https://example.com/hook'

    def test_events_split_by_comma(self):
        m = _run_add('https://example.com/hook', events='approval.created,approval.approved')
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert body['events'] == ['approval.created', 'approval.approved']

    def test_no_events_omitted_from_body(self):
        m = _run_add('https://example.com/hook', events='')
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert 'events' not in body

    def test_secret_included_when_provided(self):
        m = _run_add('https://example.com/hook', secret='mysecret')
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert body['secret'] == 'mysecret'

    def test_secret_omitted_when_empty(self):
        m = _run_add('https://example.com/hook', secret='')
        body = m.call_args.kwargs.get('body', m.call_args[1].get('body'))
        assert 'secret' not in body


# ===========================================================================
# delete
# ===========================================================================

class TestWebhooksDelete:
    def test_calls_delete_endpoint_with_id(self):
        m = _run_delete('wh-123')
        m.assert_called_once_with(
            'DELETE', 'http://localhost:8080/admin/webhooks/wh-123', token='tok'
        )

    def test_uses_correct_id(self):
        m = _run_delete('abc-456')
        call_url = m.call_args[0][1]
        assert 'abc-456' in call_url


# ===========================================================================
# Parser
# ===========================================================================

class TestWebhooksParser:
    def setup_method(self):
        self.parser = gateway_ctl._build_parser()

    def test_webhooks_registered(self):
        assert 'webhooks' in _subcommands(self.parser)

    def test_list_parsed(self):
        ns = self.parser.parse_args(['webhooks', 'list'])
        assert ns.wh_action == 'list'

    def test_add_parsed(self):
        ns = self.parser.parse_args(['webhooks', 'add', 'https://example.com/hook'])
        assert ns.wh_action == 'add'

    def test_add_with_events(self):
        ns = self.parser.parse_args(
            ['webhooks', 'add', 'https://h.com/hook', '--events', 'approval.created']
        )
        assert ns.events == 'approval.created'

    def test_add_with_secret(self):
        ns = self.parser.parse_args(
            ['webhooks', 'add', 'https://h.com/hook', '--secret', 'tok123']
        )
        assert ns.secret == 'tok123'

    def test_delete_parsed(self):
        ns = self.parser.parse_args(['webhooks', 'delete', 'wh-uuid'])
        assert ns.wh_action == 'delete'
        assert ns.id == 'wh-uuid'

    def test_func_wired(self):
        ns = self.parser.parse_args(['webhooks', 'list'])
        assert ns.func is gateway_ctl.cmd_webhooks
