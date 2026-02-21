# Intelli — Next-gen AI-native Browser (prototype)

This repository contains an initial prototype and roadmap for "Intelli": a browser combining privacy-first design with native AI/agent integration, a Tab Context Bridge, an Agent Gateway, and supervisor validation.

Quick start (agent-gateway prototype):

1. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r agent-gateway/requirements.txt
```

2. Run tests:

```powershell
pytest -q agent-gateway
```

3. Run the agent gateway locally:

```powershell
uvicorn agent-gateway.app:app --reload --host 127.0.0.1 --port 8080
```

Project layout:
- `ROADMAP.md` — high-level roadmap and phases
- `ARCHITECTURE.md` — mermaid architecture diagram
- `THREAT_MODEL.md` — threat model and privacy controls
- `agent-gateway/` — prototype local agent gateway (FastAPI)

If you want, I can create a remote GitHub repo and push this workspace (you may need to provide a token), or I can give you the git commands to run locally and push.
