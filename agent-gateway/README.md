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
- **Content filtering** — literal and regex deny-lists applied to every tool call
- **Rate limiting** — per-token and global request-rate caps
- **Agent memory** — persistent key-value memory per agent, optionally with TTL
- **Scheduled tasks** — recurring tool-call tasks with cron-like intervals
- **Approval webhooks** — push notifications to external systems on queue events
- **Audit log** — append-only immutable JSONL trail with CSV export
- **Metrics** — per-tool call counts and latency histograms
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
# Expected: ~1087 passed, 2 skipped
```

---

## UI pages

All pages are served at `http://127.0.0.1:8080/ui/<page>`.

| Page | Description |
|------|-------------|
| `index.html` | Admin hub with searchable nav cards |
| `status.html` | Live gateway status dashboard (calls, uptime, alerts, scheduler) |
| `audit.html` | Audit log viewer — sort, filter, group-by, CSV export |
| `approvals.html` | Pending approval queue — approve / reject tool calls |
| `users.html` | User management — create, delete, roles, tool restrictions, last-seen chip |
| `providers.html` | LLM provider key management + chat proxy test panel |
| `schedule.html` | Scheduler task management — create, enable/disable, history sparkline |
| `metrics.html` | Per-tool call counts and p50 latency table |
| `memory.html` | Agent memory viewer — export all / import JSON |
| `content-filter.html` | Content-filter deny-rule management |
| `rate-limits.html` | Per-token and global rate-limit configuration |
| `webhooks.html` | Approval webhook registration and testing |
| `capabilities.html` | Tool capability manifest browser |
| `consent.html` | Consent / context-sharing timeline viewer |
| `tab_permission.html` | Browser tab snapshot permission request |

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
| GET | `/admin/audit` | admin | Tail last N audit entries (filterable by actor, action, since, until) |
| GET | `/admin/audit/export.csv` | admin | Download filtered entries as CSV |

### Users
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/users` | admin | List all users |
| POST | `/admin/users` | admin | Create a user |
| DELETE | `/admin/users/{username}` | admin | Delete a user |
| POST | `/admin/users/{username}/password` | admin | Change password |
| GET | `/admin/users/{username}/permissions` | admin | Get tool allow-list |
| PUT | `/admin/users/{username}/permissions` | admin | Set or clear tool allow-list |

### Content filter
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/content-filter/rules` | admin | List all deny rules |
| POST | `/admin/content-filter/rules` | admin | Add a rule (literal or regex) |
| DELETE | `/admin/content-filter/rules/{index}` | admin | Remove a rule by index |
| POST | `/admin/content-filter/reload` | admin | Reload rules from env/file |

### Rate limits
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/rate-limits` | admin | Get current rate-limit config |
| PUT | `/admin/rate-limits` | admin | Update rate-limit config |

### Agent memory
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/memory/agents` | admin | List all agent IDs with memory |
| GET | `/agents/{id}/memory` | admin | List keys for an agent |
| GET | `/agents/{id}/memory/{key}` | admin | Get a memory entry |
| PUT | `/agents/{id}/memory/{key}` | admin | Set a memory entry (optional TTL) |
| DELETE | `/agents/{id}/memory/{key}` | admin | Delete a memory entry |
| GET | `/admin/memory/export` | admin | Export all memory as JSON |
| POST | `/admin/memory/import` | admin | Import memory (merge or replace) |

### Scheduler
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/schedule` | admin | List scheduled tasks |
| POST | `/admin/schedule` | admin | Create a task |
| GET | `/admin/schedule/{id}` | admin | Get task detail |
| PATCH | `/admin/schedule/{id}` | admin | Update task (enable/disable) |
| DELETE | `/admin/schedule/{id}` | admin | Delete a task |
| POST | `/admin/schedule/{id}/trigger` | admin | Trigger task immediately |
| GET | `/admin/schedule/{id}/history` | admin | Run history |

### Approval webhooks
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/webhooks` | admin | List registered webhooks |
| POST | `/admin/webhooks` | admin | Register a webhook |
| DELETE | `/admin/webhooks/{id}` | admin | Remove a webhook |

### Alerts
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/alerts/config` | admin | Get alert configuration |
| PUT | `/admin/alerts/config` | admin | Update alert thresholds |

### Status & metrics
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/admin/status` | admin | Gateway operational snapshot |
| GET | `/admin/metrics/tools` | admin | Per-tool call counts and latency |

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
| `AGENT_GATEWAY_CONTENT_FILTER_PATH` | *(none)* | Path to JSON file with extra deny rules |
| `AGENT_GATEWAY_MEMORY_PATH` | `agent_memory.json` | Path for persistent agent memory store |
| `AGENT_GATEWAY_AUDIT_PATH` | `audit.log` | Path for the append-only audit JSONL log |
| `SANDBOX_WORKER_PATH` | *(auto-detect)* | Explicit path to sandbox worker script |

---

## Persistent files

| File | Description |
|------|-------------|
| `users.json` | User accounts, roles, and tool allow-lists |
| `revoked_tokens.json` | Revoked token SHA-256 hashes with expiry |
| `audit.log` | Append-only JSONL audit trail — all admin actions and tool events |
| `redaction_rules.json` | Per-origin field redaction rules |
| `key_metadata.json` | Provider key TTL / rotation metadata |
| `consent_timeline.jsonl` | Context-sharing consent events |
| `agent_memory.json` | Persistent agent key-value memory store |
| `schedule_state.json` | Scheduled task definitions and run-count state |

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

# Tests (~1087 pass, 2 skip)
pytest -q

# Tests with coverage
pytest --cov=. --cov-report=term-missing -q
```

The sandbox worker runs as a subprocess isolated from the main process.
The full sandbox (namespaces, seccomp, cgroups) is intended for production deployment
inside a dedicated container or VM — the current implementation is a scaffold.

---

## CLI reference (`gateway_ctl.py`)

The bundled CLI wraps every admin API endpoint:

```powershell
# Authenticate (caches token to .gateway_token)
python gateway_ctl.py login

# — Kill-switch —
python gateway_ctl.py kill-switch status
python gateway_ctl.py kill-switch on --reason "CVE-2025-XXXX"
python gateway_ctl.py kill-switch off

# — Audit —
python gateway_ctl.py audit tail --n 50 --actor alice
python gateway_ctl.py audit export-csv --output report.csv
python gateway_ctl.py audit follow --interval 5 --actor alice

# — Users —
python gateway_ctl.py users list
python gateway_ctl.py users create alice hunter2 --role user
python gateway_ctl.py users permissions set alice file.read,noop
python gateway_ctl.py users permissions clear alice

# — Scheduler —
python gateway_ctl.py schedule list --next
python gateway_ctl.py schedule create "Daily" echo --interval 86400
python gateway_ctl.py schedule history <task-id> --n 20

# — Metrics —
python gateway_ctl.py metrics tools
python gateway_ctl.py metrics top --n 5

# — Content filter —
python gateway_ctl.py content-filter list
python gateway_ctl.py content-filter add "bad-word" --mode literal --label profanity
python gateway_ctl.py content-filter delete 0

# — Provider keys —
python gateway_ctl.py key set openai sk-... --ttl-days 90
python gateway_ctl.py key rotate openai sk-new-...
python gateway_ctl.py key status openai
```
