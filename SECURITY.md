# Security Policy — Intelli Agent Gateway

## Reporting Vulnerabilities

**Do not file public GitHub issues for security vulnerabilities.**
Email the maintainer directly with a description and proof-of-concept.
You will receive an acknowledgement within 72 hours.

---

## Threat Model Summary

| Threat | Mitigation | Status |
|---|---|---|
| Injection via tool args | JSON Schema validation + sanitizer in Supervisor | Implemented |
| Unchecked code execution | Subprocess sandbox + action whitelist | Implemented (scaffold) |
| Privilege escalation via token | PBKDF2 auth + Bearer tokens with TTL | Implemented |
| Stale/revoked tokens | In-memory revocation list + expiry check | Implemented |
| Sensitive DOM data leakage | Per-origin redaction rules + audit log | Implemented |
| Supply-chain attacks | pip-audit in CI + SBOM generation | Implemented |
| API key leakage | Keyring / Vault / env fallback chain | Implemented |
| Worker process escape | Docker isolation + network_disabled | Scaffold |
| Approval queue bypass | Admin Bearer token required for approve/reject | Implemented |
| Audit log tampering | Append-only log; archive recommended | Implemented |
| SSRF via provider adapters | Adapter calls are scaffolded (no live network) | Scaffold |
| Denial of service (large payloads) | 256 KB IPC payload limit in worker | Implemented |
| Denial of service (oversized tool-call names) | `max_length=256` on `ToolCall.tool` via Pydantic `Field` — returns 422 | Implemented |
| Outbound SSRF via provider adapters | `_check_outbound_url()` in `providers/adapters.py` + `INTELLI_PROVIDER_OUTBOUND_ALLOWLIST` | Implemented |

---

## Hardening Checklist

### Authentication & Authorisation
- [x] PBKDF2-SHA256 password hashing with per-user salt
- [x] Short-lived access tokens (default 1 h) + long-lived refresh tokens (7 d)
- [x] In-memory token revocation
- [x] Persistent token revocation (survive restart) — `revoked_tokens.json`
- [ ] OAuth2 / OIDC federation for enterprise SSO
- [x] Rate limiting on `/admin/login` (brute-force protection) — `rate_limit.py`

### Secrets Management
- [x] OS keyring integration for user credentials
- [x] Environment variable fallback for CI/dev
- [x] HashiCorp Vault adapter scaffold
- [ ] Vault AppRole / Kubernetes auth for production
- [x] Secret rotation workflow and TTL enforcement — `key_rotation.py`

### Sandboxing
- [x] Subprocess worker with action whitelist
- [x] IPC payload size limit (256 KB)
- [x] Tool call name size limit — Pydantic `Field(..., max_length=256)` on `ToolCall.tool`; returns 422 for oversized identifiers
- [x] Per-call timeout enforcement
- [x] Docker runner scaffold with `network_disabled=True`
- [x] seccomp allowlist profile for worker process — `agent-gateway/sandbox/seccomp-worker.json` (Linux/Docker; denies networking, process-spawning, and dangerous kernel interfaces)
- [x] Read-only filesystem for worker container — `docker_runner.py`: `read_only=True` + `tmpfs={'/tmp': 'size=16m'}` (writable scratch only in `/tmp`)
- [x] CPU and memory quota enforcement — `docker_runner.py`: `mem_limit` (default 64 m), `nano_cpus` (default 0.5), `pids_limit` (default 64); all overridable via `SANDBOX_DOCKER_MEMORY`, `SANDBOX_DOCKER_CPUS`, `SANDBOX_DOCKER_PIDS`

### Network & Transport
- [ ] TLS termination (run behind nginx/Caddy in production)
- [x] CORS policy — `CORSMiddleware` defaults to `http://127.0.0.1:8080`; override with `AGENT_GATEWAY_CORS_ORIGINS` env var for multi-origin production deployments
- [x] Outbound allowlist for provider adapter HTTP calls — `_check_outbound_url()` in `providers/adapters.py`; env `INTELLI_PROVIDER_OUTBOUND_ALLOWLIST`
- [x] SSE streaming CORS — `POST /chat/complete?stream=true` returns `Access-Control-Allow-Origin: *`; safe in practice because the gateway binds to `127.0.0.1` only; in production, pin this header to the local origin instead of `*`

### Audit & Monitoring
- [x] Append-only audit log for all admin actions
- [x] Prometheus metrics endpoint
- [x] Audit export endpoint (`/admin/audit`)
- [x] Log shipping to external SIEM
- [x] Alerting on `worker_healthy=0` or high validation error rate — background `alert-monitor` thread fires `gateway.alert` webhooks on worker health transitions and when `tool_validation_errors_total` rate exceeds `AGENT_GATEWAY_VALIDATION_ERR_THRESHOLD` in a rolling window; configurable via `PUT /admin/alerts/config`

### Supply Chain
- [x] `pip-audit` in CI (checks for known CVEs)
- [x] SBOM generation via `pip-licenses`
- [x] Pinned dependency hashes in `requirements.lock` (`pip-compile --generate-hashes`)
- [x] Signed release tags and provenance attestation — `.github/workflows/release.yml` triggers on `v*` tags; builds wheel + sdist, signs artifacts with `sigstore/gh-action-sigstore-python` (keyless Sigstore OIDC signing, `id-token: write` permission)

### Data Privacy
- [x] Per-origin field redaction (persisted rules)
- [x] `[REDACTED]` substitution for sensitive input values
- [x] GDPR/CCPA data-deletion API — `consent_log.py` export/erase endpoints
- [x] Encrypted audit log at rest — AES-256-GCM per-line encryption in `app.py` (`_encrypt_audit_line` / `_decrypt_audit_line`); enabled by setting `INTELLI_AUDIT_ENCRYPT_KEY` to a 64-hex-char (32-byte) key; no-op when env var is absent

---

## Security Assumptions

1. The agent gateway runs on localhost by default and is not exposed to the public internet.
2. Admin credentials are provisioned via environment variables in production; never committed to source.
3. The sandbox worker whitelist is the sole gate for permitted actions; no arbitrary code execution is supported.
4. Provider API keys should be stored in Vault in production; file fallback is for development only.

---

## Dependencies with Known History

Run `pip-audit` to check for CVEs:

```bash
pip install pip-audit
pip-audit -r agent-gateway/requirements.txt
```

The CI workflow automatically runs `pip-audit` and uploads results as artifacts.
