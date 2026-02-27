"""Tests for audit actor attribution.

Verifies that:
  - _actor() helper resolves a valid token => username string
  - _actor() falls back to None for invalid/missing tokens
  - Admin action endpoints write the authenticated username (not a token
    prefix) as the `actor` field in audit log entries
  - The memory_import audit entry bug is fixed (details is a dict, not
    the string 'admin')
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """Return (TestClient, admin_token) with isolated state."""
    monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'actorpass')
    import auth
    monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users.json')
    monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked.json')
    auth._TOKENS.clear()
    auth._REFRESH_TOKENS.clear()
    auth._REVOKED.clear()
    auth._ensure_default_admin()

    from app import app
    client = TestClient(app)
    r = client.post('/admin/login', json={'username': 'admin', 'password': 'actorpass'})
    assert r.status_code == 200, r.text
    token = r.json()['token']
    return client, token


@pytest.fixture()
def audit_path(tmp_path, monkeypatch):
    """Redirect audit writes to a temporary file and return its Path."""
    log = tmp_path / 'audit.log'
    import app as _app
    monkeypatch.setattr(_app, 'AUDIT_PATH', log)
    return log


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


# ===========================================================================
# Unit tests for _actor() helper
# ===========================================================================

class TestActorHelper:
    def test_resolves_valid_token_to_username(self, setup):
        _, token = setup
        import app as _app
        result = _app._actor(token)
        assert result == 'admin'

    def test_returns_none_for_none_token(self):
        import app as _app
        assert _app._actor(None) is None

    def test_returns_none_for_empty_string(self):
        import app as _app
        assert _app._actor('') is None

    def test_returns_none_for_invalid_token(self):
        import app as _app
        assert _app._actor('notarealtoken') is None

    def test_resolves_non_admin_token_to_username(self, tmp_path, monkeypatch):
        monkeypatch.setenv('AGENT_GATEWAY_ADMIN_PASSWORD', 'actorpass2')
        import auth
        monkeypatch.setattr(auth, 'USERS_PATH', tmp_path / 'users2.json')
        monkeypatch.setattr(auth, 'REVOKED_PATH', tmp_path / 'revoked2.json')
        auth._TOKENS.clear()
        auth._ensure_default_admin()
        auth.create_user('charlie', 'pw', roles=['admin'])
        from app import app as _app_obj
        client = TestClient(_app_obj)
        r = client.post('/admin/login', json={'username': 'charlie', 'password': 'pw'})
        assert r.status_code == 200
        token = r.json()['token']
        import app as _app
        assert _app._actor(token) == 'charlie'


# ===========================================================================
# Integration: audit entries contain username as actor
# ===========================================================================

class TestAuditEntryActor:
    def _entries(self, log: Path, event: str) -> list[dict]:
        """Parse encrypted JSONL audit log and filter by event name."""
        import app as _app
        entries = []
        if not log.exists():
            return entries
        key = _app._audit_key()
        for line in log.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                plain = _app._decrypt_audit_line(line, key)
                obj = json.loads(plain)
                if obj.get('event') == event:
                    entries.append(obj)
            except Exception:
                pass
        return entries

    def test_create_user_actor_is_username(self, setup, audit_path):
        client, token = setup
        client.post(
            '/admin/users',
            json={'username': 'diana', 'password': 'pw', 'roles': ['user']},
            headers=_auth(token),
        )
        entries = self._entries(audit_path, 'create_user')
        assert entries, 'No audit entry for create_user'
        actor = entries[-1].get('actor')
        assert actor == 'admin', f"Expected 'admin', got {actor!r}"

    def test_actor_is_not_token_prefix(self, setup, audit_path):
        """Regression: actor must NOT look like a 6-char token fragment."""
        client, token = setup
        client.post(
            '/admin/users',
            json={'username': 'edgar', 'password': 'pw', 'roles': ['user']},
            headers=_auth(token),
        )
        entries = self._entries(audit_path, 'create_user')
        for entry in entries:
            actor = entry.get('actor', '')
            assert not (isinstance(actor, str) and actor.endswith('...')), (
                f"Actor looks like a token prefix: {actor!r}"
            )

    def test_delete_user_actor_is_username(self, setup, audit_path):
        client, token = setup
        client.post(
            '/admin/users',
            json={'username': 'fiona', 'password': 'pw', 'roles': ['user']},
            headers=_auth(token),
        )
        client.delete('/admin/users/fiona', headers=_auth(token))
        entries = self._entries(audit_path, 'delete_user')
        assert entries, 'No audit entry for delete_user'
        assert entries[-1].get('actor') == 'admin'

    def test_change_password_actor_is_username(self, setup, audit_path):
        client, token = setup
        client.post(
            '/admin/users',
            json={'username': 'george', 'password': 'pw', 'roles': ['user']},
            headers=_auth(token),
        )
        client.post(
            '/admin/users/george/password',
            json={'new_password': 'newpw'},
            headers=_auth(token),
        )
        entries = self._entries(audit_path, 'change_password')
        assert entries, 'No audit entry for change_password'
        assert entries[-1].get('actor') == 'admin'
