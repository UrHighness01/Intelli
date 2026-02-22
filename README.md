# Intelli — AI-native browser prototype

Intelli is a research prototype for a privacy-first browser with native AI/agent integration.
It combines a **Tab Context Bridge**, a hardened **Agent Gateway**, an Electron desktop browser
shell with multi-tab support, a live admin sidebar, and a hybrid supervisor pipeline that
validates, redacts, approves, and audits every AI tool call before execution.

> **License** — Source-available, non-commercial.
> Free for personal / educational / research use. Commercial use requires written permission.
> See [LICENSE](LICENSE) for full terms.

---

## Repository layout

| Path | Purpose |
|------|---------|
| `ROADMAP.md` | High-level phased roadmap and session progress |
| `ARCHITECTURE.md` | Mermaid architecture diagram (all 20 subsystems) |
| `THREAT_MODEL.md` | Threat model and privacy controls |
| `SECURITY.md` | Security posture and known mitigations |
| `LICENSE` | Source-available non-commercial license |
| `agent-gateway/` | Local agent gateway (FastAPI + 1 087 tests) |
| `agent-gateway/ui/` | 15 dark-mode admin UI pages |
| `agent-gateway/gateway_ctl.py` | CLI management tool for all admin APIs |
| `agent-gateway/openapi.yaml` | Full OpenAPI 3.0.3 spec (20 named tags) |
| `agent-gateway/addons.py` | Addon JS-injection manager + persistence |
| `agent-gateway/tab_snapshot.py` | In-process active-tab HTML snapshot store |
| `browser-shell/` | Electron 29 desktop browser wrapping the gateway (`npm start`) |
| `browser-shell/main.js` | Main process — tab management, IPC, window controls |
| `browser-shell/preload.js` | Context-bridge API surface exposed to chrome renderer |
| `browser-shell/src/` | Chrome renderer — tab bar, address bar, sidebar, addons |
| `docs/` | Deployment, developer guide, and operator runbook |

---

## Agent gateway — feature summary

The gateway (`agent-gateway/`) is the most complete component:

| Feature | Capability |
|---------|------------|
| **Schema validation** | Every tool call checked against JSON Schema |
| **Capability manifests** | Per-tool `required_capabilities` and risk levels |
| **Human-in-the-loop approvals** | High-risk tool calls queue for admin sign-off; SSE stream |
| **RBAC & token auth** | Bearer tokens, refresh/revoke lifecycle |
| **Per-user tool scoping** | Granular allow-list per user (`alice` can only call `file.read`) |
| **Emergency kill-switch** | Instantly blocks all tool calls for incident response |
| **Content filtering** | Literal and regex deny-rules applied before every tool call |
| **Rate limiting** | Per-token and global caps, configurable via UI and API |
| **Agent memory** | Persistent key-value store per agent with optional TTL |
| **Scheduler** | Recurring tool-call tasks with interval, history, and live countdown |
| **Provider key management** | Store, rotate, and track expiry of LLM API keys |
| **Consent / context timeline** | Append-only log of every tab snapshot shared with an agent |
| **Audit log** | Immutable JSONL trail — tail, filter, group-by, CSV export, live follow |
| **Metrics** | Per-tool call counts and p50 / mean latency histograms |
| **Approval webhooks** | Push notifications to external systems on queue events |
| **Chat proxy** | Proxy completions to OpenAI / Anthropic / OpenRouter / Ollama |
| **GDPR export/erase** | Full actor data export and erasure via API |
| **Tab snapshot** | Active-tab HTML pushed automatically; agents read via `GET /tab/snapshot` |
| **Addon system** | Agents write and activate JS addons injected into active tab at runtime |

---

## Browser shell — feature summary

The Electron shell (`browser-shell/`) wraps the gateway with a full tabbed browser UI:

| Feature | Description |
|---------|-------------|
| **Multi-tab** | Create, switch, close tabs; each is an isolated BrowserView |
| **Live tab bar** | Tab bar stays in sync with main process via `tabs-updated` IPC events |
| **Address bar** | URL display, smart navigation, DuckDuckGo fallback for queries |
| **Bookmark star** | ★ button in address bar toggles bookmark for the current page; bookmark list in the panel |
| **Zoom indicator** | Zoom level badge in address bar; updated on `did-finish-load` |
| **Window controls** | Custom minimize / maximize / close buttons (hidden-titlebar mode) |
| **Admin sidebar** | 340 px `BrowserView` with the admin hub; toggle via Ctrl+Shift+A or ☰ |
| **⋮ App menu** | Three-dot native popup with History, Settings, Clear Data, Dev Addons items |
| **Chrome panels** | Five 360 px overlay panels (bookmarks, history, settings, clear-data, dev-addons); `panel-visible` IPC shrinks active BrowserView so panels are never hidden behind it |
| **Tab snapshot** | Page HTML pushed to gateway 1.8 s after each navigation |
| **Addon injection** | Polls `GET /tab/inject-queue` every 3 s; executes pending JS in active tab |
| **Keyboard shortcuts** | Ctrl+T new tab, Ctrl+W close, Ctrl+1-8 switch, Ctrl+L focus URL |

---

## Quickstart

```powershell
# 1. Create virtualenv and install dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r agent-gateway/requirements.txt

# 2. Set admin password
$env:AGENT_GATEWAY_ADMIN_PASSWORD = "your-strong-password"

# 3. Start the gateway
uvicorn app:app --app-dir agent-gateway --reload --host 127.0.0.1 --port 8080

# 4. Run all tests
pytest -q agent-gateway
# Expected: 1087 passed, 2 skipped
```

Open `http://127.0.0.1:8080/ui/` to access the admin hub.

### Desktop browser (Electron) — recommended
```powershell
cd browser-shell
npm install   # first time only
npm start
# Starts the gateway automatically and opens the Intelli browser window
```

---

## Addon system

Agents (or the admin) can write, store, and activate JavaScript addons that are injected
directly into the active browser tab — similar in spirit to browser extensions:

```powershell
# Create an addon
curl -X POST http://127.0.0.1:8080/admin/addons `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"name":"highlight","description":"Highlight links","code_js":"document.querySelectorAll(\"a\").forEach(a=>a.style.outline=\"2px solid lime\")"}'

# Activate it — code runs in active tab within 3 seconds
curl -X POST http://127.0.0.1:8080/admin/addons/highlight/activate `
  -H "Authorization: Bearer $TOKEN"
```

Endpoints: `GET/POST /admin/addons`, `GET/PUT/DELETE /admin/addons/{name}`,
`POST /admin/addons/{name}/activate`, `POST /admin/addons/{name}/deactivate`.

---

## Tab snapshot (agents read active page)

After every navigation the browser pushes the active tab's HTML to the gateway.
Agents retrieve it via `GET /tab/snapshot`:

```json
{
  "url": "https://example.com",
  "title": "Example Domain",
  "timestamp": "2026-01-01T12:00:00+00:00",
  "length": 1256,
  "html": "<!DOCTYPE html>..."
}
```

---

## Admin UI pages

| Page | Description |
|------|-------------|
| `index.html` | Searchable nav hub linking all admin panels |
| `status.html` | Live gateway dashboard — call counts, uptime, alerts, scheduler ETA |
| `audit.html` | Audit log viewer — sort, filter by actor/event, group-by, CSV export |
| `approvals.html` | Pending approval queue — approve or reject queued tool calls |
| `users.html` | User management — create, delete, roles, tool restrictions, last-seen chip |
| `providers.html` | LLM provider key storage, rotation, expiry + live chat proxy test |
| `schedule.html` | Scheduler — create tasks, history sparkline, duration stat cards |
| `metrics.html` | Per-tool call count table with p50 latency column |
| `memory.html` | Agent memory — browse, edit, export-all, import (merge or replace) |
| `content-filter.html` | Deny-rule management (literal and regex patterns) |
| `rate-limits.html` | Per-token and global request-rate configuration |
| `webhooks.html` | Approval webhook registration and event subscriptions |
| `capabilities.html` | Tool capability manifest browser |
| `consent.html` | Context-sharing consent timeline viewer |
| `tab_permission.html` | Browser tab snapshot permission request UI |

---

## CLI (`gateway_ctl.py`)

```powershell
cd agent-gateway

python gateway_ctl.py login
python gateway_ctl.py audit tail --n 50 --actor alice
python gateway_ctl.py audit follow --interval 5          # live stream, Ctrl-C to stop
python gateway_ctl.py audit export-csv --output report.csv
python gateway_ctl.py schedule list --next               # show countdown to next run
python gateway_ctl.py users permissions set alice file.read,noop
python gateway_ctl.py metrics top --n 5
python gateway_ctl.py content-filter add "bad-word" --mode literal
python gateway_ctl.py kill-switch on --reason "incident"
```

See `agent-gateway/README.md` for the full endpoint reference and environment variables.
