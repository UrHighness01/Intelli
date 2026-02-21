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

---

## Hardening Checklist

### Authentication & Authorisation
- [x] PBKDF2-SHA256 password hashing with per-user salt
- [x] Short-lived access tokens (default 1 h) + long-lived refresh tokens (7 d)
- [x] In-memory token revocation
- [ ] Persistent token revocation (survive restart)
- [ ] OAuth2 / OIDC federation for enterprise SSO
- [ ] Rate limiting on `/admin/login` (brute-force protection)

### Secrets Management
- [x] OS keyring integration for user credentials
- [x] Environment variable fallback for CI/dev
- [x] HashiCorp Vault adapter scaffold
- [ ] Vault AppRole / Kubernetes auth for production
- [ ] Secret rotation workflow and TTL enforcement

### Sandboxing
- [x] Subprocess worker with action whitelist
- [x] IPC payload size limit (256 KB)
- [x] Per-call timeout enforcement
- [x] Docker runner scaffold with `network_disabled=True`
- [ ] seccomp profile for subprocess worker (Linux)
- [ ] Read-only filesystem for worker container
- [ ] CPU and memory quota enforcement in production

### Network & Transport
- [ ] TLS termination (run behind nginx/Caddy in production)
- [ ] CORS policy — restrict `/tools/call` to localhost in production
- [ ] Outbound allowlist for provider adapter HTTP calls

### Audit & Monitoring
- [x] Append-only audit log for all admin actions
- [x] Prometheus metrics endpoint
- [x] Audit export endpoint (`/admin/audit`)
- [ ] Log shipping to external SIEM
- [ ] Alerting on `worker_healthy=0` or high validation error rate

### Supply Chain
- [x] `pip-audit` in CI (checks for known CVEs)
- [x] SBOM generation via `pip-licenses`
- [ ] Pinned dependency hashes in `requirements.txt`
- [ ] Signed release tags and provenance attestation

### Data Privacy
- [x] Per-origin field redaction (persisted rules)
- [x] `[REDACTED]` substitution for sensitive input values
- [ ] GDPR/CCPA data-deletion API
- [ ] Encrypted audit log at rest

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
