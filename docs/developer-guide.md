# Developer Guide — Intelli

---

## Repository Structure

```
Intelli/
├── agent-gateway/           ← FastAPI backend + all business logic
│   ├── app.py               ← Main FastAPI application (all routes)
│   ├── supervisor.py        ← Schema validation, risk scoring, approval queue
│   ├── auth.py              ← PBKDF2 auth, access/refresh tokens, RBAC, user management
│   ├── audit.py / audit.log ← Append-only JSONL audit trail
│   ├── rate_limit.py        ← Sliding-window per-IP and per-user rate limiter
│   ├── scheduler.py         ← Recurring tool-call scheduler (background daemon)
│   ├── agent_memory.py      ← Per-agent key-value store with TTL
│   ├── content_filter.py    ← Literal + regex deny-list enforcement
│   ├── webhooks.py          ← Approval webhook registry + HMAC signed delivery
│   ├── metrics.py           ← In-process counter/gauge/histogram registry
│   ├── consent_log.py       ← Append-only context-sharing consent log (JSONL)
│   ├── tab_bridge.py        ← DOM snapshot serializer + redaction engine
│   ├── gateway_ctl.py       ← Operator CLI (wraps all admin REST APIs)
│   ├── openapi.yaml         ← Full OpenAPI 3.0.3 spec (20 named tags)
│   ├── providers/
│   │   ├── provider_adapter.py   ← ProviderKeyStore + BaseProviderAdapter
│   │   ├── adapters.py           ← OpenAI / Anthropic / OpenRouter / Ollama adapters
│   │   ├── key_rotation.py       ← Key TTL metadata, rotation, expiry listing
│   │   └── vault_adapter.py      ← HashiCorp Vault KV v2 integration
│   ├── sandbox/
│   │   ├── worker.py             ← Single-shot subprocess worker
│   │   ├── worker_persistent.py  ← Long-lived pool worker (IPC)
│   │   ├── proxy.py              ← Dispatch to subprocess or persistent worker
│   │   ├── pool.py               ← Thread-safe WorkerPool with restart/backoff
│   │   ├── manager.py            ← Health checks against bundled worker
│   │   └── docker_runner.py      ← Docker-isolated worker runner
│   ├── schemas/
│   │   ├── *.json                ← Per-tool JSON Schema validation files
│   │   └── capabilities/         ← Per-tool capability manifest JSON files (13)
│   ├── tools/
│   │   └── capability.py         ← CapabilityVerifier + ToolManifest
│   ├── ui/                       ← 15 dark-mode admin HTML pages
│   └── tests/                    ← ~1087 pytest tests (57 test files)
│
├── browser-shell/           ← Electron desktop browser
│   ├── main.js              ← Electron main process: gateway lifecycle + tab management
│   ├── preload.js           ← contextBridge API (window.electronAPI)
│   ├── package.json         ← Electron + electron-builder config
│   ├── src/
│   │   ├── browser.html     ← Tab bar + address bar chrome UI
│   │   ├── browser.css      ← Dark-theme chrome styles
│   │   ├── browser.js       ← Renderer logic + keyboard shortcuts
│   │   └── splash.html      ← Startup splash while gateway boots
│   └── assets/icon.ico      ← App icon
│
├── docs/                    ← This documentation
├── ROADMAP.md               ← Phased roadmap + detailed implementation log
├── ARCHITECTURE.md          ← System architecture (Mermaid)
├── SECURITY.md              ← Security policy + hardening checklist
└── THREAT_MODEL.md          ← Attack surface and privacy controls
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Electron Browser (browser-shell/)                          │
│  ┌──────────────────────┐  ┌───────────────────────────┐   │
│  │ Chrome UI            │  │  BrowserView × N (tabs)   │   │
│  │ browser.html + .js   │  │  ← actual web pages       │   │
│  └──────────┬───────────┘  └───────────────────────────┘   │
│             │ contextBridge (preload.js)                    │
│             ▼                                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Electron main process (main.js)                      │  │
│  │  gateway spawn / health poll / tab / IPC handlers     │  │
│  └────────────────────┬──────────────────────────────────┘  │
└───────────────────────┼─────────────────────────────────────┘
                        │ HTTP  127.0.0.1:8080
                        ▼
┌───────────────────────────────────────────────────────────────┐
│  Agent Gateway (agent-gateway/app.py — FastAPI)               │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ Auth / RBAC │  │  Supervisor  │  │  Approval Queue    │   │
│  │ auth.py     │  │  supervisor  │  │  + SSE stream      │   │
│  └─────────────┘  └──────┬───────┘  └────────────────────┘   │
│                          │                                    │
│  ┌─────────────┐  ┌──────▼───────┐  ┌────────────────────┐   │
│  │ Rate Limiter│  │  Capability  │  │  Content Filter    │   │
│  │ rate_limit  │  │  Verifier    │  │  content_filter.py │   │
│  └─────────────┘  └──────┬───────┘  └────────────────────┘   │
│                          │                                    │
│  ┌─────────────┐  ┌──────▼───────┐  ┌────────────────────┐   │
│  │ Scheduler   │  │  Sandbox     │  │  Provider Adapters │   │
│  │ scheduler   │  │  Pool/Worker │  │  OpenAI/Anthropic  │   │
│  └─────────────┘  └──────────────┘  └────────────────────┘   │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ Agent Memory│  │  Webhooks    │  │  Audit Log         │   │
│  │ agent_memory│  │  webhooks.py │  │  audit.log (JSONL) │   │
│  └─────────────┘  └──────────────┘  └────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

---

## Module Reference

| Module | Purpose |
|---|---|
| `app.py` | FastAPI app — all ~40 HTTP routes |
| `supervisor.py` | Schema validation, risk scoring, manifest-driven approval routing |
| `auth.py` | PBKDF2 user auth, RBAC, sign-in, access/refresh/revoke token lifecycle, user management |
| `rate_limit.py` | Sliding-window per-IP + per-user rate limiting; runtime config API |
| `scheduler.py` | Interval-based recurring tool-call tasks; background daemon; run history |
| `agent_memory.py` | Thread-safe JSON per-agent KV store; TTL prune; export/import |
| `content_filter.py` | Literal + regex deny-list; recursive string check; runtime admin API |
| `webhooks.py` | Webhook registry; HMAC signing; exponential-back-off delivery; delivery log |
| `metrics.py` | In-process counter/gauge/histogram registry; Prometheus text output |
| `consent_log.py` | Append-only JSONL consent timeline; GDPR export/erase |
| `tab_bridge.py` | DOM snapshot serializer + per-origin field redaction |
| `gateway_ctl.py` | Operator CLI — all admin commands (21 subcommands, 55+ sub-actions) |
| `providers/provider_adapter.py` | ProviderKeyStore (keyring / env / file fallback) |
| `providers/adapters.py` | Concrete adapters: OpenAI, Anthropic, OpenRouter, Ollama |
| `providers/key_rotation.py` | Key TTL metadata, `rotate_key()`, `list_expiring()` |
| `providers/vault_adapter.py` | HashiCorp Vault KV v2 integration |
| `tools/capability.py` | CapabilityVerifier + ToolManifest (13 capability manifests) |
| `sandbox/worker.py` | Single-shot subprocess worker (action whitelist) |
| `sandbox/worker_persistent.py` | Long-lived pool worker with IPC |
| `sandbox/proxy.py` | Dispatch: subprocess vs persistent worker |
| `sandbox/pool.py` | Thread-safe WorkerPool with health checks + restart/backoff |
| `sandbox/manager.py` | Health checks against bundled worker (`/health/worker`) |
| `sandbox/docker_runner.py` | Docker-isolated worker (`--cap-drop ALL`, `no-new-privileges`) |

---

## Admin UI Pages

| Page | URL | Description |
|---|---|---|
| `index.html` | `/ui/` | Searchable nav hub |
| `status.html` | `/ui/status.html` | Live gateway dashboard |
| `audit.html` | `/ui/audit.html` | Audit log — sort, filter, group-by, CSV |
| `approvals.html` | `/ui/approvals.html` | Approval queue — SSE live updates |
| `users.html` | `/ui/users.html` | User management + last-seen chip |
| `providers.html` | `/ui/providers.html` | LLM key storage, rotation, chat proxy test |
| `schedule.html` | `/ui/schedule.html` | Scheduler — tasks, history, sparklines |
| `metrics.html` | `/ui/metrics.html` | Per-tool call counts, p50 latency |
| `memory.html` | `/ui/memory.html` | Agent memory browser + export/import |
| `content-filter.html` | `/ui/content-filter.html` | Deny-rule management |
| `rate-limits.html` | `/ui/rate-limits.html` | Rate limit config + live snapshot |
| `webhooks.html` | `/ui/webhooks.html` | Webhook registration + delivery history |
| `capabilities.html` | `/ui/capabilities.html` | Tool capability manifest browser |
| `consent.html` | `/ui/consent.html` | Context-sharing consent timeline |
| `tab_permission.html` | `/ui/tab_permission.html` | Tab snapshot permission request |

---

## CLI Subcommands (`gateway_ctl.py`)

| Subcommand | Actions |
|---|---|
| `login` | Authenticate and save token |
| `kill-switch` | `on` / `off` / `status` |
| `audit` | `tail` / `follow` / `export-csv` |
| `permissions` | `get` / `set` / `clear` |
| `alerts` | `status` / `set N` |
| `approvals` | `list` / `approve` / `reject` / `timeout get` / `timeout set` |
| `capabilities` | `list` / `show <tool>` |
| `content-filter` | `list` / `add` / `delete` / `reload` |
| `consent` | `export` / `erase` / `timeline` |
| `key` | `set` / `rotate` / `status` / `expiry` / `delete` |
| `memory` | `agents` / `list` / `get` / `set` / `delete` / `prune` / `clear` / `export` / `import` |
| `metrics` | `tools` / `top` |
| `provider-health` | `check` / `list` / `expiring` |
| `providers` | `list` / `expiring` |
| `rate-limits` | `status` / `set` / `reset-client` / `reset-user` |
| `schedule` | `list` / `get` / `create` / `delete` / `enable` / `disable` / `trigger` / `history` |
| `status` | Show gateway status summary |
| `users` | `list` / `create` / `delete` / `password` / `permissions` |
| `webhooks` | `list` / `add` / `delete` |

---

## Adding a New Tool Schema

1. Create a JSON Schema file in `agent-gateway/schemas/`:

```json
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
   File name must match `{tool}.{action}.json`.

3. Optionally add a capability manifest to `schemas/capabilities/`:

```json
{
  "tool": "browser.fetch",
  "description": "Fetch and return page content",
  "risk_level": "medium",
  "requires_approval": false,
  "required_capabilities": ["network.read"],
  "optional_capabilities": [],
  "allowed_arg_keys": ["url", "headers"]
}
```

4. Add a test in `agent-gateway/tests/test_tool_schema_validation.py`.

---

## Adding a New Endpoint

```python
# agent-gateway/app.py
@app.get('/admin/my-feature')
def my_feature(request: Request):
    token = _require_admin_token(request)
    _audit('my_feature_queried', actor=_actor(token), details={})
    return {"data": "..."}
```

Test pattern:

```python
# agent-gateway/tests/test_my_feature.py
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def _login():
    r = client.post('/admin/login', json={'username': 'admin', 'password': 'test'})
    return r.json()['token']

def test_my_feature():
    token = _login()
    r = client.get('/admin/my-feature', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
```

---

## Adding a CLI Subcommand

Every subcommand lives in `gateway_ctl.py` as a `cmd_<name>` function
and is registered in `_build_parser()`:

```python
def cmd_myfeature(args: argparse.Namespace) -> None:
    data = _request('GET', f'{args.gateway}/admin/my-feature', args.token)
    print(data)

# In _build_parser():
mf = sub.add_parser('myfeature', help='Query my feature')
mf.set_defaults(func=cmd_myfeature)
```

Add tests in `tests/test_gateway_ctl_myfeature.py` using `MagicMock` for `_request`.

---

## Metrics API

```python
import metrics as m

m.inc('tool_calls_total', labels={'tool': 'browser.summarize'})
m.observe('tool_call_duration_seconds', 0.042, labels={'tool': 'browser.summarize'})

# Query
rows = m.get_labels_for_counter('tool_calls_total')
# → [({'tool': 'browser.summarize'}, 1), ...]
```

Exposed at `GET /metrics` (Prometheus text format).

---

## Sandbox Extension

Add to both `sandbox/worker.py` and `sandbox/worker_persistent.py`:

```python
def _handle_fetch_title(params):
    return {"status": "ok", "title": params.get("url", "").split("/")[-1]}

ALLOWED = {
    "noop": _handle_noop,
    "echo": _handle_echo,
    "fetch_title": _handle_fetch_title,
}
```

---

## Browser Shell (Electron)

The `browser-shell/` directory is a complete Electron 29 application.

Key extension points:

| File | What to modify |
|---|---|
| `main.js` — `registerIPC()` | Add new IPC handlers |
| `main.js` — `buildAppMenu()` | Add menu items |
| `preload.js` | Expose new `electronAPI.*` methods |
| `src/browser.js` | Wire new UI events |
| `src/browser.html + .css` | Modify chrome UI |

The gateway origin is `http://127.0.0.1:8080`. Gateway-origin URLs open in a new tab;
all other URLs open in the system browser via `shell.openExternal()`.

---

## Running Tests

```powershell
# From repo root
python -m pytest -q                        # full suite
python -m pytest agent-gateway/tests/ -v  # verbose
python -m pytest -s test_sandbox_manager   # capture off
python -m pytest -k "test_audit"           # keyword filter
```

The top-level `conftest.py` adds `agent-gateway/` to `sys.path` and sets
`SANDBOX_WORKER_PATH` so all tests run without extra environment setup.
