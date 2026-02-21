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

3. Run tests:

```powershell
pytest -q
```

Notes
- This is a minimal scaffold: validation and proxying are intentionally simple and safe (no execution of arbitrary code).
