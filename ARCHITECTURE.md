# Intelli Architecture — Overview

Below is a high-level architecture diagram (Mermaid) showing the main components
and data flows for Intelli.

```mermaid
flowchart TD
  subgraph ElectronShell["Electron Browser Shell (browser-shell/)"]
    BrowserUI["Browser UI\n(tabs + nav bar)"]
    TabBridge["Tab Context Bridge\n(browser.js + preload)"]
    Splash["Splash Screen\n(splash.html)"]
    MainProc["Electron Main Process\n(main.js)"]
  end

  subgraph AgentGateway["Agent Gateway (agent-gateway/ — FastAPI :8080)"]
    Router["HTTP Router\n(app.py)"]
    Supervisor["Supervisor & Validator"]
    ToolProxy["Tool Proxy (sandbox worker)"]

    subgraph Security["Security Middleware"]
      Auth["Bearer Token Auth\n& RBAC"]
      RateLimit["Rate Limiter"]
      ContentFilter["Content Filter\n(literal + regex)"]
      KillSwitch["Kill-Switch"]
      Approvals["Approval Queue\n(SSE stream)"]
    end

    subgraph Subsystems["Gateway Subsystems"]
      Scheduler["Task Scheduler"]
      Memory["Agent Memory\n(key-value + TTL)"]
      Webhooks["Webhook Dispatcher\n(retry + HMAC)"]
      Metrics["Prometheus Metrics"]
      Audit["Audit Log\n(append-only JSONL)"]
      Consent["Consent / Context Timeline"]
      Capabilities["Capability Manifests"]
      KeyMgmt["Provider Key Manager\n(Vault / keyring / env)"]
    end

    subgraph AdminUI["Admin UI (15 pages — /ui/)"]
      UIPages["status · audit · approvals\nusers · providers · schedule\nmetrics · memory · content-filter\nrate-limits · webhooks · capabilities\nconsent · tab_permission · index"]
    end
  end

  subgraph External["External / Optional"]
    LocalModels["Local Models\n(Ollama / GGML)"]
    CloudProviders["Cloud LLM Providers\n(OpenAI / Anthropic / OpenRouter)"]
    VaultSrv["HashiCorp Vault"]
    SIEM["SIEM / Log Aggregator"]
  end

  BrowserUI -->|"DOM snapshot (policy-gated)"| TabBridge
  TabBridge -->|"structured context"| MainProc
  MainProc -->|"spawn / IPC"| Router
  Splash --> MainProc

  Router --> Auth
  Auth --> RateLimit
  RateLimit --> ContentFilter
  ContentFilter --> KillSwitch
  KillSwitch --> Approvals
  Approvals --> Supervisor
  Supervisor --> ToolProxy

  Supervisor --> Scheduler
  Supervisor --> Memory
  Router --> Metrics
  Router --> Audit
  Router --> Consent
  Router --> Webhooks
  Router --> Capabilities
  Router --> KeyMgmt
  Router --> AdminUI

  ToolProxy -->|"sandboxed exec"| BrowserUI
  Supervisor --> LocalModels
  Supervisor --> CloudProviders
  KeyMgmt --> VaultSrv
  Audit -->|"optional ship"| SIEM

  classDef infra fill:#c8d6e5,stroke:#2c3e50,stroke-width:1px;
  classDef security fill:#ffd3b4,stroke:#e07b39,stroke-width:1px;
  classDef electron fill:#d0f0c0,stroke:#27ae60,stroke-width:1px;
  class CloudProviders,LocalModels,VaultSrv,SIEM infra;
  class Auth,RateLimit,ContentFilter,KillSwitch,Approvals security;
  class BrowserUI,TabBridge,Splash,MainProc electron;
```

---

## Component Summary

| Component | Location | Technology | Role |
|---|---|---|---|
| Electron Main Process | `browser-shell/main.js` | Electron 29 + Node.js | Spawns/kills gateway, manages windows |
| Browser UI | `browser-shell/browser.{html,js,css}` | Chromium renderer | Multi-tab browser chrome |
| Tab Context Bridge | `browser-shell/preload.js` | contextBridge API | Isolates renderer from Node.js |
| Agent Gateway | `agent-gateway/app.py` | FastAPI + uvicorn | Central HTTP API |
| Supervisor | `agent-gateway/supervisor.py` | Python | Schema validation + routing |
| Tool Proxy | `agent-gateway/tool_proxy.py` | Python subprocess | Sandboxed action execution |
| Auth | `agent-gateway/auth.py` | PBKDF2 + Bearer | Login, tokens, RBAC |
| Audit Log | `agent-gateway/audit_log.py` | Append-only JSONL | Immutable event trail |
| Content Filter | `agent-gateway/content_filter.py` | Regex + literal | Pre-call deny rules |
| Rate Limiter | `agent-gateway/rate_limit.py` | Sliding window | Per-IP/user request caps |
| Approval Queue | `agent-gateway/approvals.py` | Async queue + SSE | Human-in-the-loop sign-off |
| Scheduler | `agent-gateway/scheduler.py` | APScheduler | Recurring tool-call tasks |
| Agent Memory | `agent-gateway/agent_memory.py` | JSON + TTL | Per-agent key-value store |
| Webhooks | `agent-gateway/webhooks.py` | HTTPX + HMAC retry | Push events to external URLs |
| Metrics | `agent-gateway/metrics.py` | Prometheus client | Per-tool counters + histograms |
| Provider Keys | `agent-gateway/key_rotation.py` | Vault / keyring / env | LLM credential lifecycle |
| Admin UI | `agent-gateway/ui/` | Vanilla JS (15 pages) | Full-featured admin console |
| CLI | `agent-gateway/gateway_ctl.py` | Click | 19 subcommands covering all APIs |
| OpenAPI spec | `agent-gateway/openapi.yaml` | OpenAPI 3.0.3 | 20 named tags, full endpoint docs |

---

## Notes

- The **Tab Context Bridge** serializes the active tab to a structured snapshot
  and enforces per-site permissions and redaction rules before any context leaves
  the renderer process.
- The **Agent Gateway** is the local HTTP/IPC endpoint; on desktop it is spawned
  by Electron and its lifecycle is tied to the browser window.
- The **Supervisor** performs schema validation, escaping, sanitization, and
  enforces execution policies (approval thresholds, rate limits, RBAC, content
  filter) on every incoming tool call.
- The **Tool Proxy** runs in a subprocess with a strict action whitelist; no
  arbitrary code execution is supported.
- All persistent data (agent memory, consent log, audit log, revoked tokens) is
  stored locally. Vault is used in production for API key storage.
- Transport: gateway listens on `127.0.0.1:8080` by default; put nginx or Caddy
  in front for TLS in network-facing deployments.
