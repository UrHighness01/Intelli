# Intelli Agent Gateway

A hardened local HTTP gateway that validates, authorises, and proxies agent tool calls.
The gateway sits between AI agents and privileged browser/filesystem/network tools and enforces:

- **Schema validation** — every call is checked against a JSON Schema
- **Capability manifests** — per-tool declared capabilities (e.g. `fs.read`, `browser.dom`)
- **Human-in-the-loop approvals** — high-risk tools require explicit admin sign-off
- **RBAC + token auth** — Bearer tokens with access/refresh lifecycle and revocation
- **Per-user tool scoping** — each user can be limited to a specific tool allow-list
- **Provider key management** — store, rotate, and track expiry of LLM API keys
- **Consent/context timeline** — logs every tab snapshot shared with an agent
- **Emergency kill-switch** — instantly blocks all tool calls for incident response
- **Signed releases** — CI builds are signed with Sigstore/cosign

---

## Quickstart

### 1. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure the admin password

```powershell
$env:AGENT_GATEWAY_ADMIN_PASSWORD = "your-strong-password"
```

The first start creates an `admin` user automatically if one does not exist.

### 3. Start the gateway

```powershell
uvicorn app:app --host 127.0.0.1 --port 8080
```

### 4. Run the test suite

```powershell
pytest -q
# Expected: 179 passed, 2 skipped
```

---

## UI pages

| URL | Description |
|-----|-------------|
| `http://127.0.0.1:8080/ui/` | Main UI index |
| `http://127.0.0.1:8080/ui/tab_permission.html` | Browser tab snapshot permission request |
| `http://127.0.0.1:8080/ui/audit.html` | Audit log viewer (dark mode) |
| `http://127.0.0.1:8080/ui/consent.html` | Consent / context-sharing timeline viewer |

---

## Endpoint reference

All endpoints are documented in [`openapi.yaml`](openapi.yaml). Quick summary:

### Health & metrics
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Gateway liveness check |
| GET | `/health/worker` | — | Sandbox worker liveness |
| GET | `/metrics` | — | Prometheus text-format metrics |

### Tool invocation
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/validate` | — | Validate payload against tool schema |
| POST | `/tools/call` | optional Bearer | Submit a tool call; enforces kill-switch and per-user allow-list |
| GET | `/tools/capabilities` | — | List all tool capability manifests |

### Approvals
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/approvals` | — | List pending approvals |
| GET | `/approvals/stream` | — | SSE stream of queue changes |
| GET | `/approvals/{id}` | — | Get single approval status |
| POST | `/approvals/{id}/approve` | admin | Approve a pending call |
| POST | `/approvals/{id}/reject` | admin | Reject a pending call |

### Authentication
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/admin/login` | — | Login; returns `{token, refresh_token}` |
| POST | `/admin/refresh` | — | Exchange refresh token for new access token |
| POST | `/admin/revoke` | admin | Revoke a token |

### Consent timeline
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/consent/timeline` | admin | Fetch context-sharing events |
| DELETE | `/consent/timeline` | admin | Clear timeline (optional origin filter) |

### Emergency kill-switch
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/kill-switch` | admin | Get kill-switch state |
| POST | `/admin/kill-switch` | admin | Activate (body: `{"reason": "..."}`) |
| DELETE | `/admin/kill-switch` | admin | Deactivate and resume |

### Per-user tool permissions
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/users/{username}/permissions` | admin | Get user tool allow-list |
| PUT | `/admin/users/{username}/permissions` | admin | Set or clear allow-list |

### Provider key management
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/providers` | admin | List providers and config status |
| POST | `/admin/providers/{p}/key` | admin | Store a key (with optional TTL) |
| POST | `/admin/providers/{p}/key/rotate` | admin | Rotate active key |
| GET | `/admin/providers/{p}/key/status` | admin | Key existence + expiry status |
| GET | `/admin/providers/{p}/key/expiry` | admin | Full TTL metadata |
| GET | `/admin/providers/expiring` | admin | Keys expiring within N days |
| DELETE | `/admin/providers/{p}/key` | admin | Remove a stored key |

### Chat proxy
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/chat/complete` | Bearer | Proxy chat-completion to a provider |

### Tab context
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/tab/preview` | — | Sanitise and log a tab snapshot |
| GET | `/tab/redaction-rules` | — | Get redaction rules for an origin |
| POST | `/tab/redaction-rules` | admin | Set redaction rules for an origin |

### Audit log
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/audit` | admin | Export last N audit entries |

---

## Authentication

Login with:

```http
POST /admin/login
Content-Type: application/json

{"username": "admin", "password": "your-strong-password"}
```

Response:

```json
{"token": "<access-token>", "refresh_token": "<refresh-token>"}
```

Use `Authorization: Bearer <access-token>` for all protected endpoints.

Access tokens expire after 1 hour (configurable). Refresh tokens last 7 days.

---

## Per-user tool permissions

Restrict which tools a user may call:

```http
PUT /admin/users/alice/permissions
Authorization: Bearer <admin-token>

{"allowed_tools": ["file.read", "noop"]}
```

When `alice` calls `/tools/call` with her token, any tool outside the allow-list
returns **HTTP 403 `tool_not_permitted`**.  
Pass `"allowed_tools": null` to remove all restrictions.

---

## Emergency kill-switch

Instantly block all tool calls (e.g. during an incident):

```http
POST /admin/kill-switch
Authorization: Bearer <admin-token>

{"reason": "CVE-2025-XXXX — halting until patch applied"}
```

All subsequent `/tools/call` requests receive **HTTP 503** while the switch is active.

Resume normal operation:

```http
DELETE /admin/kill-switch
Authorization: Bearer <admin-token>
```

---

## Capability system

Every tool has a JSON manifest in `schemas/capabilities/<category>/<tool>.json` declaring:

- `required_capabilities` — must be in `AGENT_GATEWAY_ALLOWED_CAPS`
- `optional_capabilities` — checked but not blocking
- `risk_level` — `low` / `medium` / `high`
- `requires_approval` — if `true`, call enters the approval queue

Browse all manifests:
```http
GET /tools/capabilities
```

---

## Provider key management

Store an OpenAI key with a 90-day TTL:

```http
POST /admin/providers/openai/key
Authorization: Bearer <admin-token>

{"key": "sk-...", "ttl_days": 90}
```

Rotate before expiry:

```http
POST /admin/providers/openai/key/rotate
Authorization: Bearer <admin-token>

{"key": "sk-new-...", "ttl_days": 90}
```

Check expiring keys (within 7 days):

```http
GET /admin/providers/expiring?within_days=7
Authorization: Bearer <admin-token>
```

---

## Consent / context timeline

Every call to `/tab/preview` is logged to the consent timeline (field names only, no values). View:

```http
GET /consent/timeline?limit=50&origin=https://example.com
Authorization: Bearer <admin-token>
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_GATEWAY_ADMIN_PASSWORD` | *(required)* | Default admin user password |
| `AGENT_GATEWAY_ALLOWED_CAPS` | `fs.read,browser.dom` | Comma-separated capability allow-list; `ALL` for unrestricted |
| `AGENT_GATEWAY_ACCESS_EXPIRE` | `3600` | Access token lifetime in seconds |
| `AGENT_GATEWAY_REFRESH_EXPIRE` | `604800` | Refresh token lifetime in seconds |
| `AGENT_GATEWAY_KEY_METADATA_PATH` | `key_metadata.json` | Path for provider key TTL metadata |
| `AGENT_GATEWAY_CONSENT_PATH` | `consent_timeline.jsonl` | Path for consent event log |
| `AGENT_GATEWAY_KEY_DEFAULT_TTL_DAYS` | `90` | Default TTL for stored provider keys |
| `AGENT_GATEWAY_SSE_POLL_INTERVAL` | `2.0` | Seconds between SSE approval queue polls |
| `SANDBOX_WORKER_PATH` | *(auto-detect)* | Explicit path to sandbox worker script |

---

## Persistent files

| File | Description |
|------|-------------|
| `users.json` | User accounts, roles, and tool allow-lists |
| `revoked_tokens.json` | Revoked token SHA-256 hashes with expiry |
| `audit.log` | JSONL audit trail of all admin actions |
| `redaction_rules.json` | Per-origin field redaction rules |
| `key_metadata.json` | Provider key TTL / rotation metadata |
| `consent_timeline.jsonl` | Context-sharing consent events |

---

## Security model

- **Localhost only** — the gateway binds to `127.0.0.1` by default
- **Bearer token RBAC** — unathenticated callers can only hit public endpoints
- **Per-user tool scoping** — granular allow-lists per user
- **Capability enforcement** — env-var controlled; deny-by-default
- **Human approvals** — high-risk tools queue for admin sign-off
- **Kill-switch** — admin-controlled emergency stop
- **Key rotation** — built-in TTL tracking and rotation API
- **Consent logging** — immutable audit trail of context shared with agents
- **Signed releases** — CI tags are signed with Sigstore (`cosign`)

---

## Development

```powershell
# Run with auto-reload
uvicorn app:app --reload --host 127.0.0.1 --port 8080

# Tests (179 pass, 2 skip)
pytest -q

# Tests with coverage
pytest --cov=. --cov-report=term-missing -q
```

The sandbox worker runs as a subprocess isolated from the main process.
The full sandbox (namespaces, seccomp, cgroups) is intended for production deployment
inside a dedicated container or VM — the current implementation is a scaffold.
