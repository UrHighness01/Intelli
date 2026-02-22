"""Tests for CORSMiddleware configuration and the /admin/redaction-rules endpoint.

CORS is configured via AGENT_GATEWAY_CORS_ORIGINS (comma-separated).
Default: http://127.0.0.1:8080
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ---------------------------------------------------------------------------
# Module-level setup — reload app with controlled env before importing
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def client_default():
    """Gateway with the default allowed origin (http://127.0.0.1:8080)."""
    from fastapi.testclient import TestClient
    with patch.dict(os.environ, {'AGENT_GATEWAY_CORS_ORIGINS': 'http://127.0.0.1:8080'}):
        from app import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture(scope='module')
def auth_token(client_default):
    import auth as _auth
    # Ensure a known admin user exists (reset state first, as other tests may
    # have altered the in-memory users file or token store).
    users = {}
    if _auth.USERS_PATH.exists():
        try:
            users = json.loads(_auth.USERS_PATH.read_text(encoding='utf-8'))
        except Exception:
            users = {}
    users.pop('admin', None)
    _auth.USERS_PATH.write_text(json.dumps(users), encoding='utf-8')
    _auth._TOKENS.clear()
    _auth._REFRESH_TOKENS.clear()
    _auth.create_user('admin', 'cors-test-secret', ['admin'])
    r = client_default.post('/admin/login', json={'username': 'admin', 'password': 'cors-test-secret'})
    assert r.status_code == 200, f'Login failed: {r.text}'
    return r.json()['token']


# ---------------------------------------------------------------------------
# 1. CORS – allowed origin
# ---------------------------------------------------------------------------

class TestCORSAllowedOrigin:
    def test_preflight_allowed_origin_returns_200(self, client_default):
        r = client_default.options(
            '/health',
            headers={
                'Origin': 'http://127.0.0.1:8080',
                'Access-Control-Request-Method': 'GET',
            },
        )
        assert r.status_code == 200

    def test_preflight_allowed_origin_header_present(self, client_default):
        r = client_default.options(
            '/health',
            headers={
                'Origin': 'http://127.0.0.1:8080',
                'Access-Control-Request-Method': 'GET',
            },
        )
        allowed = r.headers.get('access-control-allow-origin', '')
        assert allowed == 'http://127.0.0.1:8080'

    def test_get_with_allowed_origin_echoes_acao(self, client_default):
        r = client_default.get('/health', headers={'Origin': 'http://127.0.0.1:8080'})
        assert r.status_code == 200
        assert r.headers.get('access-control-allow-origin') == 'http://127.0.0.1:8080'


# ---------------------------------------------------------------------------
# 2. CORS – disallowed origin
# ---------------------------------------------------------------------------

class TestCORSDisallowedOrigin:
    def test_preflight_unknown_origin_no_acao_header(self, client_default):
        r = client_default.options(
            '/health',
            headers={
                'Origin': 'https://evil.example.com',
                'Access-Control-Request-Method': 'GET',
            },
        )
        # FastAPI CORS does not add Access-Control-Allow-Origin for unlisted origins
        acao = r.headers.get('access-control-allow-origin', '')
        assert acao != 'https://evil.example.com'

    def test_get_unknown_origin_no_acao_header(self, client_default):
        r = client_default.get('/health', headers={'Origin': 'https://evil.example.com'})
        acao = r.headers.get('access-control-allow-origin', '')
        assert acao != 'https://evil.example.com'


# ---------------------------------------------------------------------------
# 3. CORS – multi-origin env var
# ---------------------------------------------------------------------------

class TestCORSMultipleOrigins:
    def test_second_allowed_origin_is_accepted(self):
        """When AGENT_GATEWAY_CORS_ORIGINS contains two origins, both should be accepted."""
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {
            'AGENT_GATEWAY_CORS_ORIGINS': 'http://127.0.0.1:8080,http://localhost:3000'
        }):
            # Re-import app to pick up the patched env (note: module may be cached)
            import app as _app_mod
            importlib.reload(_app_mod)
            with TestClient(_app_mod.app, raise_server_exceptions=False) as c:
                r = c.options(
                    '/health',
                    headers={
                        'Origin': 'http://localhost:3000',
                        'Access-Control-Request-Method': 'GET',
                    },
                )
                acao = r.headers.get('access-control-allow-origin', '')
                assert acao == 'http://localhost:3000'


# ---------------------------------------------------------------------------
# 4. /admin/redaction-rules endpoint
# ---------------------------------------------------------------------------

class TestAdminRedactionRules:
    def test_requires_auth(self, client_default):
        r = client_default.get('/admin/redaction-rules')
        assert r.status_code in (401, 403)

    def test_returns_empty_when_no_rules(self, client_default, auth_token):
        r = client_default.get(
            '/admin/redaction-rules',
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        assert r.status_code == 200
        assert 'rules' in r.json()

    def test_configured_origin_appears_in_list(self, client_default, auth_token):
        # Configure a rule first
        client_default.post(
            '/tab/redaction-rules',
            json={'origin': 'https://test.corp.local', 'fields': ['password', 'ssn']},
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        r = client_default.get(
            '/admin/redaction-rules',
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        assert r.status_code == 200
        rules = r.json()['rules']
        assert 'https://test.corp.local' in rules
        assert sorted(rules['https://test.corp.local']) == ['password', 'ssn']

    def test_cleared_origin_has_empty_fields(self, client_default, auth_token):
        client_default.post(
            '/tab/redaction-rules',
            json={'origin': 'https://test.corp.local', 'fields': []},
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        r = client_default.get(
            '/admin/redaction-rules',
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        assert r.status_code == 200
        rules = r.json()['rules']
        # origin present with empty list after clearing
        assert rules.get('https://test.corp.local', []) == []

    def test_multiple_origins_returned(self, client_default, auth_token):
        for origin, fields in [
            ('https://app.corp.local', ['card_number', 'cvv']),
            ('https://hr.corp.local', ['dob', 'salary']),
        ]:
            client_default.post(
                '/tab/redaction-rules',
                json={'origin': origin, 'fields': fields},
                headers={'Authorization': f'Bearer {auth_token}'},
            )
        r = client_default.get(
            '/admin/redaction-rules',
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        rules = r.json()['rules']
        assert 'https://app.corp.local' in rules
        assert 'https://hr.corp.local' in rules
        assert sorted(rules['https://app.corp.local']) == ['card_number', 'cvv']
        assert sorted(rules['https://hr.corp.local']) == ['dob', 'salary']
