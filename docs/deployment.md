# Deployment Guide — Intelli Agent Gateway

---

## Prerequisites

- Python 3.11+
- (Optional) Docker for isolated sandbox execution
- (Optional) HashiCorp Vault for secrets management

---

## 1. Local Development

```bash
# Clone
git clone https://github.com/UrHighness01/Intelli.git
cd Intelli

# Create and activate venv
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r agent-gateway/requirements.txt

# Set admin credentials
$env:AGENT_GATEWAY_ADMIN_PASS = "changeme"   # PowerShell
# export AGENT_GATEWAY_ADMIN_PASS=changeme   # bash

# Start gateway
uvicorn agent_gateway.app:app --host 127.0.0.1 --port 8080 --reload
```

---

## 2. Running Tests

```bash
# All tests
python -m pytest -q

# Fast: sandbox-only tests (no gateway required)
python -m pytest agent-gateway/tests/test_sandbox_worker.py \
                 agent-gateway/tests/test_sandbox_manager.py -q

# With coverage report
pip install pytest-cov
python -m pytest --cov=agent-gateway --cov-report=term-missing -q
```

---

## 3. Docker Deployment

### Gateway container

```dockerfile
# Dockerfile.gateway
FROM python:3.11-slim
WORKDIR /app
COPY agent-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent-gateway/ .
COPY agent_gateway/ ../agent_gateway/
EXPOSE 8080
ENV PYTHONPATH=/app/..
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

### Docker Compose (gateway + Vault dev)

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

## 4. HashiCorp Vault Setup

```bash
# Enable KV v2
vault secrets enable -path=secret kv-v2

# Store an OpenAI key
vault kv put secret/intelli/providers/openai api_key=sk-...

# Read it back
vault kv get secret/intelli/providers/openai
```

Then set in environment:
```bash
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=<your-token>
```

---

## 5. CI/CD (GitHub Actions)

The workflow at `.github/workflows/ci.yml`:
- Sets `PYTHONPATH` to repo root.
- Exports `SANDBOX_WORKER_PATH` to bundled worker.
- Installs `agent-gateway/requirements.txt`.
- Starts the gateway via `uvicorn`.
- Runs `pytest -q agent-gateway`.
- Generates SBOM (`pip-licenses`) and audit (`pip-audit`) artifacts.

Trigger: push/PR to `main`.

---

## 6. Environment Variable Reference

See `docs/runbook.md` § 1 for the full table.

---

## 7. Security Hardening Checklist

- [ ] Set `AGENT_GATEWAY_ADMIN_PASS` from a secrets manager (not plaintext in shell).
- [ ] Use Vault for provider API keys (`VAULT_ADDR` + `VAULT_TOKEN`).
- [ ] Run gateway behind a reverse proxy (nginx/Caddy) with TLS.
- [ ] Set `network_disabled=True` for Docker sandbox containers.
- [ ] Restrict sandbox memory (`SANDBOX_DOCKER_MEMORY=64m`) and CPUs.
- [ ] Enable log aggregation for `audit.log`.
- [ ] Rotate admin tokens regularly and enable token expiry.
- [ ] Review SBOM and pip-audit outputs from CI artifacts.
