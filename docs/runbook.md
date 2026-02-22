# Operator Runbook — Intelli Agent Gateway

---

## 1. Starting the Gateway

### Development (auto-reload)
```bash
uvicorn app:app --app-dir agent-gateway --host 127.0.0.1 --port 8080 --reload
```

### Production (multi-worker)
```bash
uvicorn app:app --app-dir agent-gateway --host 0.0.0.0 --port 8080 --workers 4
```

### Via Electron Browser (recommended for desktop)
```powershell
cd browser-shell && npm start
# Gateway starts automatically; killed on window close
```

---

## 2. Environment Variables

See `docs/deployment.md §7` for the full table.

Key variables to set in production:

| Variable | Description |
|---|---|
| `AGENT_GATEWAY_ADMIN_PASS` | Admin password (required) |
| `AGENT_GATEWAY_APPROVAL_ALERT_THRESHOLD` | Alert on deep approval queue |
| `AGENT_GATEWAY_APPROVAL_TIMEOUT` | Auto-reject stale approvals (seconds) |
| `AGENT_GATEWAY_RATE_LIMIT_REQUESTS` | Per-IP request cap |
| `AGENT_GATEWAY_WEBHOOK_MAX_RETRIES` | Delivery retries |
| `VAULT_ADDR` / `VAULT_TOKEN` | Production secret store |

---

## 3. Health Checks

```bash
# Gateway liveness
curl http://localhost:8080/health
# → {"status":"ok"}

# Sandbox worker health
curl http://localhost:8080/health/worker
# → {"worker_healthy":true}

# Prometheus metrics
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/metrics

# Full gateway status
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/admin/status
```

---

## 4. Authentication

```bash
# Log in
TOKEN=$(curl -s -X POST http://localhost:8080/admin/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<pass>"}' | jq -r .token)

# Or with CLI
python agent-gateway/gateway_ctl.py login
```

Token is cached at `~/.config/intelli/gateway_token` by the CLI.

---

## 5. Audit Log

```bash
# Tail last 50 entries (CLI)
python gateway_ctl.py audit tail --n 50

# Live follow (Ctrl-C to stop)
python gateway_ctl.py audit follow --interval 5

# Filter by actor
python gateway_ctl.py audit tail --actor alice --n 100

# Export CSV
python gateway_ctl.py audit export-csv --output audit.csv

# Via HTTP
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/admin/audit?tail=200&actor=alice"
```

---

## 6. Managing Approvals

```bash
# List pending queue (CLI)
python gateway_ctl.py approvals list

# Approve / reject
python gateway_ctl.py approvals approve 42
python gateway_ctl.py approvals reject 42

# Set auto-reject timeout (0 = disabled)
python gateway_ctl.py approvals timeout set 300

# Alert when queue depth >= N
python gateway_ctl.py alerts set 10

# Stream real-time events (SSE)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/approvals/stream
```

---

## 7. Kill-Switch (Emergency Stop)

```bash
# Arm (blocks all tool calls with HTTP 503)
python gateway_ctl.py kill-switch on --reason "Incident 2026-02-22"

# Status check
python gateway_ctl.py kill-switch status

# Disarm
python gateway_ctl.py kill-switch off
```

---

## 8. User Management

```bash
# List users
python gateway_ctl.py users list

# Create operator account
python gateway_ctl.py users create bob SecurePass --role user

# Restrict tool access
python gateway_ctl.py users permissions set bob file.read,noop

# Remove restriction
python gateway_ctl.py users permissions clear bob

# Change password
python gateway_ctl.py users password bob NewPass
```

---

## 9. Content Filter

```bash
# List deny rules
python gateway_ctl.py content-filter list

# Add rule
python gateway_ctl.py content-filter add "DROP TABLE" --mode literal --label "sql-injection"
python gateway_ctl.py content-filter add "(?i)(exec|eval)\(" --mode regex --label "code-exec"

# Remove rule by index
python gateway_ctl.py content-filter delete 0

# Reload from file/env
python gateway_ctl.py content-filter reload
```

---

## 10. Rate Limits

```bash
# Current config + live client snapshot
python gateway_ctl.py rate-limits status

# Update config (no restart needed)
python gateway_ctl.py rate-limits set --max-requests 120 --window 60

# Evict a banned IP
python gateway_ctl.py rate-limits reset-client 192.168.1.42

# Evict a user
python gateway_ctl.py rate-limits reset-user alice
```

---

## 11. Scheduler

```bash
# List tasks with countdown to next run
python gateway_ctl.py schedule list --next

# Create a recurring task
python gateway_ctl.py schedule create "disk-check" "system.disk_usage" \
  --args '{"path":"/"}' --interval 3600

# Trigger immediately
python gateway_ctl.py schedule trigger <task_id>

# View run history (last 25)
python gateway_ctl.py schedule history <task_id>

# Bulk enable/disable (UI: Schedule → "✓ All on" / "✗ All off" buttons)
```

---

## 12. Webhooks

```bash
# Register a webhook
python gateway_ctl.py webhooks add https://hooks.example.com/intelli \
  --events approval.created,approval.rejected \
  --secret mysecret

# List registered hooks
python gateway_ctl.py webhooks list

# View delivery history (UI: Webhooks → select hook → right panel)
# Delete
python gateway_ctl.py webhooks delete <hook_id>
```

Retry behaviour: up to `AGENT_GATEWAY_WEBHOOK_MAX_RETRIES` attempts with exponential back-off
(1 s, 2 s, 4 s …). Retried deliveries are highlighted amber in the Webhooks UI.

---

## 13. Agent Memory

```bash
# List all agent IDs
python gateway_ctl.py memory agents

# Inspect memory for an agent
python gateway_ctl.py memory list my-agent --meta   # shows TTL per key

# Set / get / delete
python gateway_ctl.py memory set my-agent context "current task" --ttl 3600
python gateway_ctl.py memory get my-agent context
python gateway_ctl.py memory delete my-agent context

# Prune expired keys
python gateway_ctl.py memory prune my-agent

# Backup all memory
python gateway_ctl.py memory export --output memory-backup.json

# Restore (merge mode by default)
python gateway_ctl.py memory import memory-backup.json
```

---

## 14. Provider Key Management

```bash
# Store a key with optional TTL
python gateway_ctl.py key set openai sk-... --ttl-days 90

# Rotate (generates new key slot)
python gateway_ctl.py key rotate openai sk-new-...

# Check status / expiry
python gateway_ctl.py key status openai
python gateway_ctl.py key expiry openai

# List all providers with health
python gateway_ctl.py provider-health list

# Show expiring keys (within 14 days)
python gateway_ctl.py provider-health expiring --within-days 14
```

---

## 15. Metrics Reference

| Metric | Type | Description |
|---|---|---|
| `process_uptime_seconds` | gauge | Seconds since gateway started |
| `tool_calls_total{tool}` | counter | Total tool call attempts per tool |
| `tool_call_duration_seconds{tool}` | histogram | Per-tool call latency |
| `tool_validation_errors_total{tool}` | counter | Schema validation failures |
| `approvals_queued_total` | counter | Calls sent to approval queue |
| `worker_healthy` | gauge | 1 if sandbox worker passes health check |
| `worker_pool_alive` | gauge | Number of alive pool workers |
| `worker_pool_size` | gauge | Configured pool size |
| `scheduler_tasks_total` | gauge | Number of registered scheduled tasks |
| `scheduler_runs_total{task}` | counter | Total scheduler task executions |
| `scheduler_errors_total{task}` | counter | Scheduler task failures |
| `scheduler_run_duration_seconds{task}` | histogram | Per-task execution latency |

```bash
# Top 5 tools by call count
python gateway_ctl.py metrics top --n 5

# Full table with p50 latency
python gateway_ctl.py metrics tools
```

---

## 16. Incident Response

### Worker process crashed
1. Check `GET /health/worker` — if `false`, the pool restart logic kicks in automatically.
2. If pool is depleted, restart the gateway process.
3. Search logs for `SandboxError` or `WorkerProcess.restart`.

### Suspicious or malicious tool call
1. Arm kill-switch immediately: `python gateway_ctl.py kill-switch on --reason "Incident"`
2. Pull audit log: `python gateway_ctl.py audit tail --n 500 --actor <suspect>`
3. Identify the `tool_call` events and their risk level.
4. Revoke suspect token and rotate admin password.
5. Reject any pending approvals: `python gateway_ctl.py approvals reject <id>`
6. Disarm kill-switch once contained: `python gateway_ctl.py kill-switch off`

### Token compromise
1. Restart the gateway (access tokens are in-memory; restart flushes all sessions).
2. Rotate admin password: `python gateway_ctl.py users password admin NewStrongPass`
3. Review audit log for actions taken with the compromised token.

### High approval queue depth
1. Check alert config: `python gateway_ctl.py alerts status`
2. Review pending approvals: `python gateway_ctl.py approvals list`
3. Lower auto-reject timeout temporarily: `python gateway_ctl.py approvals timeout set 60`
4. Determine cause via audit log.

---

## 17. Log Rotation

The audit log is append-only at `agent-gateway/audit.log`.

```bash
# Keep last 50,000 lines
tail -n 50000 agent-gateway/audit.log > /tmp/audit.tmp \
  && mv /tmp/audit.tmp agent-gateway/audit.log
```

Recommended: ship to an external log aggregator (Loki / CloudWatch / Splunk) via log tailing.

---

## 18. GDPR / Data Subject Requests

```bash
# Export all data for an actor
python gateway_ctl.py consent export alice

# Erase all data for an actor  (irreversible)
python gateway_ctl.py consent erase alice --yes

# View consent timeline
python gateway_ctl.py consent timeline --n 100
```
