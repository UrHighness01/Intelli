"""Tests for agent-gateway/content_filter.py.

Covers:
  - Unit-level: check(), add_rule(), delete_rule(), get_rules(), reload()
  - HTTP level: /admin/content-filter/* admin API
  - Enforcement: blocked payloads in POST /tools/call and POST /chat/complete
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_filter(monkeypatch, tmp_path):
    """Point the content filter at a fresh temp directory and reload."""
    rules_file = tmp_path / 'cf_rules.json'
    monkeypatch.setenv('AGENT_GATEWAY_CONTENT_FILTER_PATH', str(rules_file))
    monkeypatch.delenv('AGENT_GATEWAY_CONTENT_FILTER_PATTERNS', raising=False)
    import content_filter as cf
    importlib.reload(cf)
    return cf


@pytest.fixture()
def cf(isolated_filter):
    return isolated_filter


# ---------------------------------------------------------------------------
# Unit: check()
# ---------------------------------------------------------------------------

class TestCheck:
    def test_no_rules_passes_everything(self, cf):
        # Should not raise
        cf.check({'a': 'anything', 'b': 'also fine'})

    def test_literal_rule_blocks_matching_string(self, cf):
        from fastapi import HTTPException
        cf.add_rule('forbidden_word', 'literal', 'test-literal')
        with pytest.raises(HTTPException) as exc:
            cf.check('This contains forbidden_word here')
        assert exc.value.status_code == 403
        detail: dict = exc.value.detail  # type: ignore[assignment]
        assert detail['error'] == 'content_policy_violation'
        assert detail['matched_rule'] == 'test-literal'

    def test_literal_rule_case_insensitive(self, cf):
        from fastapi import HTTPException
        cf.add_rule('DROP TABLE', 'literal', 'sql-injection')
        with pytest.raises(HTTPException):
            cf.check('drop table users;')

    def test_regex_rule_blocks_match(self, cf):
        from fastapi import HTTPException
        cf.add_rule(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b', 'regex', 'credit-card')
        with pytest.raises(HTTPException):
            cf.check('My card is 4111 1111 1111 1111.')

    def test_regex_rule_does_not_block_non_match(self, cf):
        cf.add_rule(r'^\d{5}$', 'regex', 'five-digits')
        cf.check('Hello, world!')  # no raise

    def test_recursive_dict_checked(self, cf):
        from fastapi import HTTPException
        cf.add_rule('secret', 'literal', 'secret-word')
        with pytest.raises(HTTPException):
            cf.check({'outer': {'inner': 'this is a secret value'}})

    def test_recursive_list_checked(self, cf):
        from fastapi import HTTPException
        cf.add_rule('bomb', 'literal', 'violence')
        with pytest.raises(HTTPException):
            cf.check(['safe', 'also safe', 'bomb reference here'])

    def test_non_matching_payload_passes(self, cf):
        cf.add_rule('blocked', 'literal', 'blocked-word')
        cf.check({'key': 'totally safe content'})  # no raise

    def test_multiple_rules_first_match_raises(self, cf):
        from fastapi import HTTPException
        cf.add_rule('alpha', 'literal', 'rule-alpha')
        cf.add_rule('beta', 'literal', 'rule-beta')
        with pytest.raises(HTTPException) as exc:
            cf.check('alpha is here')
        detail: dict = exc.value.detail  # type: ignore[assignment]
        assert detail['matched_rule'] == 'rule-alpha'

    def test_env_var_pattern_applied(self, monkeypatch, tmp_path):
        """Patterns from the env var should be enforced after reload."""
        from fastapi import HTTPException
        rules_file = tmp_path / 'cf_env_test.json'
        monkeypatch.setenv('AGENT_GATEWAY_CONTENT_FILTER_PATH', str(rules_file))
        monkeypatch.setenv('AGENT_GATEWAY_CONTENT_FILTER_PATTERNS', 'env_banned_term')
        import content_filter as cf2
        importlib.reload(cf2)
        with pytest.raises(HTTPException):
            cf2.check('this contains env_banned_term inside')


# ---------------------------------------------------------------------------
# Unit: add_rule / delete_rule / get_rules
# ---------------------------------------------------------------------------

class TestRuleManagement:
    def test_add_and_list_rules(self, cf):
        cf.add_rule('foo', 'literal', 'foo-rule')
        rules = cf.get_rules()
        assert len(rules) == 1
        assert rules[0]['pattern'] == 'foo'
        assert rules[0]['mode'] == 'literal'
        assert rules[0]['label'] == 'foo-rule'

    def test_label_defaults_to_pattern_prefix(self, cf):
        cf.add_rule('short', 'literal')
        rules = cf.get_rules()
        assert rules[0]['label'] == 'short'

    def test_add_invalid_regex_raises(self, cf):
        with pytest.raises(ValueError):
            cf.add_rule('[unclosed', 'regex', 'bad-regex')

    def test_delete_rule_by_index(self, cf):
        cf.add_rule('a', 'literal', 'a')
        cf.add_rule('b', 'literal', 'b')
        deleted = cf.delete_rule(0)
        assert deleted is True
        rules = cf.get_rules()
        assert len(rules) == 1
        assert rules[0]['pattern'] == 'b'

    def test_delete_out_of_range_returns_false(self, cf):
        assert cf.delete_rule(99) is False

    def test_rule_persisted_across_reload(self, cf):
        cf.add_rule('persist_me', 'literal', 'persist')
        cf.reload()
        rules = cf.get_rules()
        assert any(r['pattern'] == 'persist_me' for r in rules)

    def test_reload_returns_rule_count(self, cf):
        cf.add_rule('x', 'literal', 'x')
        cf.add_rule('y', 'literal', 'y')
        count = cf.reload()
        assert count >= 2


# ---------------------------------------------------------------------------
# HTTP: /admin/content-filter/* endpoints
# ---------------------------------------------------------------------------

@pytest.fixture()
def http_client(tmp_path, monkeypatch):
    """TestClient with isolated auth + rules state."""
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'testpass-cf')
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)

    r = client.post('/admin/login', json={'username': 'admin', 'password': 'testpass-cf'})
    token = r.json()['token']
    return client, {'Authorization': f'Bearer {token}'}


class TestContentFilterHTTP:
    def test_list_rules_initially_empty(self, http_client):
        client, headers = http_client
        r = client.get('/admin/content-filter/rules', headers=headers)
        assert r.status_code == 200
        assert r.json()['rules'] == []

    def test_add_literal_rule(self, http_client):
        client, headers = http_client
        r = client.post(
            '/admin/content-filter/rules',
            json={'pattern': 'no-go', 'mode': 'literal', 'label': 'test-no-go'},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()['added'] is True

        r2 = client.get('/admin/content-filter/rules', headers=headers)
        rules = r2.json()['rules']
        assert any(rule['pattern'] == 'no-go' for rule in rules)

    def test_add_invalid_regex_returns_422(self, http_client):
        client, headers = http_client
        r = client.post(
            '/admin/content-filter/rules',
            json={'pattern': '[invalid', 'mode': 'regex', 'label': 'bad'},
            headers=headers,
        )
        assert r.status_code == 422

    def test_delete_rule_by_index(self, http_client):
        client, headers = http_client
        client.post('/admin/content-filter/rules', json={'pattern': 'x', 'mode': 'literal', 'label': 'x'}, headers=headers)
        r = client.delete('/admin/content-filter/rules/0', headers=headers)
        assert r.status_code == 200
        assert r.json()['deleted'] is True

    def test_delete_out_of_range_returns_404(self, http_client):
        client, headers = http_client
        r = client.delete('/admin/content-filter/rules/99', headers=headers)
        assert r.status_code == 404

    def test_reload_returns_active_count(self, http_client):
        client, headers = http_client
        client.post('/admin/content-filter/rules', json={'pattern': 'a', 'mode': 'literal', 'label': 'a'}, headers=headers)
        r = client.post('/admin/content-filter/reload', headers=headers)
        assert r.status_code == 200
        assert r.json()['reloaded'] is True
        assert r.json()['active_rules'] >= 1

    def test_requires_admin_auth(self, http_client):
        client, _ = http_client
        r = client.get('/admin/content-filter/rules')
        assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Enforcement: /tools/call and /chat/complete integration
# ---------------------------------------------------------------------------

class TestEnforcementIntegration:
    def test_tools_call_blocked_by_filter(self, http_client):
        """A tool call whose args contain a blocked string should return 403."""
        client, headers = http_client

        # Add blocking rule via admin API
        client.post(
            '/admin/content-filter/rules',
            json={'pattern': 'BLOCK_ME_NOW', 'mode': 'literal', 'label': 'integration-block'},
            headers=headers,
        )

        # Reload so the app's content_filter module picks it up
        client.post('/admin/content-filter/reload', headers=headers)

        payload = {'tool': 'echo', 'args': {'text': 'please BLOCK_ME_NOW here'}}
        r = client.post('/tools/call', json=payload)
        assert r.status_code == 403
        detail = r.json()['detail']
        assert detail['error'] == 'content_policy_violation'
        assert detail['matched_rule'] == 'integration-block'

    def test_tools_call_passes_when_clean(self, http_client):
        """A tool call with clean args should not be blocked (even with a rule present)."""
        client, headers = http_client

        client.post(
            '/admin/content-filter/rules',
            json={'pattern': 'BLOCK_ME_ONLY', 'mode': 'literal', 'label': 'clean-test'},
            headers=headers,
        )
        client.post('/admin/content-filter/reload', headers=headers)

        payload = {'tool': 'echo', 'args': {'text': 'totally clean content'}}
        r = client.post('/tools/call', json=payload)
        # Accepts 200/400 (stubbed call / validation) — but NOT 403
        assert r.status_code != 403

    def test_chat_complete_blocked_by_filter(self, http_client):
        """Chat messages containing a blocked term should return 403."""
        client, headers = http_client

        client.post(
            '/admin/content-filter/rules',
            json={'pattern': 'CHAT_BLOCKED', 'mode': 'literal', 'label': 'chat-block'},
            headers=headers,
        )
        client.post('/admin/content-filter/reload', headers=headers)

        r = client.post(
            '/chat/complete',
            json={
                'provider': 'openai',
                'messages': [{'role': 'user', 'content': 'Say CHAT_BLOCKED please'}],
            },
            headers=headers,
        )
        assert r.status_code == 403
        detail = r.json()['detail']
        assert detail['error'] == 'content_policy_violation'
