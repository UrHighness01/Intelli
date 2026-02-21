from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json
from jsonschema import validate, ValidationError
from pathlib import Path
from supervisor import Supervisor, load_schema_from_file
from tab_bridge import TabContextBridge
import os
from datetime import datetime
import auth

app = FastAPI(title="Intelli Agent Gateway (prototype)")

SCHEMA_PATH = Path(__file__).with_name("tool_schema.json")
RULES_PATH = Path(__file__).with_name('redaction_rules.json')
AUDIT_PATH = Path(__file__).with_name('audit.log')
TOOL_SCHEMA = load_schema_from_file(SCHEMA_PATH)
supervisor = Supervisor(TOOL_SCHEMA)
# Tab context bridge instance
tab_bridge = TabContextBridge()

# In-memory redaction rules (per-origin)
_redaction_rules = {}
def _load_rules():
    global _redaction_rules
    try:
        if RULES_PATH.exists():
            with RULES_PATH.open('r', encoding='utf-8') as f:
                _redaction_rules = {k: set(v) for k, v in json.load(f).items()}
        else:
            _redaction_rules = {}
    except Exception:
        _redaction_rules = {}

def _save_rules():
    try:
        serial = {k: list(v) for k, v in _redaction_rules.items()}
        with RULES_PATH.open('w', encoding='utf-8') as f:
            json.dump(serial, f, indent=2)
    except Exception:
        pass

def _audit(event: str, details: dict, actor: str = None):
    try:
        entry = {'ts': datetime.utcnow().isoformat() + 'Z', 'event': event, 'actor': actor, 'details': details}
        with AUDIT_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

# load persisted rules on startup
_load_rules()


def _require_admin_token(request: Request):
    # Expect header Authorization: Bearer <token>
    authh = request.headers.get('authorization') or request.headers.get('Authorization')
    if not authh:
        raise HTTPException(status_code=401, detail='missing authorization')
    parts = authh.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(status_code=401, detail='invalid authorization')
    token = parts[1]
    if not auth.check_role(token, 'admin'):
        raise HTTPException(status_code=403, detail='forbidden')
    return token

# Serve the simple UI for approvals
UI_DIR = Path(__file__).with_name("ui")
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


class ToolCall(BaseModel):
    tool: str
    args: dict


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/validate")
def validate_payload(payload: dict):
    try:
        validate(instance=payload, schema=TOOL_SCHEMA)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"schema validation failed: {e.message}")
    return {"valid": True}


@app.post("/tools/call")
def tool_call(call: ToolCall):
    # Defensive payload extraction (Pydantic v2 compatibility)
    payload = call.model_dump() if hasattr(call, "model_dump") else call.dict()

    result = supervisor.process_call(payload)
    # If validation error, return structured 400
    if result.get('status') == 'validation_error':
        raise HTTPException(status_code=400, detail=result)

    if result.get("status") == "pending_approval":
        # HTTP 202 Accepted
        return {"status": "pending_approval", "id": result.get("id")}

    # Stubbed execution for accepted calls
    return {"tool": result.get("tool"), "args": result.get("args"), "status": "stubbed", "message": result.get("message")}


@app.get("/approvals")
def list_approvals():
    pending = supervisor.queue.list_pending()
    return {"pending": pending}


@app.get("/approvals/{req_id}")
def get_approval(req_id: int):
    status = supervisor.queue.status(req_id)
    if not status:
        raise HTTPException(status_code=404, detail="request not found")
    return status


@app.post("/approvals/{req_id}/approve")
def approve(req_id: int, request: Request):
    # require admin (Bearer token)
    token = _require_admin_token(request)
    ok = supervisor.queue.approve(req_id)
    if not ok:
        raise HTTPException(status_code=404, detail="request not found")
    _audit('approve', {'id': req_id}, actor=(token[:6] + '...' if token else None))
    return {"status": "approved", "id": req_id}


@app.post("/approvals/{req_id}/reject")
def reject(req_id: int, request: Request):
    token = _require_admin_token(request)
    ok = supervisor.queue.reject(req_id)
    if not ok:
        raise HTTPException(status_code=404, detail="request not found")
    _audit('reject', {'id': req_id}, actor=(token[:6] + '...' if token else None))
    return {"status": "rejected", "id": req_id}


@app.post('/tab/preview')
def tab_preview(payload: dict):
    """Accepts {'html': str, 'url': str, 'selected_text': str (optional)} and returns a sanitized snapshot."""
    html = payload.get('html', '')
    url = payload.get('url', '')
    selected = payload.get('selected_text')
    snap = tab_bridge.snapshot(html, url, selected)
    # Apply simple redaction rules if present for origin
    origin = url
    rules = _redaction_rules.get(origin, {})
    # rules can be a set of input names to redact
    if rules:
        for inp in snap.get('inputs', []):
            if inp.get('name') in rules:
                inp['value'] = '[REDACTED]'
    return snap


@app.post('/admin/login')
def admin_login(payload: dict):
    username = payload.get('username')
    password = payload.get('password')
    if not username or not password:
        raise HTTPException(status_code=400, detail='missing username/password')
    t = auth.authenticate_user(username, password)
    if not t:
        raise HTTPException(status_code=401, detail='invalid credentials')
    # t contains access_token and refresh_token
    return {'token': t['access_token'], 'refresh_token': t['refresh_token']}



@app.post('/admin/refresh')
def admin_refresh(payload: dict):
    rt = payload.get('refresh_token')
    if not rt:
        raise HTTPException(status_code=400, detail='missing refresh_token')
    new_at = auth.refresh_access_token(rt)
    if not new_at:
        raise HTTPException(status_code=401, detail='invalid or expired refresh token')
    return {'token': new_at}


@app.post('/admin/revoke')
def admin_revoke(payload: dict, request: Request):
    # require admin access token to perform revocation
    _require_admin_token(request)
    token = payload.get('token')
    if not token:
        raise HTTPException(status_code=400, detail='missing token')
    ok = auth.revoke_token(token)
    if not ok:
        raise HTTPException(status_code=404, detail='token not found')
    return {'revoked': token}


@app.get('/tab/redaction-rules')
def list_redaction_rules(origin: str):
    return {origin: list(_redaction_rules.get(origin, set()))}


@app.post('/tab/redaction-rules')
def set_redaction_rules(payload: dict, request: Request):
    # admin required (Bearer token)
    token = _require_admin_token(request)

    origin = payload.get('origin')
    fields = payload.get('fields', [])
    if not origin:
        raise HTTPException(status_code=400, detail='origin required')
    _redaction_rules[origin] = set(fields)
    _save_rules()
    _audit('set_redaction_rules', {'origin': origin, 'fields': list(_redaction_rules[origin])}, actor=(token[:6] + '...' if token else None))
    return {'origin': origin, 'fields': list(_redaction_rules[origin])}
