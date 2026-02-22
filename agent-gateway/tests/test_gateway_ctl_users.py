"""Tests for gateway_ctl.py  users subcommand.

All tests mock ``gateway_ctl._request`` and ``gateway_ctl._get_token`` so no
running gateway is needed.

Covered:  cmd_users (list / create / delete / password)  /  parser registration
"""
from __future__ import annotations

import argparse
import sys
import os
from unittest.mock import patch, call as mock_call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import gateway_ctl  # noqa: E402
from gateway_ctl import cmd_users, _build_parser  # noqa: E402


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
        'user_action': 'list',
        'username': 'alice',
        'password': 's3cret',
        'new_password': 'n3wpass',
        'role': 'user',
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _run(result, **kwargs):
    """Run cmd_users with mocked _request and collect printed lines."""
    args = _args(**kwargs)
    lines = []
    with patch.object(gateway_ctl, '_get_token', return_value='tok'):
        with patch.object(gateway_ctl, '_request', return_value=result):
            with patch('builtins.print',
                       side_effect=lambda *a, **kw: lines.append(str(a[0]) if a else '')):
                cmd_users(args)
    return lines


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_USERS = [
    {'username': 'admin',  'roles': ['admin'], 'has_tool_restrictions': False},
    {'username': 'alice',  'roles': ['user'],  'has_tool_restrictions': True},
    {'username': 'bob',    'roles': ['user'],  'has_tool_restrictions': False},
]


# ===========================================================================
# TestUsersList
# ===========================================================================

class TestUsersList:
    def test_calls_get_endpoint(self):
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={'users': []}) as mock_req:
                with patch('builtins.print'):
                    cmd_users(_args(user_action='list'))
        mock_req.assert_called_once_with(
            'GET', 'http://localhost:8080/admin/users', token='tok'
        )

    def test_empty_message(self):
        lines = _run({'users': []}, user_action='list')
        assert any('No users' in line for line in lines)

    def test_username_printed(self):
        lines = _run({'users': SAMPLE_USERS}, user_action='list')
        assert any('alice' in line for line in lines)

    def test_roles_printed(self):
        lines = _run({'users': SAMPLE_USERS}, user_action='list')
        assert any('admin' in line for line in lines)

    def test_restrictions_yes_printed(self):
        lines = _run({'users': SAMPLE_USERS}, user_action='list')
        assert any('yes' in line for line in lines)

    def test_restrictions_no_printed(self):
        lines = _run({'users': SAMPLE_USERS}, user_action='list')
        assert any('no' in line for line in lines)

    def test_user_count_footer(self):
        lines = _run({'users': SAMPLE_USERS}, user_action='list')
        assert any('3' in line and 'user' in line for line in lines)

    def test_header_columns(self):
        lines = _run({'users': SAMPLE_USERS}, user_action='list')
        header = ' '.join(lines)
        assert 'Username' in header
        assert 'Roles' in header


# ===========================================================================
# TestUsersCreate
# ===========================================================================

class TestUsersCreate:
    def _capture_body(self, **kwargs):
        """Run create and return the body dict passed to _request."""
        args = _args(user_action='create', **kwargs)
        captured = {}

        def fake_request(method, url, token=None, body=None, **kw):
            captured['method'] = method
            captured['url'] = url
            captured['body'] = body
            return {'username': args.username, 'roles': [args.role]}

        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', side_effect=fake_request):
                with patch('builtins.print'):
                    cmd_users(args)
        return captured

    def test_calls_post_endpoint(self):
        captured = self._capture_body()
        assert captured['method'] == 'POST'
        assert captured['url'].endswith('/admin/users')

    def test_username_in_body(self):
        captured = self._capture_body(username='charlie')
        assert captured['body']['username'] == 'charlie'

    def test_password_in_body(self):
        captured = self._capture_body(password='hunter2')
        assert captured['body']['password'] == 'hunter2'

    def test_default_role_user(self):
        captured = self._capture_body(role='user')
        assert captured['body']['roles'] == ['user']

    def test_admin_role_sent(self):
        captured = self._capture_body(role='admin')
        assert captured['body']['roles'] == ['admin']

    def test_roles_is_list(self):
        captured = self._capture_body()
        assert isinstance(captured['body']['roles'], list)


# ===========================================================================
# TestUsersDelete
# ===========================================================================

class TestUsersDelete:
    def test_calls_delete_endpoint(self):
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={'deleted': 'alice'}) as mock_req:
                with patch('builtins.print'):
                    cmd_users(_args(user_action='delete', username='alice'))
        mock_req.assert_called_once_with(
            'DELETE', 'http://localhost:8080/admin/users/alice', token='tok'
        )

    def test_username_in_url(self):
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', return_value={}) as mock_req:
                with patch('builtins.print'):
                    cmd_users(_args(user_action='delete', username='bob'))
        assert 'bob' in mock_req.call_args[0][1]


# ===========================================================================
# TestUsersPassword
# ===========================================================================

class TestUsersPassword:
    def _capture(self, **kwargs):
        args = _args(user_action='password', **kwargs)
        captured = {}

        def fake_request(method, url, token=None, body=None, **kw):
            captured['method'] = method
            captured['url'] = url
            captured['body'] = body
            return {'username': args.username, 'password_changed': True}

        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', side_effect=fake_request):
                with patch('builtins.print'):
                    cmd_users(args)
        return captured

    def test_calls_password_endpoint(self):
        captured = self._capture(username='alice')
        assert captured['method'] == 'POST'
        assert captured['url'].endswith('/admin/users/alice/password')

    def test_username_in_url(self):
        captured = self._capture(username='carol')
        assert 'carol' in captured['url']

    def test_new_password_in_body(self):
        captured = self._capture(new_password='supersecret')
        assert captured['body']['new_password'] == 'supersecret'


# ===========================================================================
# TestUsersParser
# ===========================================================================

class TestUsersParser:
    def setup_method(self):
        self.parser = _build_parser()
        self.subs = _subcommands(self.parser)

    def test_registered(self):
        assert 'users' in self.subs

    def test_list_action(self):
        ns = self.parser.parse_args(['users', 'list'])
        assert ns.user_action == 'list'

    def test_create_action(self):
        ns = self.parser.parse_args(['users', 'create', 'alice', 's3cret'])
        assert ns.user_action == 'create'
        assert ns.username == 'alice'
        assert ns.password == 's3cret'

    def test_create_default_role(self):
        ns = self.parser.parse_args(['users', 'create', 'alice', 's3cret'])
        assert ns.role == 'user'

    def test_create_role_flag(self):
        ns = self.parser.parse_args(['users', 'create', 'alice', 's3cret', '--role', 'admin'])
        assert ns.role == 'admin'

    def test_delete_action(self):
        ns = self.parser.parse_args(['users', 'delete', 'alice'])
        assert ns.user_action == 'delete'
        assert ns.username == 'alice'

    def test_password_action(self):
        ns = self.parser.parse_args(['users', 'password', 'alice', 'newpass'])
        assert ns.user_action == 'password'
        assert ns.username == 'alice'
        assert ns.new_password == 'newpass'

    def test_func_wired(self):
        ns = self.parser.parse_args(['users', 'list'])
        assert ns.func is cmd_users

    def test_permissions_get_action(self):
        ns = self.parser.parse_args(['users', 'permissions', 'get', 'alice'])
        assert ns.user_action == 'permissions'
        assert ns.user_perm_action == 'get'
        assert ns.username == 'alice'

    def test_permissions_set_action(self):
        ns = self.parser.parse_args(['users', 'permissions', 'set', 'alice', 'tool_a,tool_b'])
        assert ns.user_action == 'permissions'
        assert ns.user_perm_action == 'set'
        assert ns.username == 'alice'
        assert ns.tools == 'tool_a,tool_b'

    def test_permissions_clear_action(self):
        ns = self.parser.parse_args(['users', 'permissions', 'clear', 'bob'])
        assert ns.user_action == 'permissions'
        assert ns.user_perm_action == 'clear'
        assert ns.username == 'bob'


# ===========================================================================
# TestUsersPermissions
# ===========================================================================

class TestUsersPermissions:
    """Tests for `users permissions get / set / clear`."""

    def _capture_perm(self, api_resp, perm_action, tools='tool_a,tool_b', username='alice'):
        captured = {}

        def fake_req(method, url, token=None, body=None, **kw):
            captured['method'] = method
            captured['url'] = url
            captured['body'] = body
            return api_resp

        args = _args(user_action='permissions', user_perm_action=perm_action,
                     username=username, tools=tools)
        lines = []
        with patch.object(gateway_ctl, '_get_token', return_value='tok'):
            with patch.object(gateway_ctl, '_request', side_effect=fake_req):
                with patch('builtins.print',
                           side_effect=lambda *a, **kw: lines.append(str(a[0]) if a else '')):
                    cmd_users(args)
        return captured, lines

    # -- get ------------------------------------------------------------------

    def test_get_calls_get_endpoint(self):
        captured, _ = self._capture_perm({'allowed_tools': ['tool_a']}, 'get')
        assert captured['method'] == 'GET'
        assert captured['url'].endswith('/admin/users/alice/permissions')

    def test_get_unrestricted_message(self):
        _, lines = self._capture_perm({'allowed_tools': None}, 'get')
        assert any('unrestricted' in l for l in lines)

    def test_get_empty_restriction_message(self):
        _, lines = self._capture_perm({'allowed_tools': []}, 'get')
        assert any('restricted' in l for l in lines)

    def test_get_lists_tools(self):
        _, lines = self._capture_perm({'allowed_tools': ['alpha', 'beta']}, 'get')
        all_text = '\n'.join(lines)
        assert 'alpha' in all_text
        assert 'beta' in all_text

    def test_get_username_in_url(self):
        captured, _ = self._capture_perm({'allowed_tools': None}, 'get', username='carol')
        assert 'carol' in captured['url']

    # -- set ------------------------------------------------------------------

    def test_set_calls_put_endpoint(self):
        captured, _ = self._capture_perm({}, 'set', tools='tool_x,tool_y')
        assert captured['method'] == 'PUT'
        assert captured['url'].endswith('/admin/users/alice/permissions')

    def test_set_tools_list_in_body(self):
        captured, _ = self._capture_perm({}, 'set', tools='alpha, beta ,gamma')
        assert captured['body']['allowed_tools'] == ['alpha', 'beta', 'gamma']

    def test_set_strips_whitespace(self):
        captured, _ = self._capture_perm({}, 'set', tools='  x  ,  y  ')
        assert 'x' in captured['body']['allowed_tools']
        assert 'y' in captured['body']['allowed_tools']

    def test_set_skips_empty_tokens(self):
        captured, _ = self._capture_perm({}, 'set', tools='a,,b,')
        assert '' not in captured['body']['allowed_tools']

    # -- clear ----------------------------------------------------------------

    def test_clear_calls_put_endpoint(self):
        captured, _ = self._capture_perm({}, 'clear')
        assert captured['method'] == 'PUT'
        assert 'permissions' in captured['url']

    def test_clear_sends_null_allowed_tools(self):
        captured, _ = self._capture_perm({}, 'clear')
        assert captured['body']['allowed_tools'] is None
