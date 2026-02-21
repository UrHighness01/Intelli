from fastapi.testclient import TestClient
from app import app


client = TestClient(app)


def test_summarize_page_missing_url():
    payload = {"tool": "browser.summarize_page", "args": {"max_length": 100}}
    r = client.post('/tools/call', json=payload)
    assert r.status_code == 400
    detail = r.json().get('detail', {})
    # Expect structured feedback with an error token
    assert detail.get('status') == 'validation_error' or detail.get('detail')
    feedback = detail.get('feedback') if isinstance(detail, dict) else None
    assert feedback and 'token' in feedback


def test_file_write_requires_path_and_content_and_pending():
    payload = {"tool": "file.write", "args": {"path": "/tmp/x.txt", "content": "hello"}}
    r = client.post('/tools/call', json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body.get('status') == 'pending_approval'
