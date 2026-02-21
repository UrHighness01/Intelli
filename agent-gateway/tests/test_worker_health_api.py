from fastapi.testclient import TestClient
from agent_gateway.app import app


def test_worker_health_endpoint():
    client = TestClient(app)
    r = client.get('/health/worker')
    assert r.status_code == 200
    body = r.json()
    assert 'worker_healthy' in body
