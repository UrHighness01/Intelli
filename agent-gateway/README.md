# Agent Gateway Prototype

Minimal prototype for the Intelli Agent Gateway. Implements a small local HTTP API that validates agent tool-call payloads against a JSON schema and provides a stubbed tool proxy endpoint.

Run locally for development and testing.

Quickstart

1. Create a virtualenv and install deps:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run the app:

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8080
```

Tab Snapshot Preview UI: http://127.0.0.1:8080/ui/tab_permission.html

Admin key and audit log
-----------------------

Set `AGENT_GATEWAY_ADMIN_KEY` to protect approval and redaction endpoints. If unset, the default development key is `dev-key`.

Audit entries are appended to `agent-gateway/audit.log` and redaction rules are persisted in `agent-gateway/redaction_rules.json`.

Authentication & admin login

The gateway now uses a minimal RBAC system. Create a default admin by setting the environment variable `AGENT_GATEWAY_ADMIN_PASSWORD` before starting the gateway; the startup will create an `admin` user if no users exist. Login via POST `/admin/login` with JSON `{ "username": "admin", "password": "<password>" }` to receive a Bearer token. Use `Authorization: Bearer <token>` for admin endpoints like approvals and redaction rules.

Sandbox proxy scaffold

Tokens and refresh
------------------

Login returns both an access token and a refresh token: `{"token": "<access>", "refresh_token": "<refresh>"}`. Access tokens expire (default 1 hour). Use `POST /admin/refresh` with `{ "refresh_token": "..." }` to obtain a new access token. Revoke tokens via `POST /admin/revoke` with JSON `{ "token": "..." }` (requires an admin access token).

Password storage
----------------

Passwords (hashes + salts) are stored in the OS keyring when available via the `keyring` library under service name `intelli-agent-gateway-users`. If the host keyring is unavailable the gateway falls back to storing the password hash in `agent-gateway/users.json` (less secure). For production, configure a system keyring or external secret manager.

----------------------

The repository includes a minimal sandbox proxy scaffold at `agent-gateway/sandbox/proxy.py`.
This scaffold implements a strict whitelist and returns controlled responses. It is
intended as a safe starting point — the production sandbox should run as a separate
process with OS-level restrictions (namespaces, seccomp, cgroups) or in a dedicated
VM/container.

3. Run tests:

```powershell
pytest -q
```

Notes
- This is a minimal scaffold: validation and proxying are intentionally simple and safe (no execution of arbitrary code).
