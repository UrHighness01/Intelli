# Intelli Architecture — Overview

Below is a high-level architecture diagram (Mermaid) showing the main components and data flows for Intelli.

```mermaid
flowchart LR
  BrowserUI[Browser UI (Tabs + Sidebar)]
  TabBridge[Tab Context Bridge]
  AgentGateway[Agent Gateway (local HTTP/IPC)]
  Supervisor[Agent Supervisor & Validator]
  ToolProxy[Tool Proxy & Sandbox]
  LocalModels[Local Model Runtimes]
  CloudProviders[Cloud LLM Providers]
  Storage[Encrypted Storage / Agent Memory]
  AddonRegistry[Addon Registry (signed)]

  BrowserUI -->|captures DOM snapshot| TabBridge
  TabBridge -->|context (policy-controlled)| AgentGateway
  AgentGateway -->|validate / route| Supervisor
  Supervisor -->|validated calls| ToolProxy
  ToolProxy -->|exec in sandbox| BrowserUI

  Supervisor --> LocalModels
  Supervisor --> CloudProviders
  AgentGateway --> Storage
  AgentGateway --> AddonRegistry

  classDef infra fill:#f9f,stroke:#333,stroke-width:1px;
  class CloudProviders,LocalModels,Storage,AddonRegistry infra
```

Notes:
- The `Tab Context Bridge` serializes the active tab to a structured snapshot and enforces per-site permissions and redaction rules before any context leaves the renderer process.
- The `Agent Gateway` is the local-facing HTTP/IPC endpoint that only accepts requests from the browser and local agents. It delegates validation to the `Supervisor`.
- The `Supervisor` performs schema validation, escaping, sanitization and enforces execution policies (approval thresholds, rate limits, RBAC).
- The `Tool Proxy` runs in a sandboxed helper process with strictly limited capabilities; it performs UI actions only after explicit user or policy approval.
- `LocalModels` are optional on-device runtimes (GGML, Ollama, etc.). `CloudProviders` are used when high-reliability function-calls or heavy models are required.
- All persistent data (agent memory, logs, keys) is encrypted at rest and accessible only to authorized components.
