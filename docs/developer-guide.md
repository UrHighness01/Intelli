# Developer Guide — Intelli Agent Gateway

---

## Architecture Overview

```
Browser Renderer  ──IPC──► Agent Panel (sidebar)
                                 │
                          HTTP / local socket
                                 │
                    ┌─────────────▼──────────────┐
                    │      Agent Gateway          │
                    │  FastAPI  (app.py)          │
                    │  ├─ Supervisor (supervisor.py) │
                    │  ├─ Tab Context Bridge      │
                    │  ├─ Approval Queue          │
                    │  ├─ RBAC / Auth             │
                    │  ├─ Metrics registry        │
                    │  └─ Sandbox Pool ──► Worker │
                    └─────────────────────────────┘
```

Key modules:

| Module | Purpose |
|---|---|
| `app.py` | FastAPI gateway — all HTTP endpoints |
| `supervisor.py` | Schema validation, risk scoring, approval queue |
| `tab_bridge.py` | DOM snapshot serialization + redaction |
| `auth.py` | PBKDF2 user auth, in-memory access/refresh tokens |
| `sandbox/worker.py` | Single-shot subprocess worker |
| `sandbox/worker_persistent.py` | Long-lived worker for pool IPC |
| `sandbox/proxy.py` | Dispatch to subprocess or persistent worker |
| `sandbox/pool.py` | Thread-safe WorkerPool with restart/backoff |
| `sandbox/manager.py` | Health checks against bundled worker |
| `sandbox/docker_runner.py` | Docker-isolated worker runner |
| `providers/provider_adapter.py` | Provider key store (keyring / env / file) |
| `providers/vault_adapter.py` | HashiCorp Vault integration for secrets |
| `metrics.py` | In-process counter/gauge/histogram registry |

---

## Adding a New Tool Schema

1. Create a JSON Schema file in `agent-gateway/schemas/`:

```json
// schemas/my_tool.my_action.json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["url"],
  "properties": {
    "url": { "type": "string", "format": "uri" }
  },
  "additionalProperties": false
}
```

2. The `Supervisor` auto-discovers schemas at startup via `_load_tool_schema()`.
   If a `tool.action` call arrives, it validates `args` against the matching schema.

3. Add a test in `agent-gateway/tests/test_tool_schema_validation.py`.

---

## Adding a New Endpoint

1. Add a route in `app.py`:

```python
@app.get('/my/endpoint')
def my_endpoint(request: Request):
    # admin-gated example
    _require_admin_token(request)
    return {"data": "..."}
```

2. Write a test using `TestClient`:

```python
# agent-gateway/tests/test_my_feature.py
from fastapi.testclient import TestClient
from app import app

def test_my_endpoint():
    client = TestClient(app)
    # login first
    r = client.post('/admin/login', json={'username': 'admin', 'password': 'test'})
    token = r.json()['token']
    r2 = client.get('/my/endpoint', headers={'Authorization': f'Bearer {token}'})
    assert r2.status_code == 200
```

---

## Sandbox Extension

To allow a new action in the worker, add it to both `sandbox/worker.py` and
`sandbox/worker_persistent.py`:

```python
def _handle_fetch_title(params):
    # Example: parse a URL title (no network in production sandbox!)
    return {"status": "ok", "title": params.get("url", "").split("/")[-1]}

ALLOWED = {
    "noop": _handle_noop,
    "echo": _handle_echo,
    "fetch_title": _handle_fetch_title,  # new
}
```

Also add it to `sandbox/proxy.py`'s `_ALLOWED` dict and write a test.

---

## Provider Adapter Extension

Implement `BaseProviderAdapter` and optionally use `VaultKeyStore`:

```python
from providers.provider_adapter import BaseProviderAdapter
from providers.vault_adapter import get_store

class AnthropicAdapter(BaseProviderAdapter):
    def __init__(self):
        super().__init__('anthropic')

    def call(self, payload):
        key = get_store().get_key(self.provider_name)
        if not key:
            raise RuntimeError('Anthropic API key missing')
        return {'provider': 'anthropic', 'payload': payload, 'auth': f'x-api-key: {key}'}
```

---

## Running Tests

```bash
# Full suite from repo root
python -m pytest -q

# Specific test file
python -m pytest agent-gateway/tests/test_supervisor.py -v

# With stdout capture disabled (useful for debugging)
python -m pytest -s agent-gateway/tests/test_sandbox_manager.py
```

The top-level `conftest.py` automatically adds `agent-gateway/` to `sys.path`
and sets `SANDBOX_WORKER_PATH` so all tests work without extra env setup.

---

## Metrics

Metrics are recorded via `metrics.py`:

```python
import metrics as m

m.inc('my_counter', labels={'tool': 'browser.summarize'})
m.gauge('active_sessions', 3)
m.observe('call_latency_seconds', 0.042)
```

Expose them via `GET /metrics` (Prometheus text format).

---

## CI Integration

Workflow `.github/workflows/ci.yml`:
- Sets `PYTHONPATH=$GITHUB_WORKSPACE`.
- Sets `SANDBOX_WORKER_PATH` to bundled worker.
- Installs `agent-gateway/requirements.txt` + `pip-licenses` + `pip-audit`.
- Starts gateway, waits for `/health`, runs `pytest -q agent-gateway`.
- Generates `sbom.json` and `audit.json` artifacts.
