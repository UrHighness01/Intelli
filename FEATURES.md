# Intelli Feature Roadmap
> Ideas drawn from OpenClaw (https://github.com/openclaw/openclaw) and adapted for the  
> **Intelli AI-Native Browser** (Electron + Python FastAPI gateway).  
> Each section explains what OpenClaw does, how we adapt it, and rough implementation scope.

---

## Table of Contents
1. [Agent Browser Automation](#1-agent-browser-automation)
2. [Persistent Vector Memory](#2-persistent-vector-memory)
3. [Canvas — Live Agent UI](#3-canvas--live-agent-ui)
4. [Sub-Agents & Parallel Tasks](#4-sub-agents--parallel-tasks)
5. [Skill Ecosystem & ClawHub-style Registry](#5-skill-ecosystem--clawhub-style-registry)
6. [Session Compaction (Context Window Management)](#6-session-compaction-context-window-management)
7. [Voice I/O — TTS + Whisper STT](#7-voice-io--tts--whisper-stt)
8. [Model Failover & Auth Profile Rotation](#8-model-failover--auth-profile-rotation)
9. [Web Tools — Search, Fetch, Readability](#9-web-tools--search-fetch-readability)
10. [Image & Vision Upload](#10-image--vision-upload)
11. [Cron / Scheduled Agent Tasks](#11-cron--scheduled-agent-tasks)
12. [Tool Approval Flow](#12-tool-approval-flow)
13. [Coding Agent Mode](#13-coding-agent-mode)
14. [Agent Personas & Identity](#14-agent-personas--identity)
15. [MCP Server Integration](#15-mcp-server-integration)
16. [Skill Creator (AI Self-Extending)](#16-skill-creator-ai-self-extending)
17. [Page Diff Watcher](#17-page-diff-watcher)
18. [PDF / Document Analysis](#18-pdf--document-analysis)
19. [Knowledge Base Connectors](#19-knowledge-base-connectors)
20. [Secure Credential Store](#20-secure-credential-store)
21. [Extension / Plugin System](#21-extension--plugin-system)
22. [Notification & Webhook Push](#22-notification--webhook-push)
23. [Session History & Repair](#23-session-history--repair)
24. [Sandbox Code Execution](#24-sandbox-code-execution)
25. [Navigation Guard & Security Layer](#25-navigation-guard--security-layer)
26. [A2A — Agent-to-Agent Sessions](#26-a2a--agent-to-agent-sessions)
27. [Video Frame Analysis](#27-video-frame-analysis)
28. [Usage Analytics & Observability](#28-usage-analytics--observability)

---

## 1. Agent Browser Automation

**What OpenClaw does:**  
`src/browser/` — Full Playwright automation: CDP bridge, tab control, click/type/scroll/fill, screenshot, file downloads, form interactions, navigation guard, multiple Chrome profiles. The agent drives the browser like a human.

**What Intelli can do:**  
Intelli already renders pages in `BrowserView` — we can expose IPC commands from the agent gateway to control the active tab. Instead of Playwright (which runs a separate browser), we drive the *actual* Electron tab the user is looking at via IPC messages.

**Implementation ideas:**
- `agent-gateway` exposes tool: `browser.click(selector)`, `browser.type(selector, text)`, `browser.scroll(px)`, `browser.navigate(url)`, `browser.screenshot()`, `browser.eval(js)`
- `main.js` forwards these commands to the active BrowserView via `executeJavaScript` + CDP
- Agent can see the live DOM (tab snapshot) and interact with it — enables fully autonomous page scripting
- **"Agent takes the wheel"** mode: user delegates a task ("fill out this form", "scrape this table") and proceeds hands-free
- Screenshot tool returns base64 PNG, injected as image message for vision models
- Support for multiple autonomous profiles (research vs. shopping vs. work)

**Priority:** 🔴 High — this is the single biggest differentiator of a browser over a chat app.

---

## 2. Persistent Vector Memory

**What OpenClaw does:**  
`src/memory/` — Full vector memory system: embeddings via OpenAI/Gemini/Voyage/Mistral, SQLite-vec storage, hybrid search (semantic + keyword), MMR deduplication, temporal decay, batch operations, query expansion, QMD scoped semantic queries. The agent remembers conversations, web pages, and facts across sessions.

**What Intelli can do:**  
Store browsing history, chat summaries, bookmarked pages, and important facts in a local vector DB that the agent searches before every reply.

**Implementation ideas:**
- Add `chromadb` or `sqlite-vec` (pure Python, no server needed) to requirements
- `workspace_manager.py` → extend with `memory_store.py` for CRUD + vector search
- On every page visit, auto-embed the Readability extract and store with URL + timestamp
- Agent auto-injects "relevant memories" into system prompt before replying
- `GET /memory/search?q=...` — search past pages/chats by semantic query
- `POST /memory/add` — manually pin a fact or highlight
- `DELETE /memory/{id}` — forget a memory
- UI: memory panel in sidebar showing recent + pinned memories, search box
- Temporal decay: older memories contribute less weight to ranking
- MMR (Maximal Marginal Relevance): avoid injecting redundant near-duplicate memories

**Priority:** 🔴 High — gives the agent persistent context, makes it dramatically more useful over time.

---

## 3. Canvas — Live Agent UI

**What OpenClaw does:**  
`src/canvas-host/` + `skills/canvas/` — The agent can render a live interactive canvas: charts, tables, maps, custom HTML/JS UIs. A2UI (agent-to-UI) protocol streams DOM updates directly to the canvas frame. Users can interact with the canvas and the agent reacts.

**What Intelli can do:**  
Open a dedicated `BrowserView` canvas panel alongside the main tab. The agent writes HTML/CSS/JS into it to visualize data, render charts, show interactive forms, or present results in rich format.

**Implementation ideas:**
- New tab type in `main.js`: "Canvas tab" pinned to the right side panel
- Agent tool: `canvas.render(html)` — POST `{ html }` to `POST /canvas/render`; gateway pushes via SSE to canvas.html page
- `agent-gateway/ui/canvas.html` — full-screen iframe that subscribes to SSE and updates `innerHTML`
- Pre-built canvas skills: `chart(data, type)`, `table(rows, columns)`, `markdown_doc(md)`, `code_playground(lang, code)`
- Agent can also *read* from the canvas (user interactions posted back as tool results)
- Keyboard shortcut to toggle canvas panel (e.g. `Ctrl+K`)
- Canvas snapshots: save canvas state as PNG or export as HTML file

**Priority:** 🟡 Medium — high UX value, especially for data work and research.

---

## 4. Sub-Agents & Parallel Tasks

**What OpenClaw does:**  
`src/agents/openclaw-tools.subagents.*` — Spawn child agent sessions with their own context, model, and tools. Parent agent delegates subtasks in parallel, collects results, and synthesizes. Depth limits prevent runaway recursion.

**What Intelli can do:**  
When a task is complex ("research 5 competitor sites and summarize each"), the orchestrator agent spawns one sub-agent per site, runs them in parallel Python asyncio tasks, and merges results.

**Implementation ideas:**
- `POST /agent/spawn` — creates a sub-agent session with isolated message history and specific instructions
- `GET /agent/{session_id}/status` — poll for completion
- Sub-agents share the same LLM provider pool but have their own rate-limit bucket
- Parent agent gets a `spawn_agent(task, context)` tool in its tool catalog
- Depth limit (max 3) to prevent runaway chains
- Sub-agent results streamed back to the main chat UI as collapsible "agent report" bubbles
- UI shows a live sub-agent panel listing active tasks, progress, and outputs

**Priority:** 🟡 Medium — powerful for research and automation workflows.

---

## 5. Skill Ecosystem & ClawHub-style Registry

**What OpenClaw does:**  
`skills/` — 60+ bundled skills (Obsidian, Notion, Spotify, GitHub, Slack, Discord, Apple Notes, Weather, 1Password, Camera, Canvas, PDF, Video Frames, Summarize, Coding Agent, Trello…). Skills are Markdown + YAML config + optional JS/Python. Community publishes to ClawHub.

**What Intelli can do:**  
The current addon system injects JS into pages. Skills extend *the agent's capabilities*, not just pages. A skill is a bundle: a system-prompt fragment + optional Python tool + optional JS addon.

**Implementation ideas:**
- Skill manifest: `skills/{slug}/SKILL.md` (already in workspace_manager.py) + `skill.json` (tool definitions, required env vars)
- Built-in starter skills:
  - `page-summarize` — summarize any active tab with one click
  - `page-qa` — ask questions about the current page
  - `github-pr-review` — review a GitHub PR page automatically
  - `form-autocomplete` — fill detected forms using profile data
  - `price-tracker` — watch product pages for price changes
  - `readability` — clean-reader view extraction
  - `pdf-reader` — analyze PDF pages the browser has open
  - `note-to-obsidian` — clip page content to Obsidian vault
  - `web-search` — DuckDuckGo / SearXNG search
  - `weather` — current weather for detected location
  - `translate` — translate selected text via LLM
- `workspace.html` skill manager: browse, install, enable/disable, edit skills
- Skill install from URL or GitHub (similar to `skills-install.ts`)
- Skill versioning: `version` field in SKILL.md frontmatter

**Priority:** 🔴 High — skills dramatically expand what users can do without coding.

---

## 6. Session Compaction (Context Window Management)

**What OpenClaw does:**  
`src/agents/compaction.ts` — When the conversation approaches the model's context limit, the agent summarizes older turns into a compact block, preserving essential facts. Retry logic handles compaction failures. Token counting per provider.

**What Intelli can do:**  
Automatically summarize old chat history when approaching the model's token limit, without the user losing continuity.

**Implementation ideas:**
- `compaction.py` — track token count per message using `tiktoken`; when total exceeds 80% of model limit, trigger compaction
- Compaction: POST a summary request with old messages to the LLM, replace them with `[SUMMARY: ...]` system block
- Expose `GET /chat/tokens` — current conversation token usage + limit
- Token bar in chat.html already exists; hook it to the compaction trigger
- Let user manually trigger compaction with a "🗜 Compact history" button
- Compaction events logged to audit trail

**Priority:** 🟡 Medium — important for long sessions; prevents silent truncation errors.

---

## 7. Voice I/O — TTS + Whisper STT

**What OpenClaw does:**  
`skills/openai-whisper/` + `skills/sherpa-onnx-tts/` + `src/agents/tools/tts-tool.ts` — Speech-to-text input via OpenAI Whisper (API or local). Text-to-speech output via OpenAI TTS or local Sherpa-ONNX (offline). Supports macOS/iOS/Android voices.

**What Intelli can do:**  
Voice input via mic button in the chat UI (Whisper transcription) and voice output (agent reads responses aloud). Useful for hands-free browsing assistance.

**Implementation ideas:**
- Mic button in chat.html: records audio via `MediaRecorder`, sends WAV to `POST /voice/transcribe`
- `whisper_adapter.py` — calls `openai.audio.transcriptions.create` or local `whisper.cpp` subprocess
- TTS: `POST /voice/speak` with `{ text, voice }` → returns audio stream, played via Web Audio API
- Offline TTS: integrate `pyttsx3` (cross-platform) or `edge-tts` (Microsoft neural voices, free)
- TTS streamed: speak tokens as they arrive (streaming TTS like ElevenLabs)
- Voice settings panel: choose voice, speed, auto-speak toggle
- "Hands-free mode": voice input + auto-spoken responses

**Priority:** 🟡 Medium — accessibility + hands-free workflows; higher value on macOS.

---

## 8. Model Failover & Auth Profile Rotation

**What OpenClaw does:**  
`src/agents/model-fallback.ts` + `src/agents/auth-profiles.ts` — Maintains a priority list of (provider, model, API-key-profile) tuples. On rate-limit or error, auto-rotates to the next profile. Cooldown tracking per profile. Supports multiple API keys per provider.

**What Intelli can do:**  
Instead of failing when OpenAI rate-limits, automatically retry with Anthropic or Ollama. Rotate between multiple API keys for high-volume use.

**Implementation ideas:**
- `failover.py` — ordered fallback chain: primary provider → secondary → local Ollama
- `provider_settings.json` — extend to store multiple API keys per provider (key rotation list)
- On 429/503 from any provider: mark it on cooldown for N seconds, try next in chain
- `GET /providers/health` already exists — feed results into failover logic
- Configurable chain: users drag-and-drop provider preference in providers.html
- Notify user in status pill when failover occurred: "⚠ Switched to Ollama (OpenAI rate-limited)"
- Exponential backoff + jitter for retry timing

**Priority:** 🟡 Medium — reliability improvement, especially under heavy use.

---

## 9. Web Tools — Search, Fetch, Readability

**What OpenClaw does:**  
`src/agents/tools/web-tools.ts`, `web-fetch.ts`, `web-search.ts` — Agent can fetch any URL (with loopback auth guard for SSRF prevention), extract clean text via Readability, and search via DuckDuckGo/Brave/SearXNG. Cloudflare Markdown API support.

**What Intelli can do:**  
Give the agent tools to *autonomously fetch and search the web*, beyond what the user's current tab shows. This enables research workflows even without page context.

**Implementation ideas:**
- `tools/web_fetch.py` — fetch URL via `httpx`, run through `readability-lxml`, return clean text + metadata
- `tools/web_search.py` — DuckDuckGo search via unofficial API or SearXNG self-hosted instance
- SSRF guard: block private/loopback IPs (same security model as OpenClaw)
- Tool exposed to agent: `web_fetch(url)`, `web_search(query, n=5)`
- Results shown in chat as collapsible "search results" or "fetched page" cards
- Cache fetched pages in memory for the session (avoid re-fetching)
- Integration with page context: agent uses web_fetch to deep-dive on linked pages

**Priority:** 🔴 High — core research capability; completes the "AI browser" loop.

---

## 10. Image & Vision Upload

**What OpenClaw does:**  
`src/agents/tools/image-tool.ts` — Attach images to agent messages for vision analysis (GPT-4o, Claude, Gemini). Image sanitization (EXIF strip, resize) before sending. Screenshot tool returns base64.

**What Intelli can do:**  
Users drag images into the chat, or click "📸 Screenshot" to attach the current tab. Agent analyzes them with vision-capable models.

**Implementation ideas:**
- Drag-and-drop zone in chat.html; image preview thumbnail before sending
- `POST /chat/upload` — receive image, strip EXIF (Pillow), resize to max 1920px, base64-encode
- Include as `content: [{ type: "image_url", image_url: { url: "data:..." } }]` in message
- Screenshot tool button in chat context bar: grabs current BrowserView screenshot via IPC
- Vision-model guard: automatically check if selected provider/model supports vision
- Paste from clipboard (Ctrl+V) to attach screenshots

**Priority:** 🟡 Medium — high value for debugging, analysis, and content work.

---

## 11. Cron / Scheduled Agent Tasks

**What OpenClaw does:**  
`src/agents/tools/cron-tool.ts` + `src/cron/` — Agent creates cron jobs: "check my portfolio every hour and alert me if anything drops 5%". Jobs run in the background, results pushed to configured channel.

**What Intelli can do:**  
The gateway's `scheduler.py` already exists. Extend it so the agent can *create its own scheduled tasks* on demand.

**Implementation ideas:**
- Agent tool: `schedule_task(cron_expr, task_description, skill)` — persisted to `schedules.json`
- Task runs in background asyncio loop; result appears as a notification in the browser
- Electron tray notification on task completion (`new Notification(...)`)
- Scheduled tasks: page change watcher, daily summary of bookmarks, weekly digest of saved pages, price alerts, news briefing
- `GET /scheduler/tasks` — list scheduled tasks; `DELETE /scheduler/tasks/{id}` — cancel
- Scheduler UI: new panel in admin hub listing all scheduled tasks with next-run time

**Priority:** 🟡 Medium — enables the "always-on assistant" experience.

---

## 12. Tool Approval Flow

**What OpenClaw does:**  
`src/agents/bash-tools.exec-approval-request.ts` + existing Intelli `test_approvals_*.py` — Before executing risky tools (bash, file write, form submit), the gateway pauses and sends an approval request to the user. User approves/rejects; the agent proceeds accordingly.

**What Intelli can do:**  
Intelli already has an approvals API skeleton. Wire it to the new agent tools so that dangerous actions (browser.click on a "Submit Payment" button, file.write, web_fetch to unknown domain) require explicit user confirmation.

**Implementation ideas:**
- Tool decorator `@requires_approval(risk_level="high")` in Python tool catalog
- Approval request shows: tool name, arguments preview, estimated impact
- Popup in browser UI (persistent notification bar, not intrusive modal) with "✓ Allow" / "✗ Deny" / "Allow this session"
- Timeout: if user doesn't respond in 60s, auto-deny and inform agent
- Audit trail records all approvals/denials with actor
- Allowlist: trusted tools/sites the user permanently approves

**Priority:** 🟡 Medium — critical for trust when enabling autonomous browser control.

---

## 13. Coding Agent Mode

**What OpenClaw does:**  
`skills/coding-agent/` + bash tools + file read/write tools — Full coding loop: read files, edit with apply-patch, run bash, iterate. Supports Claude Code-style operation with file system access and terminal emulation.

**What Intelli can do:**  
"Coding agent" mode where the agent can read files from a configured workspace directory, write/patch files, run shell commands, and see stdout. Ideal for devs who browse docs and want the agent to immediately apply changes.

**Implementation ideas:**
- Agent tools: `file.read(path)`, `file.write(path, content)`, `file.patch(path, unified_diff)`, `shell.exec(cmd)` (sandboxed)
- Workspace root: configurable in settings (defaults to `~/intelli-workspace`)
- Shell sandbox: Docker container or Python `subprocess` with timeout + output limit
- `shell.exec` result shown in chat as terminal-style output block
- Agent can open generated files directly as a new browser tab (via `file://` or via a local static server)
- Integration with addons: agent writes an addon, immediately injects it into the current tab for testing
- "Fix this page's bug" workflow: agent reads the page source from the snapshot, diagnoses, writes a patch as an addon

**Priority:** 🔴 High — signature feature; devs will love building and testing browser addons in-place.

---

## 14. Agent Personas & Identity

**What OpenClaw does:**  
`src/agents/identity.ts` + `SOUL.md` + `identity-avatar.ts` — Each agent has a name, avatar, personality, per-channel prefix, and human-like response delay. SOUL.md defines values. Multiple agent identities can be configured.

**What Intelli can do:**  
Let users create multiple agent personas: "Research Assistant", "Code Helper", "Writing Coach" — each with their own SOUL.md, skill set, and provider preference. Switch personas with one click.

**Implementation ideas:**
- Personas stored in workspace: `~/.intelli/personas/{slug}/`
  - `SOUL.md` — personality description
  - `AGENTS.md` — system prompt override
  - `config.json` — preferred provider, model, enabled skills
- "Persona selector" dropdown in chat header or `chat.html` config bar
- Each persona has an avatar (emoji or image) shown in the message bubbles
- Default persona: "Intelli" (current behavior)
- Persona import/export as ZIP
- Workspace editor (`workspace.html`) extended with a Personas tab

**Priority:** 🟡 Medium — great UX differentiation; power users will love it.

---

## 15. MCP Server Integration

**What OpenClaw does:**  
`skills/mcporter/` — Acts as a bridge to any MCP (Model Context Protocol) server without modifying core. Add MCP servers via config; they appear as tools to the agent. Decoupled from core runtime.

**What Intelli can do:**  
Run an MCP client inside the gateway, connecting to any local or remote MCP server. This gives access to the entire MCP ecosystem (database tools, file systems, APIs) without writing custom adapters.

**Implementation ideas:**
- `mcp_client.py` — speaks the MCP stdio/SSE protocol; wraps MCP tools as FastAPI-callable tools
- Config: `mcp_servers` list in `agent_settings.json` — each entry: `{ name, command, args, env }`
- MCP tools automatically added to the agent's tool catalog for the session
- UI: MCP servers panel in admin hub — add/remove/status
- Popular MCP servers to pre-configure: `filesystem`, `github`, `postgres`, `google-maps`, `brave-search`
- Restart-free: hot-reload MCP server config without restarting the gateway

**Priority:** 🟡 Medium — unlocks the entire MCP ecosystem with minimal code.

---

## 16. Skill Creator (AI Self-Extending)

**What OpenClaw does:**  
`skills/skill-creator/` — The agent writes, tests, and installs new skills on demand. User says "make me a skill that tracks Hacker News top posts every morning" — the agent generates the SKILL.md, tests it, and installs it.

**What Intelli can do:**  
Same pattern: the agent generates a skill (AGENTS.md fragment + Python tool stub + optional JS addon) and uses the workspace API to install it. The chat.html "Create Addon" button is already a step in this direction.

**Implementation ideas:**
- System prompt includes a "how to create a skill" reference
- Agent generates skill JSON, calls `POST /workspace/skills` and then edits `skills/{slug}/SKILL.md`
- Skill validation: syntax check Python, load attempt, report errors back to agent for iteration
- Expose `POST /workspace/skills/{slug}/test` — dry-run the skill's tool function
- Show "🛠 New skill created: {name}" notification in chat with a link to workspace.html editor
- Loop: agent → create skill → test → fix → activate, all autonomously

**Priority:** 🟡 Medium — the self-extending agent is a compelling demo and real power feature.

---

## 17. Page Diff Watcher

**What OpenClaw does:**  
`skills/blogwatcher/` — Watches URLs for content changes (polls on cron), diffs the content, and notifies the user or agent when something changes.

**What Intelli can do:**  
Native advantage: the browser is already fetching pages. Add a "Watch this page" button that saves a baseline snapshot and alerts when content changes.

**Implementation ideas:**
- `watcher.py` — polls stored URLs on a configurable interval, compares with stored baseline using text diff
- `POST /watchers` — create watcher: `{ url, interval_minutes, notify_threshold }` 
- `GET /watchers` — list active watchers with last-seen state
- Diff algorithm: character-level diff, ignoring navigation/ads (Readability pre-processing)
- Alert: Electron tray notification + red badge on the Intelli icon
- "Watch tab" button in browser toolbar — one-click; agent can also create watchers via tool
- Agent can summarize the diff: "The price dropped from $299 to $249"

**Priority:** 🟡 Medium — very useful for research, news, and price tracking.

---

## 18. PDF / Document Analysis

**What OpenClaw does:**  
`skills/nano-pdf/` — Agent reads and analyzes PDF files: counts pages, extracts text, searches within a PDF, summarizes sections.

**What Intelli can do:**  
The browser already renders PDFs natively. Expose them to the agent for Q&A, summary, and data extraction.

**Implementation ideas:**
- `tools/pdf_reader.py` — uses `pypdf` or `pdfminer.six` to extract text from PDF URLs or uploaded files
- When agent detects a PDF URL in the active tab snapshot, auto-suggest "📄 Analyze this PDF"
- `POST /tools/pdf/extract` — accepts URL or base64 file → returns structured text + page metadata
- Long PDFs: chunk, embed, and store in vector memory for semantic Q&A
- "Chat with PDF" mode: like current page context, but chunked and indexed
- Export extracted text blocks to workspace as a note

**Priority:** 🟡 Medium — very common need; researchers and business users hit this constantly.

---

## 19. Knowledge Base Connectors

**What OpenClaw does:**  
`skills/obsidian/` + `skills/notion/` + `skills/bear-notes/` + `skills/apple-notes/` — Read/write to external knowledge bases. Clip pages to Obsidian, create Notion pages, append to Bear notes.

**What Intelli can do:**  
Save browsing research directly to knowledge bases without leaving the browser.

**Implementation ideas:**
- Obsidian connector: watch configured vault directory, `POST /kb/obsidian/clip` → saves Markdown file to vault
- Notion connector: `POST /kb/notion/clip` → creates a Notion page via Notion API
- Simple local note system first: `POST /notes/save` — appends a Markdown note to `~/.intelli/notes/YYYY-MM-DD.md`
- One-click "📋 Save to Notes" button appears on agent responses or via right-click context menu on selection
- Note includes source URL, timestamp, optional summary
- Notes searchable via vector memory system (feature 2)

**Priority:** 🟢 Low-Medium — polish feature; build the local note system first.

---

## 20. Secure Credential Store

**What OpenClaw does:**  
`skills/1password/` — Agent can retrieve credentials from 1Password CLI to use in automated tasks (form fill, API calls).

**What Intelli can do:**  
Securely store login credentials for automated tasks (form autofill, scheduled logins). Never stored in plaintext.

**Implementation ideas:**
- OS keychain integration via `keyring` Python package (macOS Keychain, GNOME Keyring, Windows Credential Store)
- `POST /credentials` — store name + secret in OS keychain, AES-encrypted at rest
- Agent tool: `credentials.get(name)` — retrieve secret; requires user to unlock with master password first
- Lock screen: after 5 min idle, re-prompt for master password
- Optional 1Password CLI bridge (same as OpenClaw) for users who already use 1Password
- Form autofill: agent sees a login form in the tab snapshot and offers to fill it from the credential store

**Priority:** 🟢 Low — security-sensitive; implement after sandbox is solid.

---

## 21. Extension / Plugin System

**What OpenClaw does:**  
Extensions under `extensions/*` as standalone workspace packages (npm). Community publishes on ClawHub. Plugin SDK in `src/plugin-sdk/`. Optional deps stay in plugin package, not core.

**What Intelli can do:**  
Let the community publish "Intelli plugins" — Python packages that register new tools and skills. Installed via pip or from a registry.

**Implementation ideas:**
- Plugin entry point: `intelli_plugin.json` — declares tool endpoints, skill prompts, required env vars
- Plugin loader: scans `~/.intelli/plugins/` + configured registry at startup
- `POST /admin/plugins/install` — install from URL/package name
- Registry: a simple JSON file hosted on GitHub (community-maintained, similar to ClawHub)
- Tool sandboxing: plugins run in a subprocess with limited permissions
- Plugin manager in admin hub: browse, install, enable/disable, uninstall
- Official starter plugins: `intelli-spotify`, `intelli-weather`, `intelli-github`, `intelli-obsidian`

**Priority:** 🟢 Low — future ecosystem play; build after core feature set is stable.

---

## 22. Notification & Webhook Push

**What OpenClaw does:**  
Full multi-channel messaging: WhatsApp, Telegram, Slack, Discord, iMessage, Signal, Teams. Agent proactively sends results to any channel.

**What Intelli can do:**  
Send agent results and watcher alerts to external channels. Users configure Telegram or Discord bot tokens; the agent pushes updates there.

**Implementation ideas:**
- `webhooks.py` already exists in `agent-gateway/` — extend to support push (not just receive)
- Notification channels: Telegram bot, Discord webhook, Slack webhook, desktop notification, email (SMTP)
- `POST /notify/{channel}` — send a text/image message to configured channel
- Agent tool: `notify(message, channel="telegram")` — push result to user asynchronously
- Notification settings panel in admin hub
- Electron native notifications for local alerts (`Notification` API in preload.js)

**Priority:** 🟢 Low-Medium — useful for scheduled task results and watcher alerts.

---

## 23. Session History & Repair

**What OpenClaw does:**  
`src/agents/session-transcript-repair.ts` + `session-file-repair.ts` — Persists full conversation history to disk. On restart, repairs corrupted transcripts. History queryable per session.

**What Intelli can do:**  
Persist chat history across gateway restarts. Users can browse past conversations, search them, and resume any session.

**Implementation ideas:**
- `sessions.py` — save each chat turn to `~/.intelli/sessions/{date}/{session_id}.jsonl`
- `GET /sessions` — list sessions with date + first message preview
- `GET /sessions/{id}` — full message history
- `POST /chat/resume/{id}` — load history into current chat context
- Repair: validate JSONL on load, skip malformed lines, log warnings
- Search: semantic search across all sessions (feeds into vector memory)
- Session browser UI: sidebar panel or separate `sessions.html` page
- Auto-archive sessions older than 30 days (configurable)

**Priority:** 🟡 Medium — users expect continuity; current implementation is in-memory only.

---

## 24. Sandbox Code Execution

**What OpenClaw does:**  
`src/agents/sandbox/` + `Dockerfile.sandbox` — Executes agent-generated code inside a Docker container with seccomp profiles, read-only mounts, and network restrictions. Prevents malicious code from escaping.

**What Intelli can do:**  
`agent-gateway/sandbox/` already exists with `docker_runner.py`. Wire it to the coding agent so all LLM-generated code runs in the sandbox, not the host.

**Implementation ideas:**
- `sandbox/docker_runner.py` already provides the container run primitive
- Agent tool: `sandbox.exec(code, lang)` — runs in Docker with timeout and output capture
- Languages: Python, Node.js, Bash, Ruby (via pre-built sandbox images)
- Network:
  - Default: no network (pure compute)
  - With flag `allow_web=True`: restricted outbound only (user confirms)
- File system: `/workspace` bind-mounted from `~/.intelli/workspace`; results written there
- Output streamed back to chat as terminal block
- Kill switch: `DELETE /sandbox/exec/{id}` — immediately terminate runaway jobs

**Priority:** 🔴 High — required before enabling any autonomous code execution.

---

## 25. Navigation Guard & Security Layer

**What OpenClaw does:**  
`src/browser/navigation-guard.ts` — Blocks navigation to unsafe URLs. Prevents the agent from visiting SSRF targets, internal network addresses, or known malicious domains.

**What Intelli can do:**  
Intercept navigation events in the Electron `BrowserView` and run them through a policy check before proceeding.

**Implementation ideas:**
- `main.js` — hook `webContents.on('will-navigate')` for each BrowserView
- Policy check: POST URL to `GET /security/check_url` — returns `{ allow, reason }`
- Block list: known phishing/malware domains (community feed, e.g. PhishTank)
- Private IP guard: block `10.x`, `192.168.x`, `localhost` (unless explicitly allowed)
- Prompt injection detection: scan fetched page text for `<!-- IGNORE PREVIOUS INSTRUCTIONS -->` style attacks
- User-level allowlist/blocklist: `Settings > Security > Site Rules`
- Warning page: when navigation is blocked, show the Intelli warning page with reason + override option

**Priority:** 🟡 Medium — especially important when autonomous browser control is enabled.

---

## 26. A2A — Agent-to-Agent Sessions

**What OpenClaw does:**  
`src/agents/tools/sessions-send-tool.a2a.ts` — Agents send messages to other agent sessions (different personas, different tools, different models). Enables specialized micro-agents: "Researcher" + "Writer" + "Critic" working in chain.

**What Intelli can do:**  
Chained agent workflows: user gives a high-level goal; an orchestrator persona decomposes it and routes sub-tasks to specialized personas (defined in workspace).

**Implementation ideas:**
- Extend sub-agents (feature 4) with inter-session messaging
- Agent tool: `message_agent(persona, task)` — sends a task to another persona's session, waits for reply
- Personas can be remote (other users' Intelli instances via invite code) or local
- Progress shown in a "task board" UI: columns for In Progress, Done, Waiting
- Simple first implementation: two hardcoded roles — "Research" (web fetch heavy) and "Synthesis" (writing heavy)

**Priority:** 🟢 Low — advanced feature; implement after personas (14) and sub-agents (4) are stable.

---

## 27. Video Frame Analysis

**What OpenClaw does:**  
`skills/video-frames/` — Extracts frames from video files or streams and sends them to vision models for analysis. Enables "describe what's happening in this video" workflows.

**What Intelli can do:**  
Extract frames from video pages the user is watching (YouTube, Vimeo) or local video files. Pass frames to vision model for Q&A.

**Implementation ideas:**
- `tools/video_frames.py` — uses `ffmpeg` subprocess to extract N frames as JPEG
- Browser integration: detect `<video>` element in active tab, capture current frame via `canvas.drawImage(video)` JS injection
- Agent tool: `video.describe(n_frames=5)` — grabs evenly-spaced frames, sends to vision model
- "What's on screen?" quick action button in chat context bar
- Transcription: also pipe audio track through Whisper STT (feature 7)

**Priority:** 🟢 Low — niche but compelling; easy to implement given existing vision (10) + STT (7).

---

## 28. Usage Analytics & Observability

**What OpenClaw does:**  
`skills/model-usage/` + `src/agents/usage.ts` — Tracks token usage per model, cost estimates, usage over time. Detailed logs with provider attribution.

**What Intelli can do:**  
The metrics system `metrics.py` already collects data. Build a proper analytics dashboard.

**Implementation ideas:**
- `metrics.py` — extend to track: tokens in/out per provider per day, chat count, tool call count, page visits
- Cost estimator: map model → price per 1M tokens (configurable table), compute `usage × price`
- `GET /metrics/usage?range=7d` — time-series data for charts
- `ui/analytics.html` — dashboard with:
  - Tokens/day bar chart (recharts via CDN)
  - Cost breakdown by provider (pie chart)
  - Most-used skills / tools
  - Top visited domains
- Export to CSV
- Budget alerts: `POST /metrics/budget` — set daily token budget; get tray notification when 80% used

**Priority:** 🟢 Low-Medium — valuable for cost management; easy to build on existing metrics infrastructure.

---

## Implementation Priority Summary

| # | Feature | Priority | Effort |
|---|---------|----------|--------|
| 1 | Agent Browser Automation | 🔴 High | Large |
| 2 | Persistent Vector Memory | 🔴 High | Large |
| 9 | Web Tools (Search + Fetch) | 🔴 High | Medium |
| 13 | Coding Agent Mode | 🔴 High | Large |
| 24 | Sandbox Code Execution | 🔴 High | Medium |
| 5 | Skill Ecosystem | 🔴 High | Large |
| 3 | Canvas — Live UI | 🟡 Medium | Medium |
| 4 | Sub-Agents | 🟡 Medium | Medium |
| 6 | Session Compaction | 🟡 Medium | Small |
| 7 | Voice I/O | 🟡 Medium | Medium |
| 8 | Model Failover | 🟡 Medium | Small |
| 10 | Image & Vision | 🟡 Medium | Small |
| 11 | Cron / Scheduled Tasks | 🟡 Medium | Small |
| 12 | Tool Approval Flow | 🟡 Medium | Small |
| 14 | Agent Personas | 🟡 Medium | Medium |
| 15 | MCP Integration | 🟡 Medium | Medium |
| 16 | Skill Creator | 🟡 Medium | Medium |
| 17 | Page Diff Watcher | 🟡 Medium | Small |
| 23 | Session History | 🟡 Medium | Medium |
| 25 | Navigation Guard | 🟡 Medium | Small |
| 18 | PDF Analysis | 🟡 Medium | Small |
| 19 | Knowledge Base Connectors | 🟢 Low | Small |
| 20 | Credential Store | 🟢 Low | Medium |
| 21 | Plugin System | 🟢 Low | Large |
| 22 | Notifications & Webhooks | 🟢 Low | Small |
| 26 | A2A Sessions | 🟢 Low | Large |
| 27 | Video Frames | 🟢 Low | Small |
| 28 | Usage Analytics | 🟢 Low | Small |

---

## Guiding Philosophy (adapted from OpenClaw's VISION.md)

> **Intelli is the AI that actually *browses* things.**  
> It runs in your browser, on your device, with your rules.  
> The browser is the computer — Intelli makes it programmable by anyone.

Core principles:
- **Local first** — everything runs on your machine; no data leaves unless you explicitly allow it
- **Secure defaults** — autonomous actions require explicit approval before first use
- **Open by design** — workspace files are plain Markdown; skills are readable text; no lock-in
- **Hackable** — users can write skills in plain Python or JS; no special SDK required
- **Skills over features** — new capabilities ship as skills first; only graduate to core when battle-tested
