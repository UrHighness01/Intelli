# Deployment Guide — Intelli

---

## Components

| Component | Location | Description |
|---|---|---|
| **Agent Gateway** | `agent-gateway/` | FastAPI backend — all AI/agent APIs, auth, audit, scheduler, memory |
| **Admin UI** | `agent-gateway/ui/` | 15 dark-mode HTML pages served by the gateway |
| **CLI** | `agent-gateway/gateway_ctl.py` | Operator CLI for all admin APIs |
| **Electron Browser** | `browser-shell/` | Desktop browser that auto-launches the gateway on open |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | Gateway runtime |
| Node.js 18+ | Electron browser shell only |
| (Optional) Docker | Isolated sandbox worker |
| (Optional) HashiCorp Vault | Production secrets management |

---

## 1. Local Development — Gateway Only

```powershell
# Clone
git clone https://github.com/UrHighness01/Intelli.git
cd Intelli

# Create and activate virtualenv
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
# source .venv/bin/activate          # macOS / Linux

# Install dependencies
pip install -r agent-gateway/requirements.txt

# Required: set admin credentials
$env:AGENT_GATEWAY_ADMIN_PASS = "changeme"

# Start gateway (development — auto-reload)
uvicorn app:app --app-dir agent-gateway --host 127.0.0.1 --port 8080 --reload
```

Open `http://127.0.0.1:8080/ui/` to access the Admin Hub.

---

## 2. Local Development — Electron Browser

The Electron browser **automatically launches and kills the gateway** so you do not need to run
`uvicorn` separately when using the desktop app.

```powershell
cd browser-shell
npm install          # first run — downloads Electron ~120 MB
node generate-icon.js  # first run — generates placeholder icon
npm start            # launches browser + gateway
```

The gateway is discovered at `../agent-gateway/` in dev mode.
It uses `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python3` (macOS/Linux).

---

## 3. Running Tests

```powershell
# Full pytest suite (run from repo root)
python -m pytest -q
# Expected: ~1087 passed, 2 skipped

# Sandbox worker tests only (fast, no gateway required)
python -m pytest agent-gateway/tests/test_sandbox_worker.py `
                 agent-gateway/tests/test_sandbox_manager.py -q

# Single module
python -m pytest agent-gateway/tests/test_scheduler.py -v

# With coverage
pip install pytest-cov
python -m pytest --cov=agent-gateway --cov-report=term-missing -q
```

---

## 4. Docker Deployment — Gateway

```dockerfile
# Dockerfile.gateway
FROM python:3.11-slim
WORKDIR /app
COPY agent-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent-gateway/ .
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
```

```bash
docker build -f Dockerfile.gateway -t intelli-gateway .
docker run -d \
  -p 8080:8080 \
  -e AGENT_GATEWAY_ADMIN_PASS=changeme \
  -e SANDBOX_WORKER_PATH=/app/sandbox/worker.py \
  --name intelli-gateway \
  intelli-gateway
```

---

## 5. Docker Compose (Gateway + Vault dev mode)

```yaml
# docker-compose.yml
version: '3.9'
services:
  vault:
    image: hashicorp/vault:1.15
    ports: ['8200:8200']
    environment:
      VAULT_DEV_ROOT_TOKEN_ID: dev-root-token
      VAULT_DEV_LISTEN_ADDRESS: 0.0.0.0:8200
    cap_add: [IPC_LOCK]

  gateway:
    build:
      context: .
      dockerfile: Dockerfile.gateway
    ports: ['8080:8080']
    environment:
      AGENT_GATEWAY_ADMIN_PASS: changeme
      VAULT_ADDR: http://vault:8200
      VAULT_TOKEN: dev-root-token
    depends_on: [vault]
```

```bash
docker compose up -d
```

---

## 6. HashiCorp Vault Setup

```bash
vault secrets enable -path=secret kv-v2
vault kv put secret/intelli/providers/openai api_key=sk-...
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=<your-token>
```

---

## 7. Environment Variables — Full Reference

### Core authentication

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_ADMIN_USER` | `admin` | Bootstrap admin username |
| `AGENT_GATEWAY_ADMIN_PASS` | *required* | Bootstrap admin password |
| `AGENT_GATEWAY_ACCESS_EXPIRE` | `3600` | Access token TTL (seconds) |
| `AGENT_GATEWAY_REFRESH_EXPIRE` | `604800` | Refresh token TTL (seconds) |

### Sandbox

| Variable | Default | Description |
|---|---|---|
| `SANDBOX_WORKER_PATH` | bundled | Path to `sandbox/worker.py` |
| `SANDBOX_POOL_SIZE` | `2` | Persistent worker pool size |
| `SANDBOX_WORKER_TIMEOUT` | `5` | Per-call timeout (seconds) |
| `SANDBOX_DOCKER_IMAGE` | `python:3.11-slim` | Docker image for isolated runs |

### Rate limiting

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_RATE_LIMIT_REQUESTS` | `60` | Max requests per window (per IP) |
| `AGENT_GATEWAY_RATE_LIMIT_WINDOW` | `60` | Sliding window size (seconds) |
| `AGENT_GATEWAY_RATE_LIMIT_BURST` | `10` | Burst allowance above limit |
| `AGENT_GATEWAY_USER_RATE_LIMIT_REQUESTS` | `60` | Max requests per window (per user) |
| `AGENT_GATEWAY_USER_RATE_LIMIT_WINDOW` | `60` | User window size (seconds) |

### Approvals & alerts

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_APPROVAL_TIMEOUT` | `0` | Auto-reject timeout in seconds (0 = disabled) |
| `AGENT_GATEWAY_APPROVAL_ALERT_THRESHOLD` | `0` | Queue depth alert threshold (0 = disabled) |

### Content filter

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_CONTENT_FILTER_FILE` | — | Path to content filter JSON rules file |
| `AGENT_GATEWAY_CONTENT_FILTER_PATTERNS` | — | Comma-separated literal deny patterns |

### Agent memory

| Variable | Default | Description |
|---|---|---|
| `AGENT_MEMORY_PATH` | `agent-gateway/agent_memory/` | Memory storage directory |

### Webhooks

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_WEBHOOK_MAX_RETRIES` | `3` | Delivery retry attempts (exponential back-off) |

### Capabilities

| Variable | Default | Description |
|---|---|---|
| `AGENT_GATEWAY_ALLOWED_CAPS` | — | Comma-separated allowed capability tokens |

### Vault

| Variable | Default | Description |
|---|---|---|
| `VAULT_ADDR` | — | HashiCorp Vault URL |
| `VAULT_TOKEN` | — | Vault auth token |
| `VAULT_KV_MOUNT` | `secret` | Vault KV mount |
| `VAULT_KV_PREFIX` | `intelli/providers` | Vault secret prefix |

---

## 8. Persistent Files

| File | Description |
|---|---|
| `agent-gateway/audit.log` | Append-only audit trail (JSONL) |
| `agent-gateway/users.json` | User accounts (PBKDF2 hashed passwords) |
| `agent-gateway/revoked_tokens.json` | Revoked token hashes + expiry |
| `agent-gateway/redaction_rules.json` | Per-origin field redaction rules |
| `agent-gateway/webhooks.json` | Registered webhook URLs |
| `agent-gateway/schedule.json` | Scheduled task definitions |
| `agent-gateway/consent_timeline.jsonl` | Tab context consent log |
| `agent-gateway/key_metadata.json` | Provider key TTL/rotation metadata |
| `agent-gateway/agent_memory/` | Per-agent key-value memory files (JSON) |

---

## 9. CI/CD (GitHub Actions)

**`ci.yml`** — triggers on push/PR to `main`:
- Sets `PYTHONPATH`, exports `SANDBOX_WORKER_PATH`.
- Installs `requirements.txt` + `pip-licenses` + `pip-audit`.
- Starts gateway, waits for `/health`, runs `pytest -q`.
- Generates `sbom.json` and `audit.json` artifacts.

**`release.yml`** — triggers on `v*` tags:
- Full test suite + pip-audit vulnerability scan.
- pip-licenses SBOM generation.
- Builds wheel + sdist; signs with Sigstore keyless signing.
- Creates a GitHub Release with signed artifacts attached.

---

## 10. Security Hardening Checklist (Production)

- [ ] Set `AGENT_GATEWAY_ADMIN_PASS` from a secrets manager — never hardcode.
- [ ] Use Vault for provider API keys (`VAULT_ADDR` + `VAULT_TOKEN`).
- [ ] Run gateway behind a reverse proxy (nginx / Caddy) with TLS.
- [ ] Set `network_disabled=True` for Docker sandbox containers.
- [ ] Restrict sandbox memory (`SANDBOX_DOCKER_MEMORY=64m`) and CPUs.
- [ ] Enable log aggregation for `audit.log` (Splunk / Loki / CloudWatch).
- [ ] Rotate admin tokens regularly; review `revoked_tokens.json`.
- [ ] Review SBOM and pip-audit outputs from CI artifacts before each release.
- [ ] Set `AGENT_GATEWAY_APPROVAL_ALERT_THRESHOLD` to receive queue-spike notifications.

---

## 11. Building the Electron Desktop Installer

### Windows installer (.exe via NSIS)

```powershell
cd browser-shell
npm install
node generate-icon.js    # one-time placeholder icon
npm run build
# → dist/Intelli-Setup-0.1.0.exe
```

### Linux (.deb + AppImage)

```bash
cd browser-shell
npm run build:linux
# → dist/intelli-browser_0.1.0_amd64.deb
# → dist/intelli-browser-0.1.0.AppImage
```

Replace `assets/icon.ico` with a proper multi-resolution icon before release.
