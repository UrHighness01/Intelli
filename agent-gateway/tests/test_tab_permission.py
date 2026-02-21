import json
from fastapi.testclient import TestClient
from app import app
import auth as _auth
import os


client = TestClient(app)
_TEST_PW = 'dev-key'


def _reset_admin(pw: str = _TEST_PW):
    """Ensure a fresh admin account with the given password exists (for test isolation)."""
    users_path = _auth.USERS_PATH
    users = {}
    if users_path.exists():
        try:
            with users_path.open('r') as f:
                users = json.load(f)
        except Exception:
            users = {}
    users.pop('admin', None)
    with users_path.open('w') as f:
        json.dump(users, f)
    _auth._TOKENS.clear()
    _auth._REFRESH_TOKENS.clear()
    _auth.create_user('admin', pw, roles=['admin'])


def test_tab_preview_and_redaction_rules():
    _reset_admin()
    html = """
    <html>
      <body>
        <form>
          <input type="text" name="token" value="secret-token-123" />
          <input type="text" name="user" value="alice" />
        </form>
      </body>
    </html>
    """
    url = "https://example.test"

    # preview without explicit rules: tab_bridge redacts SENSITIVE_KEYS fields (incl. 'token') by default
    r = client.post('/tab/preview', json={'html': html, 'url': url})
    assert r.status_code == 200
    snap = r.json()
    tokens = [i for i in snap.get('inputs', []) if i.get('name') == 'token']
    # 'token' matches SENSITIVE_KEYS pattern, so value is redacted even without explicit rules
    assert tokens and tokens[0]['value'] == '[REDACTED]'

    # login as admin to get token
    lr = client.post('/admin/login', json={'username': 'admin', 'password': _TEST_PW})
    assert lr.status_code == 200
    token = lr.json().get('token')
    assert token

    # set redaction rules (requires admin bearer token)
    r = client.post('/tab/redaction-rules', json={'origin': url, 'fields': ['token']}, headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    assert r.json()['fields'] == ['token']

    # preview again: token should be redacted
    r = client.post('/tab/preview', json={'html': html, 'url': url})
    snap = r.json()
    tokens = [i for i in snap.get('inputs', []) if i.get('name') == 'token']
    assert tokens and tokens[0]['value'] == '[REDACTED]'
