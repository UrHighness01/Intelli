from fastapi.testclient import TestClient
from app import app
import os


client = TestClient(app)
ADMIN_KEY = os.environ.get('AGENT_GATEWAY_ADMIN_KEY', 'dev-key')


def test_tab_preview_and_redaction_rules():
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

    # preview without rules: token should appear
    r = client.post('/tab/preview', json={'html': html, 'url': url})
    assert r.status_code == 200
    snap = r.json()
    tokens = [i for i in snap.get('inputs', []) if i.get('name') == 'token']
    assert tokens and tokens[0]['value'] == 'secret-token-123'

    # login as admin to get token
    pw = os.environ.get('AGENT_GATEWAY_ADMIN_PASSWORD', 'dev-key')
    lr = client.post('/admin/login', json={'username': 'admin', 'password': pw})
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
