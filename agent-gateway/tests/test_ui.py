from fastapi.testclient import TestClient
from app import app


client = TestClient(app)


def test_ui_index_served():
    r = client.get('/ui/')
    assert r.status_code == 200
    assert 'text/html' in r.headers.get('content-type', '')
