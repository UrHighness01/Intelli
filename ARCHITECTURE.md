# Intelli Architecture — Overview

Below is a high-level architecture diagram (Mermaid) showing the main components
and data flows for Intelli.

```mermaid
flowchart TD
  subgraph ElectronShell["Electron Browser Shell (browser-shell/)"]
    BrowserUI["Browser UI\n(tabs + nav bar + panels)"]
    PanelSystem["Chrome Panels (360 px)\nbookmarks · history · settings\nclear-data · dev-addons"]
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

    subgraph CoreSubsystems["Core Subsystems"]
      Scheduler["Task Scheduler"]
      Memory["Agent Memory\n(key-value + TTL)"]
      VectorMemory["Vector Memory\n(embedding store)"]
      Webhooks["Webhook Dispatcher\n(retry + HMAC)"]
      Metrics["Prometheus Metrics"]
      Audit["Audit Log\n(append-only JSONL)"]
      Consent["Consent / Context Timeline"]
      Capabilities["Capability Manifests"]
      KeyMgmt["Provider Key Manager\n(Vault / keyring / env)"]
      Failover["Provider Failover"]
    end

    subgraph AgentFeatures["Agent Features"]
      Notifier["Notifier\n(Telegram · Discord · Slack)"]
      Notes["Notes / Knowledge Base\n(local Markdown)"]
      CredStore["Credential Store\n(OS keychain + AES-256-GCM)"]
      A2A["Agent-to-Agent (A2A)\n(persona task routing)"]
      PluginLoader["Plugin Loader\n(pip / zip / GitHub)"]
      Voice["Voice I/O\n(STT + TTS)"]
      Canvas["Canvas Manager\n(structured output)"]
      MCPClient["MCP Client\n(Model Context Protocol)"]
      Personas["Personas"]
      Sessions["Session History"]
      Watcher["Page Diff Watcher"]
      Workspace["Workspace / Skills"]
      Compaction["Context Compaction"]
    end

    subgraph ToolLayer["Tool Layer"]
      BrowserTools["Browser Automation"]
      WebTools["Web Tools\n(fetch / search)"]
      PDFReader["PDF Reader"]
      VideoFrames["Video Frame Analysis\n(ffmpeg + vision)"]
      CodingTools["Coding Tools"]
    end

    subgraph AdminUI["Admin UI (24 pages — /ui/)"]
      UIPages["status · audit · approvals · users\nproviders · schedule · metrics · memory\ncontent-filter · rate-limits · webhooks\ncapabilities · consent · tab_permission\nchat · canvas · personas · mcp\nsessions · analytics · watchers\nsetup · workspace · index"]
    end
  end

  subgraph External["External / Optional"]
    LocalModels["Local Models\n(Ollama / GGML)"]
    CloudProviders["Cloud LLM Providers\n(OpenAI / Anthropic / OpenRouter)"]
    VaultSrv["HashiCorp Vault"]
    SIEM["SIEM / Log Aggregator"]
    NotifChannels["Notification Channels\n(Telegram / Discord / Slack)"]
    MCPServers["MCP Servers"]
  end

  BrowserUI -->|"DOM snapshot (policy-gated)"| TabBridge
  BrowserUI <-->|"panel-visible IPC"| PanelSystem
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
  Supervisor --> VectorMemory
  Router --> Metrics
  Router --> Audit
  Router --> Consent
  Router --> Webhooks
  Router --> Capabilities
  Router --> KeyMgmt
  Router --> AdminUI
  Router --> Notifier
  Router --> Notes
  Router --> CredStore
  Router --> A2A
  Router --> PluginLoader
  Router --> Sessions
  Router --> Watcher
  Router --> Canvas
  Router --> MCPClient
  Router --> Voice

  Supervisor --> ToolLayer

  ToolProxy -->|"sandboxed exec"| BrowserUI
  Supervisor --> LocalModels
  Supervisor --> CloudProviders
  Failover --> LocalModels
  Failover --> CloudProviders
  KeyMgmt --> VaultSrv
  Audit -->|"optional ship"| SIEM
  Notifier --> NotifChannels
  MCPClient --> MCPServers

  classDef infra fill:#c8d6e5,stroke:#2c3e50,stroke-width:1px;
  classDef security fill:#ffd3b4,stroke:#e07b39,stroke-width:1px;
  classDef electron fill:#d0f0c0,stroke:#27ae60,stroke-width:1px;
  classDef agent fill:#e8daff,stroke:#7c4dff,stroke-width:1px;
  class CloudProviders,LocalModels,VaultSrv,SIEM,NotifChannels,MCPServers infra;
  class Auth,RateLimit,ContentFilter,KillSwitch,Approvals security;
  class BrowserUI,TabBridge,Splash,MainProc electron;
  class Notifier,Notes,CredStore,A2A,PluginLoader,Voice,Canvas,MCPClient,Personas,Sessions,Watcher,Workspace,Compaction agent;
```

---

## Component Summary

| Component | Location | Technology | Role |
|---|---|---|---|
| Electron Main Process | `browser-shell/main.js` | Electron 29 + Node.js | Spawns/kills gateway, manages windows; `panel-visible` + `toggle-chrome-devtools` IPC |
| Browser UI | `browser-shell/src/browser.{html,js,css}` | Chromium renderer | Multi-tab browser chrome + 5 overlay panels (bookmarks, history, settings, clear-data, dev-addons) |
| Chrome Panel System | `browser-shell/src/browser.{html,js,css}` | DOM overlay (360 px) | Bookmark star, zoom indicator, ⋮ menu, per-panel data loaders |
| Tab Context Bridge | `browser-shell/preload.js` | contextBridge API | Isolates renderer from Node.js |
| Agent Gateway | `agent-gateway/app.py` | FastAPI + uvicorn | Central HTTP API (60+ endpoints) |
| Supervisor | `agent-gateway/supervisor.py` | Python | Schema validation + manifest-driven approval routing |
| Tool Proxy | `agent-gateway/sandbox/proxy.py` | Python subprocess | Sandboxed action execution |
| Auth | `agent-gateway/auth.py` | PBKDF2 + Bearer | Login, tokens, RBAC, user management |
| Audit Log | `agent-gateway/audit.log` | Append-only JSONL | Immutable event trail; optional AES-256-GCM encryption |
| Content Filter | `agent-gateway/content_filter.py` | Regex + literal | Pre-call deny rules |
| Rate Limiter | `agent-gateway/rate_limit.py` | Sliding window | Per-IP/user request caps |
| Approval Queue | `agent-gateway/supervisor.py` | Async queue + SSE | Human-in-the-loop sign-off |
| Scheduler | `agent-gateway/scheduler.py` | Background daemon | Recurring tool-call tasks |
| Agent Memory | `agent-gateway/agent_memory.py` | JSON + TTL | Per-agent key-value store |
| Vector Memory | `agent-gateway/memory_store.py` | Embedding store | Semantic long-term recall |
| Webhooks | `agent-gateway/webhooks.py` | HTTPX + HMAC retry | Push events to external URLs |
| Metrics | `agent-gateway/metrics.py` | Prometheus client | Per-tool counters + histograms |
| Provider Keys | `agent-gateway/providers/key_rotation.py` | Vault / keyring / env | LLM credential lifecycle |
| Provider Failover | `agent-gateway/failover.py` | Python | Automatic fallback across providers |
| Notifier | `agent-gateway/notifier.py` | Python | Outbound push to Telegram / Discord / Slack |
| Notes / Knowledge Base | `agent-gateway/notes.py` | Local Markdown | Search and manage local knowledge files |
| Credential Store | `agent-gateway/credential_store.py` | OS keychain + AES-256-GCM | Named secret management |
| Agent-to-Agent (A2A) | `agent-gateway/a2a.py` | Python | Route tasks between personas; async task queue |
| Plugin Loader | `agent-gateway/plugin_loader.py` | pip / zip / GitHub | Dynamic plugin install and tool registration |
| Voice I/O | `agent-gateway/voice.py` | STT + TTS | Speech input and output |
| Canvas | `agent-gateway/canvas_manager.py` | Python | Structured multi-block output |
| MCP Client | `agent-gateway/mcp_client.py` | MCP protocol | External tool and resource integration |
| Personas | `agent-gateway/personas.py` | Python | Named agent personas with system prompts |
| Session History | `agent-gateway/sessions.py` | Python | Per-session conversation storage |
| Page Diff Watcher | `agent-gateway/watcher.py` | Python | Monitor URLs for content changes |
| Workspace / Skills | `agent-gateway/workspace_manager.py` | Python | Skill manifest management |
| Context Compaction | `agent-gateway/compaction.py` | Python | Auto-summarise long context windows |
| Browser Tools | `agent-gateway/tools/browser_tools.py` | Playwright / CDP | Headless browser automation |
| Web Tools | `agent-gateway/tools/web_tools.py` | Python | Web fetch, search, summarise |
| PDF Reader | `agent-gateway/tools/pdf_reader.py` | Python | Text + structure extraction from PDF |
| Video Frame Analysis | `agent-gateway/tools/video_frames.py` | ffmpeg + vision | Frame extraction + vision model description |
| Coding Tools | `agent-gateway/tools/coding_tools.py` | Python | Code generation, execution, linting |
| Admin UI | `agent-gateway/ui/` | Vanilla JS (24 pages) | Full-featured admin console |
| CLI | `agent-gateway/gateway_ctl.py` | argparse | 20 subcommands covering all APIs |
| OpenAPI spec | `agent-gateway/openapi.yaml` | OpenAPI 3.0.3 | Full endpoint documentation |
| Log Shipper | `scripts/log_shipper.py` | Python | NDJSON audit log forwarding to SIEM |

---

## Chrome API Shim Bridge

Intelli implements a retro-engineered Chrome Extension Runtime layer that intercepts
broken or missing `chrome.*` APIs in Electron's built-in extension system and
routes them to native Intelli equivalents.

### Problem

Electron's MV3 extension support has several gaps:
- `chrome.tabs.create()` opens a standalone `BrowserWindow` instead of an Intelli tab
- `chrome.contextMenus.getAll()` always returns `[]` (menus stored in C++ registry, invisible from JS)
- `chrome.windows.create()` opens standalone windows
- `chrome.notifications.create()` may silently fail
- No way to read registered menus from JS in the SW context

### Solution — Two-way CDP Binding Bridge

```mermaid
sequenceDiagram
    participant SW as Extension SW (e.g. MaxAI)
    participant Shim as chrome-api-shim.js<br/>(injected via CDP Runtime.evaluate)
    participant Binding as intelliApi() binding<br/>(Runtime.addBinding)
    participant Main as main.js _handleChromeApiCall
    participant UI as Intelli Tab Bar / UI

    SW->>Shim: chrome.tabs.create({url:'https://maxai.co'})
    Shim->>Binding: intelliApi(JSON.stringify({id, api:'tabs.create', args}))
    Binding->>Main: Runtime.bindingCalled event
    Main->>UI: createTab('https://maxai.co')
    Main->>Binding: Runtime.evaluate → self.intelliApiReply(id, result)
    Binding->>Shim: resolve(result)
    Shim->>SW: callback / Promise resolved

    SW->>Shim: chrome.contextMenus.create({id, title})
    Shim->>Binding: intelliApi({api:'contextMenus.create',...})
    Main->>Main: _extContextMenuCache.set(extId, item)
    Main->>Binding: intelliApiReply(id, itemId)
```

### Shim Injection Flow

```mermaid
flowchart TD
    Start["App Startup"] --> CDP["_initServiceWorkerCDP()"]
    CDP --> Attach["Target.attachToTarget\n(flatten: true)"]
    Attach --> Bind["Runtime.addBinding\n(name: 'intelliApi')"]
    Bind --> Inject["Runtime.evaluate\n(_CHROME_API_SHIM_SRC)"]
    Inject --> Ready["Shim active in SW scope"]

    Ready --> Call["SW calls chrome.*"]
    Call --> Override{"Is API\nshimmed?"}
    Override -- Yes --> Bridge["intelliApi() → bindingCalled\n→ _handleChromeApiCall"]
    Override -- No --> Native["Falls through to\nElectron native (may break)"]

    Bridge --> Dispatch["Route to Intelli:\ncreateTab / cache update /\nElectron Notification / ..."]
    Dispatch --> Reply["intelliApiReply(id, result)\nback to SW"]
```

### Shimmed APIs

The full `_SHIMMED_APIS` set defined in `main.js` (source of truth for the audit scanner).
Any API in this set is considered handled — by Electron's native extension support,
by an Intelli shim, or by the `browser-window-created` / `setWindowOpenHandler` routing layer.

| Namespace | Shimmed methods / events |
|---|---|
| `runtime` | `sendMessage`, `connect`, `getURL`, `getManifest`, `id`, `lastError`, `reload`, `setUninstallURL`, `requestUpdateCheck`, `getPlatformInfo`, `getPackageDirectoryEntry`, `openOptionsPage`*, `onMessage`, `onConnect`, `onInstalled`, `onStartup`, `onSuspend` |
| `storage.local` | `get`, `set`, `remove`, `clear` |
| `storage.sync` | `get`, `set`, `remove`, `clear` |
| `storage` | `local`, `sync`, `onChanged` |
| `tabs` | `query`, `get`, `create`, `update`, `remove`, `duplicate`, `sendMessage`, `onUpdated`, `onActivated`, `onRemoved`, `captureVisibleTab`, `executeScript`, `insertCSS`, `move`, `reload` |
| `windows` | `getAll`, `getCurrent`, `get`, `create`, `update`, `remove`, `onFocusChanged`, `onCreated`, `onRemoved` |
| `alarms` | `create`, `get`, `getAll`, `clear`, `clearAll`, `onAlarm` |
| `contextMenus` | `create`, `update`, `remove`, `removeAll`, `onClicked` |
| `notifications` | `create`, `update`, `clear`, `getAll`, `onClicked`, `onClosed`, `onButtonClicked` |
| `scripting` | `executeScript`, `insertCSS`, `removeCSS`, `registerContentScripts`, `unregisterContentScripts`, `getRegisteredContentScripts` |
| `declarativeNetRequest` | `updateDynamicRules`, `getDynamicRules`, `updateStaticRules`, `getSessionRules`, `updateSessionRules` |
| `webRequest` | `onBeforeRequest`, `onBeforeSendHeaders`, `onHeadersReceived`, `onResponseStarted`, `onCompleted`, `onErrorOccurred` |
| `identity` | `getAuthToken`, `launchWebAuthFlow`, `removeCachedAuthToken` |
| `cookies` | `get`, `getAll`, `set`, `remove`, `onChanged` |
| `history` | `addUrl`, `deleteUrl`, `search`, `getVisits`, `deleteAll`, `deleteRange` |
| `bookmarks` | `get`, `search`, `create`, `remove`, `update`, `move`, `getTree`, `getRecent` |
| `action` / `browserAction` | `setIcon`, `setTitle`, `setBadgeText`, `setBadgeBackgroundColor`, `getTitle`, `getBadgeText`, `enable`, `disable`, `setPopup`, `openPopup`, `onClicked`† |
| `downloads`* | `download`, `search`, `pause`, `resume`, `cancel`, `erase`, `open`, `show`, `showDefaultFolder`, `drag`, `acceptDanger`, `setShelfEnabled`, `onCreated`, `onChanged`, `onErased` |
| `sidePanel` | `open`, `setOptions`, `getOptions`, `setPanelBehavior` |
| `i18n` | `getMessage`, `getUILanguage`, `detectLanguage` |
| `permissions` | `contains`, `request`, `remove`, `getAll` |
| `extension` | `getURL`, `getBackgroundPage`, `getViews` |
| `management` | `getAll`, `get`, `getSelf` |
| `offscreen` | `createDocument`, `closeDocument`, `hasDocument` |
| `commands` | `getAll`, `onCommand` |
| `system.memory` | `getInfo` |
| `system.cpu` | `getInfo` |
| `system.storage` | `getInfo` |

> \* `runtime.openOptionsPage` and `downloads.*` are handled by Electron 35 natively.
> Electron's session layer intercepts `chrome.downloads.download()` and routes it
> through the OS download manager. `openOptionsPage()` opens the manifest's options
> page; a `browser-window-created` listener in `main.js` catches the resulting
> window and redirects it to Intelli's `createTab()`.
>
> † `action.onClicked` fires when the user clicks the extension toolbar icon.
> Electron dispatches this natively for loaded extensions.

### Extension API Audit Agent

An always-on background agent that statically analyzes installed Chrome extension
JS files and surfaces any `chrome.*` API calls that Intelli has **not** shimmed.
This makes it easy to prioritize which APIs to implement next.

#### How it works

1. **Startup load** — `_initExtApiAuditAgent()` (called immediately after
   `createMainWindow()`) reads any previously persisted entries from
   `<userData>/ext-api-audit.jsonl` into the in-memory `_apiAuditEntries` array.
2. **Static scan** — 12 seconds after startup (to avoid competing with tab
   rendering), a `worker_threads` Worker walks every installed extension's JS files
   with the regex `/chrome\.([a-zA-Z]+(?:\.[a-zA-Z]+)+)\s*(?:\(|;|\.)/g` and logs
   any captured API name that is absent from `_SHIMMED_APIS`.
3. **Deduplication** — `_logUnknownApi` skips any `(extId, api)` pair that already
   exists in `_apiAuditEntries`, so re-scans on subsequent launches only record
   newly discovered APIs.
4. **Persistence** — each new finding is `appendFile`'d to
   `<userData>/ext-api-audit.jsonl` in JSONL format
   `{ts, extId, extName, api, source}`.
5. **Live push** — findings are pushed to the renderer via
   `mainWin.webContents.send('ext-api-audit-new', entry)`.
6. **Panel** — "Extension API Audit" side panel accessible from
   **☰ → Extensions / Addons → Extension API Audit**; shows a grouped-by-extension
   table with Refresh and Clear buttons.

> **Note — no `fs.watch`**: watching the extensions directory was explicitly removed
> because Chromium's own write activity inside `chrome-extensions/` caused an
> inotify storm that saturated file-descriptor limits. Re-scan on extension install
> is planned via the `ext-load-unpacked` / `ext-load-crx` IPC handlers instead.

```mermaid
flowchart LR
    subgraph Main["main.js (Main Process)"]
        Init["_initExtApiAuditAgent()\n(called after createMainWindow)"]
        Worker["_startScanWorker()\nworker_threads — 12 s delay"]
        Log["_logUnknownApi()\ndedup + appendFile + IPC push"]
        JSONL["ext-api-audit.jsonl\n(userData — JSONL)"]
        IPC["ext-api-audit-new\nIPC event → mainWin"]
        IPCHandle["IPC handlers\nget-ext-api-audit\nclear-ext-api-audit"]
    end
    subgraph Renderer["browser.js (Renderer)"]
        Panel["panel-ext-audit\n(side panel)"]
        Loader["loadExtApiAuditPanel()"]
        Live["onExtApiAuditNew(cb)\nreal-time push"]
    end

    Init -->|"load JSONL"| JSONL
    Init --> Worker
    Worker -->|"regex hits"| Log
    Log --> JSONL
    Log --> IPC
    IPC --> Live
    Live --> Panel
    IPCHandle -->|"get"| Loader
    Loader --> Panel
```

#### Crash-Guard Sentinel (related)

To prevent Chromium's Service Worker LevelDB from becoming corrupted across
restarts, Intelli writes a sentinel file `<userData>/.intelli-clean-exit`
on every clean `before-quit`. At next startup, if the sentinel is **absent**
(previous session crashed), the `Service Worker/` directory is wiped before
any extension loads. If it is **present**, the wipe is skipped.
This eliminates `IO error: .../LOCK` and `Status code: 2` extension errors.

#### Discovered Missing APIs (as of 2026-02-27) — RESOLVED

The following APIs were found by the static scanner and have since been addressed:

| Extension | API | Resolution | Status |
|---|---|---|---|
| MaxAI | `chrome.downloads.download` | Added to `_SHIMMED_APIS` — Electron 35 routes `chrome.downloads.*` through its native session download system (will-download event + OS download manager). Works out of the box. | ✅ Resolved |
| LLM Tweet Comment Generator | `chrome.runtime.openOptionsPage` | Added to `_SHIMMED_APIS` + `browser-window-created` listener routes any window opened by the API into Intelli's `createTab()` instead of a standalone `BrowserWindow`. | ✅ Resolved |
| LLM Tweet Comment Generator | `chrome.runtime.onMessage.addListener` | **False positive.** The 3-segment chain `onMessage.addListener` was matched by the regex but the parent event object `runtime.onMessage` IS shimmed. Fixed by the `resolveApi()` normalization in the worker. | ✅ False positive — suppressed |
| LLM Tweet Comment Generator | `chrome.action.onClicked.addListener` | **False positive** for the same reason. `action.onClicked` added to `_SHIMMED_APIS`; `resolveApi()` short-circuits the 3-segment chain. | ✅ False positive — suppressed |

> **Scanner fix**: the `resolveApi()` function inside `_SCAN_WORKER_SRC` now checks
> the 2-segment prefix of any 3-segment match before reporting it as unimplemented.
> This eliminates entire categories of false positives from `.addListener`,
> `.removeListener`, `.hasListener`, and similar event-object method calls.

**Current scan result: 0 unimplemented APIs across all installed extensions.**

#### Files

| File | Symbols |
|---|---|
| `browser-shell/main.js` | `_SHIMMED_APIS`, `EXT_API_AUDIT_LOG`, `_apiAuditEntries`, `_SCAN_WORKER_SRC`, `_startScanWorker`, `_logUnknownApi`, `_initExtApiAuditAgent`, IPC handlers (`get-ext-api-audit`, `clear-ext-api-audit`), menu entry, before-quit sentinel write |
| `browser-shell/preload.js` | `extApiAuditGet`, `extApiAuditClear`, `onExtApiAuditNew` |
| `browser-shell/src/browser.html` | `#panel-ext-audit` side panel |
| `browser-shell/src/browser.js` | `loadExtApiAuditPanel()`, `_renderAuditEntries()` |

---

## Notes

- The **Tab Context Bridge** serializes the active tab to a structured snapshot
  and enforces per-site permissions and redaction rules before any context leaves
  the renderer process.
- The **Chrome Panel System** renders five `position:fixed` overlay panels (bookmarks,
  history, settings, clear-data, dev-addons) inside the chrome renderer. When any panel
  is open, `browser.js` calls `electronAPI.setPanelVisible(true)` which fires the
  `panel-visible` IPC handler in `main.js`; `tabBounds()` subtracts `PANEL_WIDTH = 360 px`
  from the active BrowserView so the DOM panel is never hidden behind it.
- The **Agent Gateway** is the local HTTP/IPC endpoint; on desktop it is spawned
  by Electron and its lifecycle is tied to the browser window.
- The **Supervisor** performs schema validation, escaping, sanitization, and
  enforces execution policies (approval thresholds, rate limits, RBAC, content
  filter) on every incoming tool call. Manifest-driven routing overrides heuristic
  risk scoring when a capability manifest is present for the tool.
- The **Sandbox** runs in a subprocess with a strict action whitelist and optional
  Docker isolation with `--cap-drop ALL`, seccomp profile, read-only filesystem,
  and CPU/memory quotas.
- All persistent data (agent memory, consent log, audit log, revoked tokens, notes,
  credentials, session history) is stored locally. Vault is used in production for
  API key storage.
- Transport: gateway listens on `127.0.0.1:8080` by default; put nginx or Caddy
  in front for TLS in network-facing deployments.
- The **Plugin Loader** discovers a `plugins/` directory and any installed pip packages
  that expose an `intelli_plugin.json` manifest, dynamically registering their tools
  at startup.
