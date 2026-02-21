# Intelli Browser Roadmap

A practical roadmap for building "Intelli": a next-gen browser combining Brave-level security with native AI/agent integration, HTML context feeding, and agent orchestration.

## Overview
- Goal: ship a secure, extensible browser where AI/agents are first-class citizens—persistent panels, local/remote model routing, and a secure context bridge that feeds DOM snapshots to agents safely and transparently.
- Principles: security-by-default, modular architecture, user control & transparency, and support for hybrid (local+cloud) models.

---

## Phase 1 — Core Engine Foundation
- Fork or embed Chromium to leverage compatibility, performance, and security.
- Implement Brave-style privacy features: tracker/ad-blocking, fingerprint randomization, per-site isolated profiles, and an opt-in crypto wallet.
- Design an extensible architecture: modular services (agent gateway, context bridge, plugin SDK, sandboxing layer).

Deliverables:
- Minimal Chromium-based shell with a persistent sidebar area reserved for the AI panel.
- Privacy defaults and profile isolation implemented.

---

## Phase 2 — Embedded AI & Agent System
- Add a native, persistent chatbox panel (sidebar) integrated into the browser UI (not an extension).
- Implement provider-agnostic LLM routing: support OpenAI, OpenRouter, Anthropic/Claude, Google Gemini, Ollama, and local models. Securely store API keys (browser OS secure storage / encrypted local vault).
- Launch a local Agent Gateway on browser start (OpenClaw-compatible API if feasible). The gateway exposes a local HTTP/IPC endpoint for agents and orchestrators.

Deliverables:
- Sidebar UI + provider selector and secure key management.
- Local agent gateway process and basic agent lifecycle management (start/stop/list).

---

## Phase 3 — Contextual Awareness (HTML Feeding)
- Tab Context Bridge: capture structured snapshots of the active tab (DOM tree, meta, URL, selected text, frame origins, resource metadata).
- Expose that context to the agent gateway via IPC or a local HTTP API with strict per-site permissions.
- Implement privacy controls: global pause, per-site allow/deny, automatic masking of known sensitive fields (password inputs, CVVs). Maintain an audit log of what was shared.

Deliverables:
- Secure tab-to-agent feed with permission UI and redaction options.

---

## Phase 4 — Agent Tools, Actions & Add-on Creation
- Tool Call Proxy: map agent tool calls to browser APIs (file ops, script execution, automated click/scroll/input replay) behind a validated gateway.
- One-click Addon Creation: let the AI scaffold and inject scoped JS/CSS mini-addons (user must approve and inspect before activation). Use signed sandboxed registries for sharing.
- Task/Goal Management: agents can own multi-step goals, persist progress, and run sub-tasks using tab context (e.g., summarize page PDF, draft reply to thread, autofill form).

Deliverables:
- Tool proxy API with validation layer; addon scaffolder with approval flow.

---

## Phase 5 — Multi-Agent Orchestration (Optional / Advanced)
- Agent lifecycle manager: create/kill/inspect agents and subagents; control resource caps and execution windows.
- Per-agent memory and storage: isolated caches, optional long-term memory with per-domain scoping and purge controls.
- Autonomous exploration (opt-in): agents may suggest or execute browsing tasks with explicit user consent and a replayable audit trail.

Deliverables:
- Agent dashboard with logs, memory inspector, and controls for autonomy and scheduling.

---

## Phase 6 — Developer & Power User Features
- Plugin/Add-on SDK: an AI-native SDK for adding panels, tools, and connectors (supports JS and WASM; optional Python worker sandbox).
- Dev console: event streams (tab events, agent logs, tool results) and quick replay/debug utilities.
- Scripting panel: sandboxed JS/Python REPL for experimental automation; require explicit user approval for persistence and network access.

Deliverables:
- SDK docs, example plugins, and built-in dev console panel.

---

## Phase 7 — UX, Privacy, and Governance
- Security-first defaults: per-site agent permissions, local-model-only modes, and requirement for explicit consent before sending context off-device.
- Transparency: detailed logs of context sent to agents (with redact/recall UI), and a permissioned timeline for agent actions.
- Customizability: themes, repositionable agent panels, voice input, and accessibility features.

Deliverables:
- Privacy dashboard, logs UI, and consent flows.

---

## Extra Ideas
- Built-in Web Automation API: agents can request event replays (click, scroll, input) in a constrained, auditable sandbox.
- Invisible/local-only mode: run agents exclusively on local models with no external network.
- Contextive Memory: per-site memory summaries that can be enabled/disabled; optionally encrypted at rest.
- Crowdsourced mini-addons repository with user moderation and signing.

---

## Critical Cautions & Design Constraints
- Never allow unchecked code execution. All auto-generated scripts/addons must be previewed, signed, and sandboxed.
- Minimize attack surface: sandbox agent processes, restrict network/file APIs by default, perform static/heuristic checks on generated code.
- Modular provider API: do not tie to a single LLM vendor. Provide an abstraction layer for function-calling and tool schemas.

---

## Reliability with Local Models — Supervisor Pattern (Hybrid Strategy)
Local models (<=13B) struggle with strict function-call formatting, schema adherence, and long multi-step tool orchestration. To make agent tooling reliable across model scales:

1. Use a hybrid stack:
   - Local model: general conversation, summarization, and contextual framing.
   - Cloud/bigger model or a specialized supervisor: handle strict function/tool-calling turns and validation.

2. Agent Supervisor Layer:
   - The local model outputs suggestions in pseudo-structured form.
   - Supervisor parses, validates, and converts to canonical tool calls; it enforces schemas, escapes values, and checks preconditions.
   - If validation fails, the supervisor returns a deterministic error token and asks the model to retry or reformat.

3. Optional lightweight alternatives:
   - Small deterministic validators (regex + JSON schema) to correct common formatting issues without cloud calls.
   - Few-shot finetuning on tool-call examples for local models, or use a small on-premise verifier finetuned for your tool schema.

Outcome: this hybrid pipeline ensures reliable tool usage while allowing most conversational work to remain local and private when desired.

---

## Next Steps / Implementation Roadmap
1. Finalize the architecture diagram (agent gateway, IPC, sandboxing boundaries).
2. Prototype the Agent Gateway API and Tab Context Bridge with strict permissions.
3. Implement the agent supervisor prototype (small validator service + schema enforcement).
4. Build the sidebar UI and provider key manager.
5. Iterate on privacy UX and add-on approval flow.

---

## Notes
- This document is a living roadmap. Prioritize security and user consent throughout implementation. The hybrid supervisor pattern is recommended to make agent tooling robust across both local and cloud LLMs.

If you want, I can now:
- produce a detailed architecture diagram and API spec for the Agent Gateway and Tab Context Bridge,
- or scaffold an initial `agent-gateway` prototype (local HTTP server + schema validator).

---

Created by: Intelli design draft


## Implementation status (updated 2026-02-21 → refreshed this session)

- [x] Prototype Agent Gateway: local FastAPI gateway (`agent-gateway/`) with schema validation, supervisor pipeline, and tests.
- [x] Review and refine roadmap with hybrid pipeline sketch (this document now includes a detailed hybrid supervisor design).
- [x] Tab Context Bridge: DOM snapshotter with input-field redaction and a preview UI (`agent-gateway/tab_bridge.py`, `agent-gateway/ui/tab_permission.html`).
- [x] Schema validator with deterministic error tokens: Supervisor now emits structured validation errors with deterministic tokens and feedback.
- [x] Approval workflow: approval API endpoints, minimal approval UI, and approval queue tests.
- [x] Per-tool schema registry: example JSON schemas and runtime enforcement in the supervisor.
- [x] Persisted redaction rules + audit log: redaction rules persisted to `agent-gateway/redaction_rules.json`, audit entries appended to `agent-gateway/audit.log`, and admin-protected endpoints.
- [x] OS-backed user credential storage: passwords stored in the OS keyring when available, with safe fallback to local storage.
- [x] Subprocess sandbox worker: `agent-gateway/sandbox/worker.py` and IPC integration in `agent-gateway/sandbox/proxy.py`.
- [x] Worker lifecycle & health checks: `agent-gateway/sandbox/manager.py` and `/health/worker` + `/metrics` endpoints.
- [x] Sandbox tests: `test_sandbox_worker.py`, `test_sandbox_manager.py`, `test_worker_health_api.py`.
- [x] Import shim `agent_gateway/__init__.py` to allow package imports from the hyphenated folder.
- [x] ProviderKeyStore resilient fallback: `keyring` optional with file-backed fallback.
- [x] Auth system (RBAC): token-based auth (`/admin/login`, `/auth/refresh`, `/auth/revoke`) with PBKDF2 passwords, access/refresh token lifecycle, and role checks.
- [x] Persistent token revocation: revoked tokens stored in `revoked_tokens.json` (SHA-256-hashed with expiry).
- [x] Monitoring / metrics / audit export: Prometheus-format `/metrics` and authenticated `/admin/audit` JSON-lines export.
- [x] SSE real-time approval stream: `/approvals/stream` with env-configurable poll interval and keepalive.
- [x] Docker runner hardening: `--cap-drop ALL`, `no-new-privileges:true`, configurable seccomp profile, PID limit, and FD ulimit.
- [x] CI/CD security hardening: SBOM generation and dependency scanning.
- [x] Rate limiter: sliding-window per-client-IP rate limiter (`rate_limit.py`) wired into `/tools/call`, `/validate`, `/admin/login`, and `/chat/complete`; configurable via env vars.
- [x] Risk scorer: heuristic risk scoring in `supervisor.py` (`compute_risk()`) — classifies tool calls as `low`/`medium`/`high` based on tool name, arg key patterns, traversal/injection signatures, and payload size; `high` calls queued for approval automatically.
- [x] Provider adapters: concrete adapters for OpenAI, Anthropic, OpenRouter, and Ollama (`providers/adapters.py`) with unified `chat_complete()` interface.
- [x] Provider key management API: `POST/GET/DELETE /admin/providers/{provider}/key`, `GET /providers` (admin-gated), and a `/chat/complete` proxy endpoint.
- [x] Audit review UI: `ui/audit.html` — dark-mode HTML/JS viewer for `/admin/audit` with token login, event/actor/details filtering, and newest-first display.
- [x] **Tool capability manifests + verifier**: 13 capability manifest JSON files (`schemas/capabilities/**/*.json`), `tools/capability.py` (CapabilityVerifier), wired into supervisor and app; `GET /tools/capabilities`; `AGENT_GATEWAY_ALLOWED_CAPS` env var; 403 on capability_denied.
- [x] **Provider key TTL + rotation**: `providers/key_rotation.py` (KeyMetadata, store_key_with_ttl, rotate_key, list_expiring); wired into `POST /admin/providers/{provider}/key` (+ `ttl_days`), new `POST .../key/rotate`, `GET .../key/expiry`, `GET .../key/status` (+ expiry fields), `GET /admin/providers/expiring`.
- [x] **Consent / context timeline**: `consent_log.py` (append-only JSONL, field-name-only logging); wired into `/tab/preview` (logs on every preview call); `GET /consent/timeline` + `DELETE /consent/timeline` admin endpoints; `ui/consent.html` dark-mode timeline viewer.
- [x] **Signed-release CI workflow**: `.github/workflows/release.yml` — triggers on `v*` tags, runs test suite, generates SBOM (pip-licenses), runs pip-audit vulnerability scan, builds wheel+sdist, signs artifacts with Sigstore keyless signing, creates GitHub Release.
- [x] **E2E integration tests**: `tests/test_e2e.py` — 25 tests covering full HTTP flows: auth, tool call pipeline, capability verifier, approval lifecycle, provider key lifecycle (store → rotate → expiry), audit trail, consent timeline, and rate limiter.
- [x] **Unit tests for new modules**: `tests/test_capability.py` (35 assertions), `tests/test_consent_log.py` (20 assertions), `tests/test_key_rotation.py` (20 assertions), `tests/test_provider_api.py` extended with rotation/expiry tests.
- [x] **Per-user scoped tool permissions**: `auth.py` — `get_user_allowed_tools()` / `set_user_allowed_tools()` against `users.json`; `GET /admin/users/{username}/permissions` and `PUT /admin/users/{username}/permissions` admin endpoints; enforced in `POST /tools/call` (403 `tool_not_permitted` when caller's token maps to a user with a restrict allow-list). 13 new tests in `tests/test_user_permissions.py`.
- [x] **Agent kill-switch (emergency stop)**: `threading.Event()` kill-switch in `app.py`; `POST /admin/kill-switch` (activate with reason), `DELETE /admin/kill-switch` (deactivate), `GET /admin/kill-switch` (status) — all admin-gated; activation causes `/tools/call` to return 503; actions audited. 5 new tests in `tests/test_kill_switch.py`.
- [x] **OpenAPI spec overhaul**: `openapi.yaml` expanded from 262 lines to full coverage of all 30+ endpoints: health, metrics, tool invocation, approvals/SSE, consent timeline, auth, kill-switch, per-user permissions, provider key management (store/rotate/expiry/delete), chat proxy, tab preview, and redaction rules — with complete request/response schemas and security declarations.
- [x] **README overhaul**: comprehensive documentation including UI page URLs, full endpoint reference table (grouped by category), authentication guide, per-user permissions guide, kill-switch guide, capability system guide, provider key management guide, consent timeline guide, environment variables reference, persistent files reference, security model summary, and development quickstart.
- [x] **Tests: 291 passing, 2 skipped** (up from 179; +112 new tests: 7 per-user rate-limit, 11 GDPR export, 94 fuzzer payloads).
- [x] **Agent memory store** (Phase 5 — Per-agent memory and storage): `agent_memory.py` — thread-safe JSON-backed per-agent key-value store; path-traversal-safe agent-ID validation; `memory_get/set/delete/list/clear` and `list_agents()`; admin-gated REST API: `GET /agents`, `GET/POST/DELETE /agents/{id}/memory`, `DELETE /agents/{id}/memory/{key}`. 10 unit-level tests, 10 HTTP tests in `tests/test_agent_memory.py`.
- [x] **Content moderation filter** (Abuse, Safety & Moderation): `content_filter.py` — configurable deny-list supporting literal and regex patterns; recursive string extraction from any JSON value; raises HTTP 403 `content_policy_violation` on match; patterns sourced from env var and persisted JSON file; runtime admin API: `GET/POST/DELETE /admin/content-filter/rules`, `POST /admin/content-filter/reload`; enforced in `POST /tools/call` (args) and `POST /chat/complete` (messages). 24 unit tests, 7 HTTP tests, 3 integration enforcement tests in `tests/test_content_filter.py`.
- [x] **Metrics dashboard UI** (Monitoring & Observability): `ui/metrics.html` — dark-theme live dashboard matching existing UI style; polls `GET /metrics` (Prometheus text format) every 5 s; parses all metric/label/type/help lines; shows 6 summary cards (tool calls, validation errors, pending approvals, provider requests, provider errors, rate-limit hits); full metric table with name/labels/type/help/value columns and per-metric sparklines; client-side filter bar; session-stored Bearer token.
- [x] **Memory key TTL/expiry**: `agent_memory.py` extended with per-key TTL — `memory_set()` now accepts `ttl_seconds`; values stored as `{"__v": value, "__exp": unix_ts}` wrapper; `_load_active()` prunes expired on every read; new `memory_prune(agent_id)` (remove only expired, returns count) and `memory_get_meta(agent_id, key)` (returns `{value, expires_at}`) functions; `app.py` updated: `MemoryUpsertRequest.ttl_seconds`, GET endpoint returns `expires_at`, new `POST /agents/{id}/memory/prune` (admin-gated). 14 new TTL unit tests + 3 HTTP tests in `tests/test_agent_memory.py` (54 total).
- [x] **Agent memory browser UI**: `ui/memory.html` — dark-theme two-column admin browser; left panel lists agents (from `GET /agents`), right panel shows key-value table with Edit/Delete per row, Add form (key, value, optional TTL), Prune expired and Clear all buttons; expiry shown as relative time with color-coded warning (< 5 min) and expired states; inline editing (click Edit → input → Enter to save); toast notifications; sessionStorage Bearer token.
- [x] **OpenAPI spec — new endpoints**: `openapi.yaml` extended with full definitions for: `GET /agents`, `GET/POST/DELETE /agents/{id}/memory`, `GET/DELETE /agents/{id}/memory/{key}`, `POST /agents/{id}/memory/prune`, `GET/POST /admin/content-filter/rules`, `DELETE /admin/content-filter/rules/{index}`, `POST /admin/content-filter/reload`; added `memory` and `content-filter` tags; `ContentFilterRule` schema in components.
- [x] **Tests: 372 passing, 2 skipped** (up from 355; +17 new tests: 34 agent memory TTL/HTTP, +3 HTTP memory/prune).
- [x] **Rate-limit admin API**: `rate_limit.py` extended with `get_config()`, `update_config(**kwargs)`, `usage_snapshot()` (runtime read/write of all six rate-limit globals without restart); new admin-gated REST endpoints: `GET /admin/rate-limits` (config + live per-client snapshot), `PUT /admin/rate-limits` (reconfigure at runtime, audited), `DELETE /admin/rate-limits/clients/{client_key}` (evict one IP), `DELETE /admin/rate-limits/users/{username}` (evict one user); `gateway_ctl.py` extended with `rate-limits status|set|reset-client|reset-user` sub-commands. 27 tests in `tests/test_rate_limit_admin.py`.
- [x] **Provider health-check endpoint**: `GET /admin/providers/{provider}/health` (admin-gated) queries `ProviderKeyStore` for key presence and calls `adapter.is_available()`; returns `{'status': 'ok'|'no_key'|'unavailable', 'configured': bool, 'available': bool}`; 400 for unknown providers; `gateway_ctl.py` `provider-health <provider>` command. 9 tests in `tests/test_provider_health.py`.
- [x] **Approval webhooks**: `webhooks.py` — registry of HTTP callback URLs notified on `approval.created`, `approval.approved`, `approval.rejected` events; persisted to `webhooks.json`; outbound delivery is fire-and-forget via `ThreadPoolExecutor` (urllib, no extra deps); admin-gated CRUD: `POST /admin/webhooks` (201, validates URL + events), `GET /admin/webhooks`, `GET /admin/webhooks/{id}`, `DELETE /admin/webhooks/{id}`; fire hooks integrated into `/approvals/{id}/approve`, `/approvals/{id}/reject`, and the `pending_approval` branch of `/tools/call`; `gateway_ctl.py` `webhooks list|add|delete` commands; `openapi.yaml` updated with all new paths + `Webhook`/`RateLimitConfig`/`BadRequest` components. 35 tests in `tests/test_webhooks.py`.
- [x] **Tests: 443 passing, 2 skipped** (up from 372; +71 new tests: 27 rate-limit admin, 9 provider health, 35 webhooks).
- [x] **GDPR/consent export API**: `GET /consent/export/{actor}` (data-subject access, all entries for an actor) and `DELETE /consent/export/{actor}` (right to erasure); backed by `export_actor_data()` / `erase_actor_data()` in `consent_log.py`; admin-gated and audited. 11 new tests in `tests/test_gdpr_export.py`.
- [x] **Per-user rate limits**: per-username sliding-window quota (`check_user_rate_limit()`) in `rate_limit.py` with separate `_user_windows` dict; enforced in authenticated `POST /tools/call` and `POST /chat/complete`; configurable via `AGENT_GATEWAY_USER_RATE_LIMIT_REQUESTS` / `AGENT_GATEWAY_USER_RATE_LIMIT_WINDOW`. 7 new tests in `tests/test_per_user_rate_limit.py`.
- [x] **Expanded fuzzing harness**: `tests/test_fuzzer_payloads.py` — 112 tests across two surfaces: (1) ToolCall structural fuzzing (missing fields, wrong types, oversized 1 MB args, deeply nested dicts, 18 injection string variants × 3 arg positions); (2) `/tab/preview` DOM injection fuzzing (16 adversarial HTML payloads, 500 KB flood, 5 000-input document, unicode extremes). Key invariant: no fuzz input may produce HTTP 5xx.
- [x] **CLI operator tool** (`gateway_ctl.py`): argparse CLI wrapping the admin API — commands: `login`, `kill-switch on/off/status`, `permissions get/set/clear`, `audit tail`, `key set/rotate/status/expiry/delete`, `providers list/expiring`, `consent export/erase/timeline`, `webhooks list/add/delete`, `rate-limits status/set/reset-client/reset-user`, `provider-health`. Reads token from `~/.config/intelli/gateway_token` cache or `GATEWAY_TOKEN` env var. Works with `httpx` or stdlib `urllib` fallback.
- [x] **Content filter UI**: `ui/content-filter.html` — dark-theme two-column admin UI matching the style of `ui/memory.html` and `ui/metrics.html`; left panel shows deny-rule table (pattern + mode badge + label + delete), add-rule form (pattern, mode dropdown, label), reload-from-disk button; right panel is a live test-input textarea that evaluates text client-side against the fetched rules and shows PASS/BLOCK result with matched rule name; sessionStorage Bearer token.
- [x] **Memory export/import (backup & recovery)**: `agent_memory.py` extended with `export_all()` (full live snapshot → `{agents, agent_count, key_count, exported_at}`) and `import_all(data, merge=True)` (merge or replace per-agent memory); admin-gated REST endpoints `GET /admin/memory/export` and `POST /admin/memory/import`; audited; `openapi.yaml` updated with both paths. 19 new tests across `TestExportAll`, `TestImportAll`, `TestMemoryExportImportHTTP` in `tests/test_agent_memory.py`.
- [x] **Webhook delivery log**: `webhooks.py` extended so `_deliver()` records every outbound attempt (timestamp, event, status, HTTP status code, error) in a per-hook in-memory `deque` (max 100 entries, newest-first); new `get_deliveries(hook_id, limit)` function; admin-gated `GET /admin/webhooks/{hook_id}/deliveries` endpoint (returns `{hook_id, count, deliveries}`; 404 for unknown hook); `openapi.yaml` updated. 11 new tests across `TestGetDeliveries` and `TestDeliveriesEndpoint` in `tests/test_webhooks.py`.
- [x] **Tests: 473 passing, 2 skipped** (up from 443; +30 new tests: 19 memory export/import, 11 webhook delivery log).
- [x] **User management API**: `auth.py` extended with `list_users()` (returns `[{username, roles, has_tool_restrictions}]`, no passwords), `delete_user(username)` (protected against deleting built-in `admin`), and `change_password(username, new_password)`; four new admin-gated REST endpoints: `GET /admin/users` (list all), `POST /admin/users` (create, 201 / 409 on duplicate), `DELETE /admin/users/{username}` (403 for admin, 404 if missing), `POST /admin/users/{username}/password` (404 if missing); all mutating actions audited; `openapi.yaml` updated with new paths and `UserSummary` schema. 33 new tests in `tests/test_user_management.py`.
- [x] **Approvals dashboard UI**: `ui/approvals.html` — dark-theme real-time approval queue viewer; SSE-driven live updates via `GET /approvals/stream` with auto-reconnect and green/red live-dot indicator; each pending item rendered as a card showing request ID, tool name, args (pretty JSON), risk badge (LOW/MED/HIGH with %-score), timestamp, plus per-card ✓ Approve / ✗ Reject buttons (admin-gated REST calls); resolved items move to a session history table; empty-state when queue is clear; sessionStorage Bearer token; manual Refresh button.
- [x] **Webhooks admin UI**: `ui/webhooks.html` — dark-theme two-column webhook manager; left panel shows add-registration form (URL, per-event checkboxes: `approval.created/approved/rejected`) with Register button; hook list below with URL, event chips, created-at and ✕ Delete button; selecting a hook loads its delivery history in the right panel (table: timestamp, event, status badge ok/error, HTTP code, error message); both panels independent-refreshable; sessionStorage Bearer token; auto-connects on load when token is cached.
- [x] **Tests: 506 passing, 2 skipped** (up from 473; +33 new tests: 11 auth unit, 22 HTTP endpoint).
- [x] **Agent task scheduler**: `scheduler.py` — interval-based recurring tool-call scheduler with background daemon thread (1 s tick), JSON persistence (`schedule.json`), and full CRUD API (`add_task`, `list_tasks`, `get_task`, `delete_task`, `set_enabled`, `update_task`); executor injected at startup via `set_executor(supervisor.process_call)` to avoid circular imports; five new admin-gated REST endpoints in `app.py`: `GET/POST /admin/schedule`, `GET/PATCH/DELETE /admin/schedule/{task_id}`; `openapi.yaml` updated with scheduler paths and `ScheduledTask` schema.
- [x] **User management UI**: `ui/users.html` — dark-theme two-column admin interface; left panel: create-user form (username, password, role checkboxes) + user list with avatar initials, role chips (red admin / purple user), yellow dot for users with tool restrictions, and active selection; right panel: dynamically rendered detail with change-password section, roles display, and allowed-tools tag editor (add by typing, remove ✕, Save/Clear → `PUT /admin/users/{username}/permissions`); delete user button (hidden for built-in `admin`).
- [x] **Admin navigation hub**: `ui/index.html` replaced with a dark-theme admin landing page; 9 tool cards (Approvals, Users, Agent Memory, Metrics, Webhooks, Content Filter, Audit Log, Consent Timeline, Tab Preview) each link to their respective UI; live status bar polls `GET /health`, `/approvals`, and `/metrics` every 10 s; quick-link row points to Swagger, ReDoc, and key API paths.
- [x] **Tests: 560 passing, 2 skipped** (up from 506; +54 new tests: 33 scheduler module unit tests, 21 schedule HTTP endpoint tests — `tests/test_scheduler.py`).
- [x] **Scheduler Prometheus metrics**: `scheduler.py` now imports `metrics`; `_run_task()` emits `scheduler_runs_total{task}` and `scheduler_errors_total{task}` counters plus a `scheduler_run_duration_seconds{task}` histogram observation; `scheduler_tasks_total` gauge updated on `add_task()` and `delete_task()`.
- [x] **Scheduler trigger endpoint**: `trigger_task(task_id)` function in `scheduler.py` sets `next_run_at` to the past so the daemon picks it up within 1 s; `POST /admin/schedule/{task_id}/trigger` (202) added to `app.py` and `openapi.yaml`.
- [x] **Scheduler UI**: `ui/schedule.html` — dark-theme two-column task manager; left panel: collapsible create form (name, tool, JSON args, interval, enabled toggle) + task list with status dot (green=enabled), interval badge (auto-formatted 60s→1m→1h), run count; right panel: stat cards (run count, last run, next run), last result/error display, edit form (name/args/interval save), action bar with Enable/Disable toggle, ▶ Run now (trigger), and Delete; auto-connects if sessionStorage token cached.
- [x] **Tests: 572 passing, 2 skipped** (up from 560; +12 new tests: `TestTriggerTask`, `TestSchedulerMetrics`, `TestScheduleTriggerEndpoint`).
- [x] **Scheduler task run history**: `get_history(task_id, limit=50)` added to `scheduler.py` — per-task in-memory `deque(maxlen=50)` records each execution (run counter, timestamp, duration, ok, result, error); populated in `_run_task()`, cleared in `delete_task()`; `GET /admin/schedule/{task_id}/history` endpoint added to `app.py` and `openapi.yaml` (404 if task not found, returns `{task_id, count, history}`); history table + refresh button added to `ui/schedule.html` detail panel (auto-loads on task select); 13 new tests in `tests/test_scheduler.py` (`TestGetHistory` + `TestScheduleHistory`).
- [x] **Rate-limit admin UI**: `ui/rate-limits.html` — dark-theme two-column interface; left panel: config editor with enabled toggle, IP limits (max\_requests, window\_seconds, burst) and user limits (user\_max\_requests, user\_window\_seconds) fields, Apply button (`PUT /admin/rate-limits`); right panel: live usage snapshot polling every 10 s (`GET /admin/rate-limits`), client table (key, type IP/user, hits, remaining) with per-row Evict buttons (`DELETE /admin/rate-limits/clients/{key}` or `/users/{username}`); auto-connects from sessionStorage token; Rate Limits card added to `ui/index.html` nav hub.
- [x] **Scheduler metric cards in metrics.html**: two new summary cards (`scheduler_runs_total` labelled green, `scheduler_errors_total` labelled red) added to `ui/metrics.html` cards grid; `updateCards()` JS updated to populate them on each poll cycle.
- [x] **Tests: 585 passing, 2 skipped** (up from 572; +13 new tests: `TestGetHistory` (8 unit) + `TestScheduleHistory` (5 HTTP)).
- [x] **Audit server-side filtering + CSV export**: `GET /admin/audit` enhanced with `actor`, `action`, `since`, `until` query params — substring match on actor/event fields, ISO-8601 datetime range (raises HTTP 400 on invalid input); new `GET /admin/audit/export.csv` endpoint (same filters, `text/csv` response, `Content-Disposition: attachment; filename="audit.csv"`); `ui/audit.html` updated with two `datetime-local` date-range pickers, ↓ CSV download button (fetch+blob pattern), and `_serverParams()` helper wiring all filters to server-side API; `openapi.yaml` updated with 4 new query parameters and the new CSV path.
- [x] **Scheduler CLI commands**: `gateway_ctl.py` `schedule` subcommand registered in `_build_parser()` with 8 sub-actions: `list`, `get <task_id>`, `create <name> <tool> [--args JSON] [--interval SECONDS] [--disabled]`, `delete <task_id>`, `enable <task_id>`, `disable <task_id>`, `trigger <task_id>`, `history <task_id>`.
- [x] **Providers dashboard UI**: `ui/providers.html` — dark-theme provider management page; 2×2 grid of 4 provider panels (openai, anthropic, openrouter, ollama); each panel shows live health status badge (ok/no_key/unavailable), configured/available flags, TTL/expiry line (colour-coded: red <7 days, yellow <30 days), and key management controls (Store / Rotate / Delete); expiring-keys table polling `GET /admin/providers/expiring?within_days=7`; auto-refreshes health every 30 s; sessionStorage Bearer token with auto-connect; Providers nav card added to `ui/index.html`.
- [x] **Tests: 594 passing, 2 skipped** (up from 585; +9 new tests in `tests/test_metrics_api.py`: audit filter by actor, action, since/until exclusion, invalid datetime 400 ×2, CSV content-type, Content-Disposition header, and auth guard).
- [x] **Type-checker fixes (13 errors)**: `users.html` — removed invalid `title:` CSS property from `.restriction-dot`; `webhooks.py` — wrapped `resp.status` in `int(...or 0)` to satisfy `int | None`; `test_metrics_api.py` — changed `details: dict = None` to `details: dict | None = None`; `test_per_user_rate_limit.py` and `test_content_filter.py` — added `# type: ignore[assignment]` to `HTTPException.detail` dict annotations.
- [x] **`GET /admin/status` endpoint**: returns JSON summary of gateway state — `version`, `uptime_seconds`, `kill_switch_active`, `kill_switch_reason`, `tool_calls_total` (Prometheus counter), `pending_approvals`, `scheduler_tasks`, `memory_agents`; admin Bearer required; documented in `openapi.yaml`.
- [x] **`gateway-ctl status` command**: pretty-prints the status summary with coloured kill-switch indicator.
- [x] **Audit CLI filter flags**: `audit tail` now accepts `--actor`, `--action`, `--since`, `--until` (ISO-8601) alongside `--n`; new `audit export-csv --output FILE` subcommand downloads the filtered CSV directly via urllib.
- [x] **`provider-health` CLI subcommands**: refactored from a single positional-arg command to three sub-actions — `check <provider>` (single check), `list` (polls all 4 providers and prints a health table), `expiring [--within-days N]` (calls `GET /admin/providers/expiring`).
- [x] **Tests: 599 passing, 2 skipped** (up from 594; +5 new tests: `test_gateway_status_requires_auth`, `test_gateway_status_returns_expected_fields`, `test_gateway_status_uptime_is_positive`, `test_gateway_status_kill_switch_off_by_default`, `test_gateway_status_reflects_kill_switch_on`).
- [x] **Audit actor attribution**: added `_actor(token)` helper to `app.py` that resolves an admin Bearer token to the authenticated username via `auth.get_user_for_token()`; all 18 `_audit()` call sites updated from `actor=(token[:6]+'...')` to `actor=_actor(token)`; also fixed a bug in `memory_import` where `'admin'` was passed as `details` and the info dict as `actor` (positional args reversed). New tests in `tests/test_audit_actor.py`.
- [x] **Webhook HMAC signing**: `register_webhook()` in `webhooks.py` now accepts `secret=''`; secret stored in hook dict (persisted to `webhooks.json`); `_deliver()` computes `hmac.new(secret.encode(), body, sha256).hexdigest()` and sends `X-Intelli-Signature-256: sha256=<hex>` header when secret is non-empty; `WebhookCreate` FastAPI model gains `secret: str = ''` field; `create_webhook` endpoint passes it through and audit-logs `'signed': True/False`; `openapi.yaml` updated with `secret` field and HMAC description. 7 new tests in `tests/test_webhooks.py` (`TestHMACSigning`).
- [x] **`ui/status.html` — live gateway dashboard**: polls `GET /admin/status` every 5 s; cards for version, uptime, tool calls, pending approvals, scheduler tasks, memory agents; kill-switch panel with Arm/Disarm controls and 🔴/🟢 indicator; sessionStorage auto-connect; Status nav card added to `ui/index.html`.
- [x] **Tests: 615 passing, 2 skipped** (up from 599; +16 new tests: 9 in `tests/test_audit_actor.py` for `_actor()` helper and audit log assertions, 7 in `tests/test_webhooks.py::TestHMACSigning` for HMAC header presence, value correctness, and endpoint integration).
- [ ] Browser integration: embed Chromium + sidebar UI and wire the renderer to POST snapshots to the gateway.

The prototype implementation lives under `agent-gateway/` and includes tests and a README with quickstart instructions.

## Remaining steps — recommended priority

1. Wire the Tab Snapshot Preview into the browser renderer and expose a per-tab permission prompt (high priority).
2. ~~Implement provider adapters and move API keys into an OS keyring or vault~~ — done (OpenAI, Anthropic, OpenRouter, Ollama adapters + admin key management API + `/chat/complete` proxy). ~~Remaining: key rotation, scoped permissions per-user, TTL-based expiry~~ — **key rotation + TTL done** (`providers/key_rotation.py`, rotate/expiry API). ~~Remaining: scoped permissions per-user~~ — **done** (`GET/PUT /admin/users/{username}/permissions`, enforced in `/tools/call`).
3. ~~Harden the Tool Proxy: create a sandboxed helper process with capability restrictions~~ — Docker runner now applies `--cap-drop ALL`, `no-new-privileges`, PID/FD limits, and optional seccomp profile. ~~Remaining: sign tool contracts and enforce a formal capability model~~ — **capability model done** (`tools/capability.py`, 13 manifests, CapabilityVerifier, `AGENT_GATEWAY_ALLOWED_CAPS`).
4. ~~Replace dev `X-API-Key` with an auth system (local RBAC, operator accounts)~~ — done. ~~Audit review UI~~ — done (`ui/audit.html`). ~~Consent timeline and per-tab permission UI~~ — **done** (`consent_log.py`, `ui/consent.html`, `/consent/timeline` endpoints).
5. ~~Rate limiting~~ — done (`rate_limit.py`, sliding-window per-IP, env-configurable, wired into write endpoints).
6. ~~Risk scorer~~ — done (`compute_risk()` in supervisor — heuristic `low/medium/high` based on tool name, arg patterns, traversal/injection signatures).
7. ~~Expand OpenAPI/tool contracts and generate client SDKs for plugins and provider adapters~~ — **done** (`openapi.yaml` fully expanded: all 40+ endpoints documented with request/response schemas, security declarations, and reusable components; latest additions: agent memory CRUD + TTL/prune, content-filter admin, `memory` and `content-filter` tags, `ContentFilterRule` schema).
8. ~~Add CI gates: SBOM, dependency scanning~~ — done. ~~Remaining: signed-release workflow~~ — **done** (`.github/workflows/release.yml` — `v*` tag trigger, pip-audit, pip-licenses SBOM, wheel build, Sigstore keyless signing, GitHub Release upload).
9. ~~Add E2E tests and a fuzzing harness for the Tab Bridge and supervisor~~ — **E2E done** (`tests/test_e2e.py`, 25 tests). **Expanded fuzzing harness done** (`tests/test_fuzzer_payloads.py`, 94 adversarial payloads, 18 injection variants × 3 positions, 16 DOM injection vectors, never-5xx invariant enforced).
10. ~~Incident Response: emergency agent kill-switch~~ — **done** (`POST/DELETE/GET /admin/kill-switch`, thread-safe `threading.Event()`, audited, blocks all `/tools/call` with 503).
11. ~~Per-user rate limits~~ — **done** (`check_user_rate_limit()` in `rate_limit.py`, separate `_user_windows` dict, wired into `/tools/call` and `/chat/complete`, env-configurable).
12. ~~GDPR/consent export API~~ — **done** (`GET/DELETE /consent/export/{actor}`, backed by `export_actor_data`/`erase_actor_data` in `consent_log.py`, admin-gated and audited).
13. ~~Operator CLI~~ — **done** (`gateway_ctl.py` — `login`, `kill-switch`, `permissions`, `audit`, `key`, `providers`, `consent` commands; httpx or stdlib fallback; token cache).
14. ~~Per-agent memory store~~ — **done** (`agent_memory.py`, thread-safe JSON per-agent store, full CRUD REST API, path-traversal-safe IDs, admin-gated endpoints). ~~Memory key TTL/expiry~~ — **done** (`ttl_seconds` param, `_load_active` auto-prune, `memory_prune`, `memory_get_meta`, `/prune` endpoint). ~~Agent memory browser UI~~ — **done** (`ui/memory.html`, two-column dark-theme browser, inline edit, TTL display, toast notifications).
15. ~~Content moderation filter~~ — **done** (`content_filter.py`, literal + regex deny rules, recursive string check, HTTP 403 on match, enforced in `/tools/call` and `/chat/complete`, runtime admin API, env-var + file config).
16. ~~Metrics dashboard UI~~ — **done** (`ui/metrics.html`, Prometheus poll, sparklines, 6 summary cards, full table with filter bar).
17. ~~Rate-limit admin API~~ — **done** (`get_config()`, `update_config()`, `usage_snapshot()` in `rate_limit.py`; `GET/PUT /admin/rate-limits`, `DELETE /admin/rate-limits/clients/{key}`, `DELETE /admin/rate-limits/users/{username}`).
18. ~~Provider health-check endpoint~~ — **done** (`GET /admin/providers/{provider}/health`; returns `ok`/`no_key`/`unavailable`).
19. ~~Approval webhooks~~ — **done** (`webhooks.py`, persisted registry, fire-and-forget ThreadPoolExecutor delivery, CRUD at `/admin/webhooks`, fired on `approval.created/approved/rejected`).
20. ~~Content filter UI~~ — **done** (`ui/content-filter.html`, two-column dark-theme admin browser, live client-side test panel, reload-from-disk, sessionStorage token).
21. ~~Memory export/import (backup & recovery)~~ — **done** (`export_all()`/`import_all()` in `agent_memory.py`; `GET /admin/memory/export`, `POST /admin/memory/import`; merge and replace modes; audited).
22. ~~Webhook delivery log~~ — **done** (`_delivery_log` deque in `webhooks.py`; `_deliver()` records every attempt; `get_deliveries(hook_id, limit)`; `GET /admin/webhooks/{hook_id}/deliveries`; 404 for unknown hook).
23. Browser integration: embed Chromium + sidebar UI + renderer-to-gateway bridge (long-term, high complexity).

Refined Hybrid Supervisor Pipeline (implementation details)
--------------------------------------------------------

Overview:
- The hybrid supervisor mediates between local small models and optional cloud/specialized verifiers. It ensures tool-call correctness, enforces per-tool schemas, redacts sensitive fields, and decides whether a call requires elevation (human approval or a stronger verifier).

Components & responsibilities:
- Ingress adapter: receives model outputs (pseudo-structured suggestions) and normalizes them into a canonical ToolCall envelope.
- Schema validator: validates `ToolCall.args` against per-tool JSON Schema; on failure, returns a deterministic error token and structured feedback for the model to retry.
- Sanitizer/Redactor: applies per-origin and per-user redaction rules (from `redaction_rules.json` / keyring-backed policies) and removes or replaces sensitive fields before downstream processing.
- Risk scorer: lightweight heuristics + policy rules that mark calls as `low`, `medium`, or `high` risk (file writes, network access, system commands count as higher risk). Risk score influences whether approvals or a cloud verifier are required.
- Approver/Escalation queue: stores pending high-risk calls for operator approval; includes audit metadata and a replayable sanitized snapshot.
- Verifier (optional cloud/large model): performs deterministic reformatting/validation for strict function-call adherence when local models fail; should be auditable and rate-limited.
- Executor (sandbox proxy): receives approved calls and runs them in a tightly constrained sandbox process with capability and resource limits, returning structured results.

Runtime flow:
1. Model emits pseudo-ToolCall -> Ingress adapter normalizes it.
2. Schema validator checks args.
   - On validation error: return standardized error token + guidance for model to correct format.
3. Sanitizer redacts sensitive fields and records audit entry.
4. Risk scorer computes risk; if `high` then enqueue for approval and return `pending_approval` to orchestrator.
5. If `low/medium`: optionally invoke verifier for deterministic formatting, then dispatch to sandboxed executor.
6. Executor returns result; supervisor records result and emits audit + metrics.

Design constraints & tests:
- Deterministic error tokens for validation failures so models can detect and retry without leaking secrets.
- Unit tests: schema validation, sanitizer redaction, risk scoring rules, approval queue lifecycle.
- Integration tests: full flow with stubbed verifier and sandbox, including replay of sanitized snapshots.
- Fuzzing: feed random DOM snapshots and malformed ToolCalls to the ingress + validator pipeline (CI harness already includes a fuzzer entry point).

Operational notes:
- All approval and audit actions are append-only and signed by the gateway process identity; retain logs for at least 90 days by default.
- Metricization: per-tool invocation counts, failures, mean validation latency, approval rates, and executor resource usage.
- Privacy-by-default: local models operate in a local-only mode; any call that would send data externally must require explicit site-level opt-in.


Choose a next task and I will implement it: (A) wire the renderer preview + selectable input UI, (B) scaffold a sandboxed Tool Proxy, or (C) scaffold provider adapters + secure key storage.

- **Compliance & Data Governance:** implement GDPR/CCPA compliance flows, data residency controls, Data Processing Addenda, and user data export/deletion APIs.
- **Secure Updates & Supply Chain:** signed updates, reproducible builds, SBOM for third-party components, and an automated CI/CD pipeline with security gates.
- **Secrets & Key Recovery:** store API keys in OS-backed secure storage or hardware-backed keystores; provide encrypted backup/escrow and recovery UI.
- **Telemetry & Opt-in:** design minimal, privacy-preserving telemetry (opt-in), with clear opt-out and on/offline modes.
- **Incident Response & Forensics:** comprehensive audit logs, replayable action traces, emergency agent kill-switch, and incident playbooks.
- **Abuse, Safety & Moderation:** rate limits, action approval thresholds, content filtering, and escalation paths for harmful agent behaviors.
- **Enterprise Features & Policy Controls:** MDM/enterprise policies, role-based access, centralized configuration, and logging for compliance needs.
- **Testing & QA:** unit/integration tests for agent gateway, contract tests for tool schemas, fuzzing (DOM inputs, agent payloads), and end-to-end automation tests.
- **Monitoring & Observability:** health endpoints, metrics (resource usage per agent), alerts for anomalous behavior, and dashboards for ops.
- **Backup & Recovery:** encrypted export/import of agent memories and settings, plus snapshot-based rollback for agents and addons.
- **Documentation & Developer DX:** comprehensive API docs, example plugins, security hardening guides, and onboarding tutorials for users and devs.
- **Legal & Policy:** clear transparency about model providers, provenance of persistent memories, and a privacy/security notice for users.
- **Accessibility & Internationalization:** screen-reader support, keyboard navigation, localization workflows, and RTL language support.
- **Performance & Resource Controls:** per-agent CPU/GPU quotas, memory caps, and graceful degradation for large workloads.

Deliverables:
- Compliance checklist and DPA templates.
- CI/CD with signed release pipeline and SBOM generation.
- Test suite (unit + integration + fuzzing) and monitoring dashboards.
- Enterprise policy management UI and backup/restore tools.

