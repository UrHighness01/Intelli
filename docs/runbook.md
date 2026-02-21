# Operator Runbook — Intelli Agent Gateway

This runbook covers monitoring, incident response, and operational procedures
for the Intelli Agent Gateway service.

---

## 1. Starting the Gateway

```bash
# Development (auto-reload)
uvicorn agent_gateway.app:app --host 0.0.0.0 --port 8080 --reload

# Production (multiple workers)
uvicorn agent_gateway.app:app --host 0.0.0.0 --port 8080 --workers 4
```

Environment variables used by the gateway:

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_ADMIN_USER` | `admin` | Bootstrap admin username |
| `AGENT_GATEWAY_ADMIN_PASS` | (required) | Bootstrap admin password |
| `AGENT_GATEWAY_ACCESS_EXPIRE` | `3600` | Access token TTL (seconds) |
| `AGENT_GATEWAY_REFRESH_EXPIRE` | `604800` | Refresh token TTL (seconds) |
| `SANDBOX_WORKER_PATH` | (bundled) | Path to `sandbox/worker.py` |
| `SANDBOX_POOL_SIZE` | `2` | Persistent worker pool size |
| `SANDBOX_WORKER_TIMEOUT` | `5` | Per-call timeout (seconds) |
| `SANDBOX_DOCKER_IMAGE` | `python:3.11-slim` | Docker image for isolated runs |
| `VAULT_ADDR` | — | HashiCorp Vault URL |
| `VAULT_TOKEN` | — | Vault auth token |
| `VAULT_KV_MOUNT` | `secret` | Vault KV mount |
| `VAULT_KV_PREFIX` | `intelli/providers` | Vault secret prefix |

---

## 2. Health Checks

```bash
# Gateway liveness
curl http://localhost:8080/health

# Sandbox worker health
curl http://localhost:8080/health/worker

# Prometheus metrics
curl http://localhost:8080/metrics
```

Expected responses when healthy:
- `/health` → `{"status":"ok"}`
- `/health/worker` → `{"worker_healthy":true}`

---

## 3. Admin Authentication

```bash
# Log in and capture tokens
TOKEN=$(curl -s -X POST http://localhost:8080/admin/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<pass>"}' | jq -r .token)

# Export last 200 audit entries
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/admin/audit

# Refresh access token
curl -X POST http://localhost:8080/admin/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<rt>"}'

# Revoke a token
curl -X POST http://localhost:8080/admin/revoke \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"token":"<token_to_revoke>"}'
```

---

## 4. Managing Approvals

```bash
# List pending approvals
curl http://localhost:8080/approvals

# Approve a request
curl -X POST http://localhost:8080/approvals/42/approve \
  -H "Authorization: Bearer $TOKEN"

# Reject a request
curl -X POST http://localhost:8080/approvals/42/reject \
  -H "Authorization: Bearer $TOKEN"

# Stream real-time approval events (SSE)
curl http://localhost:8080/approvals/stream
```

---

## 5. Redaction Rules

```bash
# Set redaction rules for an origin
curl -X POST http://localhost:8080/tab/redaction-rules \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"origin":"https://bank.example","fields":["password","card_number"]}'

# Fetch rules for an origin
curl "http://localhost:8080/tab/redaction-rules?origin=https://bank.example"
```

---

## 6. Incident Response

### Worker process crashed
1. Check `/health/worker` — if `false`, the bundled worker restart logic will kick in automatically.
2. If the pool is depleted, restart the gateway process.
3. Check logs for `SandboxError` or `WorkerProcess.restart` entries.

### Suspicious tool call
1. Pull audit log: `GET /admin/audit?tail=500`
2. Identify the `tool_call` events.
3. If a call should not have been executed, revoke the token used and rotate the admin password.
4. If a pending call looks malicious, `POST /approvals/{id}/reject`.

### Token compromise
1. Revoke all active tokens: restart the gateway (tokens are in-memory).
2. Rotate admin password via `create_user` with a new password.
3. Review audit log for actions taken with the compromised token.

---

## 7. Metrics Reference

| Metric | Type | Description |
|---|---|---|
| `process_uptime_seconds` | gauge | Seconds since gateway started |
| `tool_calls_total` | counter | Total tool call attempts, labelled by `tool` |
| `tool_call_duration_seconds` | histogram | Per-tool call latency |
| `tool_validation_errors_total` | counter | Schema validation failures, labelled by `tool` |
| `approvals_queued_total` | counter | Calls sent to the approval queue |
| `worker_healthy` | gauge | 1 if sandbox worker passes health check |
| `worker_pool_alive` | gauge | Number of alive pool workers |
| `worker_pool_size` | gauge | Configured pool size |
| `scheduler_tasks_total` | gauge | Number of registered scheduled tasks |
| `scheduler_runs_total` | counter | Total scheduler task executions, labelled by `task` |
| `scheduler_errors_total` | counter | Scheduler task failures, labelled by `task` |
| `scheduler_run_duration_seconds` | histogram | Per-task execution latency, labelled by `task` |

---

## 8. Log Retention

The audit log is append-only at `agent-gateway/audit.log`.  Archive or rotate weekly:

```bash
# Rotate (keep last 10 000 lines)
tail -n 10000 agent-gateway/audit.log > audit.log.tmp && mv audit.log.tmp agent-gateway/audit.log
```

Recommended: ship logs to an external log aggregator (Splunk, CloudWatch, Loki) via log tailing.
