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


## Implementation status (updated 2026-02-21)

- [x] Prototype Agent Gateway: local FastAPI gateway (`agent-gateway/`) with schema validation, supervisor pipeline, and tests.
- [x] Review and refine roadmap with hybrid pipeline sketch (this document now includes a detailed hybrid supervisor design).
- [x] Tab Context Bridge: DOM snapshotter with input-field redaction and a preview UI (`agent-gateway/tab_bridge.py`, `agent-gateway/ui/tab_permission.html`).
- [x] Schema validator with deterministic error tokens: Supervisor now emits structured validation errors with deterministic tokens and feedback.
- [x] Approval workflow: approval API endpoints, minimal approval UI, and approval queue tests.
- [x] Per-tool schema registry: example JSON schemas and runtime enforcement in the supervisor.
- [x] Persisted redaction rules + audit log: redaction rules persisted to `agent-gateway/redaction_rules.json`, audit entries appended to `agent-gateway/audit.log`, and admin-protected endpoints (`X-API-Key`).
- [x] Tests: unit/integration tests covering gateway, supervisor, tab bridge, and approval flows.
- [x] OS-backed user credential storage: passwords stored in the OS keyring when available, with safe fallback to local storage.
- [ ] Browser integration: embed Chromium + sidebar UI and wire the renderer to POST snapshots to the gateway.
- [ ] Provider adapters & secure key storage: OS-backed secure storage / vault integration for provider keys.
- [ ] Harden Tool Proxy: sandbox helper process, capability model, and strict execution limits for tool actions.
- [ ] CI/CD security hardening: SBOM generation, dependency scanning, signed releases.
- [x] CI/CD security hardening: SBOM generation, dependency scanning (CI updated to generate SBOM and run pip-audit). 
- [ ] E2E tests and fuzzing harness: DOM/agent payload fuzzing and full end-to-end automation.

The prototype implementation lives under `agent-gateway/` and includes tests and a README with quickstart instructions.

## Remaining steps — recommended priority

1. Wire the Tab Snapshot Preview into the browser renderer and expose a per-tab permission prompt (high priority).
2. Implement provider adapters and move API keys into an OS keyring or vault; add secrets rotation and scoped permissions (high priority before enabling external providers).
3. Harden the Tool Proxy: create a sandboxed helper process with capability restrictions (file, network) and enforce signed tool contracts (medium-high priority).
4. Replace dev `X-API-Key` with an auth system (local RBAC, operator accounts) and add a UI for audit/event review (medium priority).
5. Expand the OpenAPI/tool contracts and generate client SDKs for plugins and provider adapters (medium priority).
6. Add CI gates: SBOM, dependency scanning, unit+integration tests, and a signed-release workflow (medium priority).
7. Add E2E tests and a fuzzing harness for the Tab Bridge and supervisor (medium priority).
8. UX polish: preview UI with selectable input redaction checkboxes, realtime approval stream (SSE/WebSocket), and consent timeline (low-medium priority).

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

