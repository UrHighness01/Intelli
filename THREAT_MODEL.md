# Intelli Threat Model & Privacy Controls

This document outlines primary attack surfaces, threat scenarios, mitigations, and required privacy controls for the Intelli browser (agent-native, context-fed architecture).

## High-level assets to protect
- User browsing context (DOM snapshots, selected text, form data)
- Secrets: API keys, user credentials, crypto wallets
- Agent memory and logs
- Local model runtimes and model weights
- Browser profile data and extensions/addons
- System file access and local scripts

## Primary attack surfaces
1. Tab Context Bridge — leaking DOM snapshots or sensitive inputs to agents or external providers.
2. Agent Gateway / Supervisor — forged or malicious tool-calls routed to browser APIs.
3. Tool Proxy / Sandbox — sandbox breakout via injected scripts or malicious addons.
4. Local model runtimes — rogue model behavior or model poisoning.
5. Provider integrations — misconfiguration leading to unintended data egress to cloud LLMs.
6. Update & Supply chain — compromised third-party libs, unsigned updates.

## Threat scenarios & mitigations
- DOM leakage to cloud: default deny; require per-site explicit allow for sending DOM content off-device. Provide a visible consent modal and granular permissions UI.

- Sensitive field exfiltration (passwords, credit-cards): implement field-level redaction rules in the Tab Context Bridge (mask `<input type=password>`, PCI/SSN/CVV detectors, and user-marked secrets). Maintain an audit log of all fields sent.

- Malicious tool-call injection: enforce strong JSON-schema validation, canonical tool identifiers, argument whitelists, and a supervisor approval step for privileged tools (file write, native execution, network access). Rate-limit tool calls and require user confirmation for high-risk actions.

- Addon/script injection risk: only allow signed addons by default; sandbox all injected JS/CSS in isolated origins or shadow DOM; require user review and explicit enablement. Support a read-only preview mode for generated code.

- Sandbox breakout: run tool proxy and script execution in OS-level sandboxed helper processes (drop privileges, use seccomp/AppArmor on supported OSes, and enable job/object isolation). Limit accessible syscalls and file-system mounts.

- Local model compromise: treat model runtimes as untrusted — run them in isolated processes/containers, restrict network access by default, version-lock model artifacts, and verify checksums/signatures for model files.

- Cloud provider leakage: default to local-only unless user opts into a provider. UI must show exact context that will be sent; support ephemeral provider keys and per-request redaction toggles.

- Supply chain & updates: sign binaries and updates; produce SBOMs for builds; require reproducible builds where possible; enforce CI/CD security gates and vulnerability scanning.

## Privacy controls & UX
- Privacy-first defaults: agents disabled for private/incognito profiles; DOM feed off by default; per-site allow/deny persisted per profile.
- Redaction UI: show an exact preview of the context that will be sent to an agent/provider with an inline redact toggle and permanent redaction rules.
- Audit & transparency: per-agent action log with timestamp, tool-call, tool args (with secrets redacted), provider used, and user approvals. Allow export/delete of logs and agent memory.
- Kill-switch & emergency stop: global “pause agents” switch that immediately stops ongoing agent tasks and blocks new ones.
- Consent history: store user consent decisions with provenance and ability to revoke retroactively (i.e., instruct system to delete or re-encrypt past shared contexts).

## Policy & enforcement primitives
- Capability-based tool model: define `browser.*`, `file.*`, `network.*`, `system.*` capabilities; require explicit grant per agent and per-tool.
- Approval tiers: low-risk (read-only, summarize) auto-approved; medium-risk (write files, minor UI changes) require single user confirmation; high-risk (execute native code, network push) require multi-factor or policy approval.
- Rate-limiting & quotas: per-agent and per-profile quotas for tool calls and resource consumption.

## Logging, monitoring & forensics
- Immutable append-only logs for agent actions (local encrypted ledger) with replayable events but redacted payloads.
- Health telemetry: opt-in, privacy-preserving metrics only; provide clear toggles to opt out.
- Forensics mode for enterprise: more detailed logging under admin control with data-retention policies.

## Testing & verification
- Fuzzing: fuzz Tab Bridge inputs (malformed DOM, large payloads) and agent payloads to find validation gaps.
- Contract tests: per-tool JSON-schema contract tests to ensure supervisor validation is strict and deterministic.
- Penetration testing: periodic red-team engagements for sandbox escape and supply-chain attacks.

## Minimal secure-by-design checklist (shippable MVP)
1. DOM feed disabled by default; per-site opt-in with preview and redaction.
2. Supervisor validates schema and enforces capability checks for every tool call.
3. Tool proxy executes only in sandboxed helper processes with privilege separation.
4. Signed updates and SBOM generation in CI/CD pipeline.
5. Global agent pause/kill switch and per-agent audit logs.

## Notes for implementation
- Keep the Supervisor deterministic and auditable: validation rules must be explicit JSON-schemas and sanitizer transforms stored in version-controlled files.
- Avoid dynamic eval of agent-sent code. If the system scaffolds code (addons), require static analysis + user approval + signing before enabling.
- Design privacy UX with minimal friction: use safe defaults but enable advanced controls for power users and enterprises.
