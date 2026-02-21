from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel
import json
from jsonschema import validate, ValidationError
from pathlib import Path
from supervisor import Supervisor, load_schema_from_file, compute_risk
from tab_bridge import TabContextBridge
import os
from datetime import datetime, timezone
import auth
from sandbox.manager import WorkerManager
from sandbox.pool import get_pool
import metrics as _metrics
import time
import asyncio
import threading
from rate_limit import rate_limiter, check_user_rate_limit
from providers.adapters import get_adapter, available_providers
from providers.provider_adapter import ProviderKeyStore
from providers.key_rotation import store_key_with_ttl, rotate_key, get_key_metadata, list_expiring
from tools.capability import CapabilityVerifier, _MANIFEST_DIR, ToolManifest
import consent_log as _consent
import agent_memory as _agent_memory
import content_filter as _content_filter
import rate_limit as _rate_limit
import webhooks as _webhooks
import scheduler as _scheduler

app = FastAPI(title="Intelli Agent Gateway (prototype)")

SCHEMA_PATH = Path(__file__).with_name("tool_schema.json")
RULES_PATH = Path(__file__).with_name('redaction_rules.json')
AUDIT_PATH = Path(__file__).with_name('audit.log')
TOOL_SCHEMA = load_schema_from_file(SCHEMA_PATH)
supervisor = Supervisor(TOOL_SCHEMA)
# Wire the scheduler to execute tool calls via the supervisor
_scheduler.set_executor(supervisor.process_call)
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
        entry = {'ts': datetime.now(timezone.utc).isoformat(), 'event': event, 'actor': actor, 'details': details}
        with AUDIT_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

# load persisted rules on startup
_load_rules()

# ---------------------------------------------------------------------------
# Kill-switch: when set all /tools/call requests are immediately rejected
# ---------------------------------------------------------------------------
_kill_switch = threading.Event()
_kill_switch_reason: str = ''

# startup time for basic metrics
_start_time = datetime.now(timezone.utc)

# Worker manager (checks bundled or env-provided worker)
_worker_path = os.environ.get('SANDBOX_WORKER_PATH')
_worker_manager = WorkerManager(worker_path=_worker_path) if _worker_path or True else None


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


def _actor(token: str | None) -> str | None:
    """Resolve the authenticated username for an admin token (used in audit records)."""
    if not token:
        return None
    user = auth.get_user_for_token(token)
    return user['username'] if user else None


def _get_authenticated_user(request: Request):
    """Return the authenticated user dict, or raise HTTP 401/403."""
    authh = request.headers.get('authorization') or request.headers.get('Authorization')
    if not authh:
        return None
    parts = authh.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None
    return auth.get_user_for_token(parts[1])

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


@app.get('/health/worker')
def health_worker():
    """Check the sandbox worker health (noop call)."""
    try:
        ok = _worker_manager.check_health()
    except Exception:
        ok = False
    return {"worker_healthy": ok}


@app.get('/metrics')
def metrics_endpoint():
    """Prometheus text-format metrics."""
    try:
        worker_ok = _worker_manager.check_health()
    except Exception:
        worker_ok = False
    pool_h = get_pool().health()
    _metrics.gauge('worker_healthy', 1.0 if worker_ok else 0.0)
    _metrics.gauge('worker_pool_alive', float(pool_h['alive']))
    _metrics.gauge('worker_pool_size', float(pool_h['size']))
    text = _metrics.export_prometheus()
    return PlainTextResponse(content=text, media_type='text/plain; version=0.0.4')


@app.get('/admin/audit')
def audit_export(
    request: Request,
    tail: int = 200,
    actor: str = '',
    action: str = '',
    since: str = '',
    until: str = '',
):
    """Export audit log entries with optional server-side filtering.

    Query params:
      - ``tail``   – maximum entries to read from the file (default 200)
      - ``actor``  – substring match on the ``actor`` field (case-insensitive)
      - ``action`` – substring match on the ``event`` field (case-insensitive)
      - ``since``  – ISO-8601 datetime; exclude entries before this timestamp
      - ``until``  – ISO-8601 datetime; exclude entries after this timestamp

    Requires admin Bearer token.
    """
    _require_admin_token(request)
    lines = []
    try:
        with AUDIT_PATH.open('r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass
    lines = lines[-tail:]

    # Parse optional datetime bounds
    from datetime import datetime, timezone as _tz
    since_dt = None
    until_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
        except ValueError:
            raise HTTPException(status_code=400, detail=f'Invalid since datetime: {since!r}')
    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace('Z', '+00:00'))
        except ValueError:
            raise HTTPException(status_code=400, detail=f'Invalid until datetime: {until!r}')

    actor_f  = actor.lower()  if actor  else ''
    action_f = action.lower() if action else ''

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            entry = {'raw': line}
        # actor filter
        if actor_f and actor_f not in str(entry.get('actor') or '').lower():
            continue
        # action / event filter
        if action_f and action_f not in str(entry.get('event') or '').lower():
            continue
        # date-range filters
        if since_dt or until_dt:
            ts_raw = str(entry.get('ts') or '')
            try:
                entry_dt = datetime.fromisoformat(ts_raw.replace('Z', '+00:00'))
                if since_dt and entry_dt < since_dt:
                    continue
                if until_dt and entry_dt > until_dt:
                    continue
            except ValueError:
                pass  # entries with unparseable timestamps pass through
        entries.append(entry)
    return {'count': len(entries), 'entries': entries}


@app.get('/admin/audit/export.csv')
def audit_export_csv(
    request: Request,
    tail: int = 1000,
    actor: str = '',
    action: str = '',
    since: str = '',
    until: str = '',
):
    """Download audit entries as a CSV file.  Accepts the same filters as
    ``GET /admin/audit``.  Admin Bearer required."""
    import csv
    import io
    _require_admin_token(request)
    # Reuse the JSON endpoint logic by calling the filter inline
    data = audit_export(
        request=request, tail=tail, actor=actor, action=action,
        since=since, until=until,
    )
    entries = data['entries']
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['ts', 'event', 'actor', 'details'])
    for e in entries:
        writer.writerow([
            e.get('ts', ''),
            e.get('event', e.get('raw', '')),
            e.get('actor', ''),
            json.dumps(e.get('details', {})) if 'details' in e else '',
        ])
    content = buf.getvalue()
    from starlette.responses import Response
    return Response(
        content=content,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="audit.csv"'},
    )


_SSE_POLL_INTERVAL = float(os.environ.get('AGENT_GATEWAY_SSE_POLL_INTERVAL', '2.0'))


@app.get('/approvals/stream')
async def approvals_stream(request: Request):
    """SSE stream of approval queue changes.  Polls every 2 s."""
    async def event_generator():
        # Yield a keepalive comment immediately so the HTTP response is delivered.
        yield ': keepalive\n\n'
        last_ids: set = set()
        while True:
            if await request.is_disconnected():
                break
            # list_pending() returns {id: {...}, ...} — extract ids from keys
            pending_dict = supervisor.queue.list_pending()
            current_ids = {str(k) for k in pending_dict}
            new_ids = current_ids - last_ids
            if new_ids:
                # Convert to a serialisable list for the SSE payload
                pending_list = [
                    {'id': k, **v} for k, v in pending_dict.items()
                ]
                data = json.dumps({'pending': pending_list})
                yield f'event: approval_update\ndata: {data}\n\n'
                last_ids = current_ids
            await asyncio.sleep(_SSE_POLL_INTERVAL)
    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.post("/validate")
def validate_payload(payload: dict, _rl=Depends(rate_limiter)):
    try:
        validate(instance=payload, schema=TOOL_SCHEMA)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"schema validation failed: {e.message}")
    return {"valid": True}


@app.post("/tools/call")
def tool_call(call: ToolCall, request: Request, _rl=Depends(rate_limiter)):
    # ---- Kill-switch check ------------------------------------------------
    if _kill_switch.is_set():
        raise HTTPException(
            status_code=503,
            detail={'error': 'gateway kill-switch is active', 'reason': _kill_switch_reason},
        )

    # ---- Per-user scoped tool permissions ---------------------------------
    caller = _get_authenticated_user(request)
    if caller is not None:
        # Per-user rate limit (separate quota per authenticated username)
        check_user_rate_limit(caller['username'])
        allowed = caller.get('allowed_tools')  # None → unrestricted
        if allowed is not None and call.tool not in allowed:
            _metrics.inc('tool_permission_denied_total', labels={'tool': call.tool})
            raise HTTPException(
                status_code=403,
                detail={'status': 'tool_not_permitted', 'tool': call.tool},
            )

    # ---- Content policy enforcement -----------------------------------
    _content_filter.check(call.args)

    _metrics.inc('tool_calls_total', labels={'tool': call.tool})
    # Defensive payload extraction (Pydantic v2 compatibility)
    payload = call.model_dump() if hasattr(call, "model_dump") else call.dict()

    t0 = time.time()
    result = supervisor.process_call(payload)
    _metrics.observe('tool_call_duration_seconds', time.time() - t0, labels={'tool': call.tool})

    # If validation error, return structured 400
    if result.get('status') == 'validation_error':
        _metrics.inc('tool_validation_errors_total', labels={'tool': call.tool})
        raise HTTPException(status_code=400, detail=result)

    # If capability was denied, return 403 Forbidden
    if result.get('status') == 'capability_denied':
        _metrics.inc('capability_denied_total', labels={'tool': call.tool})
        raise HTTPException(status_code=403, detail=result)

    if result.get("status") == "pending_approval":
        _metrics.inc('approvals_queued_total')
        _webhooks.fire_webhooks('approval.created', {'approval_id': result.get('id'), 'tool': call.tool})
        # HTTP 202 Accepted
        return {"status": "pending_approval", "id": result.get("id")}

    # Stubbed execution for accepted calls
    return {"tool": result.get("tool"), "args": result.get("args"), "status": "stubbed", "message": result.get("message"), "risk": result.get("risk")}


@app.get('/tools/capabilities')
def list_tool_capabilities():
    """List all tools that have capability manifests, with their declared capabilities."""
    if not _MANIFEST_DIR.exists():
        return {'tools': []}
    tools = []
    for f in sorted(_MANIFEST_DIR.rglob('*.json')):
        try:
            m = ToolManifest(json.loads(f.read_text(encoding='utf-8')))
            tools.append({
                'tool': m.tool,
                'display_name': m.display_name,
                'description': m.description,
                'required_capabilities': sorted(m.required),
                'optional_capabilities': sorted(m.optional),
                'risk_level': m.risk_level,
                'requires_approval': m.requires_approval,
            })
        except Exception:
            continue
    return {'tools': tools}



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
    _audit('approve', {'id': req_id}, actor=_actor(token))
    _webhooks.fire_webhooks('approval.approved', {'approval_id': req_id})
    return {"status": "approved", "id": req_id}


@app.post("/approvals/{req_id}/reject")
def reject(req_id: int, request: Request):
    token = _require_admin_token(request)
    ok = supervisor.queue.reject(req_id)
    if not ok:
        raise HTTPException(status_code=404, detail="request not found")
    _audit('reject', {'id': req_id}, actor=_actor(token))
    _webhooks.fire_webhooks('approval.rejected', {'approval_id': req_id})
    return {"status": "rejected", "id": req_id}


@app.post('/tab/preview')
def tab_preview(payload: dict, request: Request):
    """Accepts {'html': str, 'url': str, 'selected_text': str (optional)} and returns a sanitized snapshot."""
    html = str(payload.get('html') or '')
    url = str(payload.get('url') or '')
    selected = payload.get('selected_text')
    snap = tab_bridge.snapshot(html, url, selected)
    # Apply simple redaction rules if present for origin
    origin = url
    rules = _redaction_rules.get(origin, {})
    redacted_names: list = []
    # rules can be a set of input names to redact
    if rules:
        for inp in snap.get('inputs', []):
            if inp.get('name') in rules:
                inp['value'] = '[REDACTED]'
                redacted_names.append(inp.get('name', ''))

    # Determine actor from optional Bearer token
    actor: str | None = None
    authh = request.headers.get('authorization') or request.headers.get('Authorization')
    if authh and authh.lower().startswith('bearer '):
        tok = authh.split(None, 1)[1]
        actor = tok[:6] + '...' if tok else None

    # Log consent/context sharing event
    _consent.log_context_share(url=url, origin=origin, snapshot=snap,
                                actor=actor, redacted_fields=redacted_names)
    return snap


@app.post('/admin/login')
def admin_login(payload: dict, _rl=Depends(rate_limiter)):
    username = payload.get('username')
    password = payload.get('password')
    if not username or not password:
        raise HTTPException(status_code=400, detail='missing username/password')
    # Lazy creation in case env var was set after module import (e.g. in tests)
    auth._ensure_default_admin()
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
    _audit('set_redaction_rules', {'origin': origin, 'fields': list(_redaction_rules[origin])}, actor=_actor(token))
    return {'origin': origin, 'fields': list(_redaction_rules[origin])}


# ---------------------------------------------------------------------------
# Provider key management
# ---------------------------------------------------------------------------

@app.get('/providers')
def list_providers(request: Request):
    """List all known provider names and which are currently configured.
    Admin auth required.
    """
    _require_admin_token(request)
    known = ['openai', 'anthropic', 'openrouter', 'ollama']
    configured = available_providers()
    return {
        'providers': [
            {'name': p, 'configured': p in configured}
            for p in known
        ]
    }


@app.post('/admin/providers/{provider}/key')
def set_provider_key(provider: str, payload: dict, request: Request):
    """Store an API key for the given provider.  Admin auth required.

    Body: {"key": "<api-key>", "ttl_days": 90}
    ttl_days defaults to AGENT_GATEWAY_KEY_DEFAULT_TTL_DAYS (90).
    Set to 0 for no expiry.
    """
    token = _require_admin_token(request)
    key = payload.get('key', '').strip()
    if not key:
        raise HTTPException(status_code=400, detail='key is required')
    ttl = payload.get('ttl_days')
    ttl_days = int(ttl) if ttl is not None else None
    meta = store_key_with_ttl(provider, key, ttl_days=ttl_days if ttl_days != 0 else None)
    _audit('set_provider_key', {'provider': provider, 'expires_at': meta.expires_at},
           actor=_actor(token))
    return {'provider': provider, 'status': 'stored', 'expires_at': meta.expires_at}


@app.post('/admin/providers/{provider}/key/rotate')
def rotate_provider_key(provider: str, payload: dict, request: Request):
    """Replace the active API key with a new one (key rotation).  Admin auth required.

    Body: {"key": "<new-api-key>", "ttl_days": 90}
    """
    token = _require_admin_token(request)
    new_key = payload.get('key', '').strip()
    if not new_key:
        raise HTTPException(status_code=400, detail='key is required')
    ttl = payload.get('ttl_days')
    ttl_days = int(ttl) if ttl is not None else None
    meta = rotate_key(provider, new_key, ttl_days=ttl_days if ttl_days != 0 else None)
    _audit('rotate_provider_key', {'provider': provider, 'expires_at': meta.expires_at},
           actor=_actor(token))
    return {'provider': provider, 'status': 'rotated', 'last_rotated': meta.last_rotated, 'expires_at': meta.expires_at}


@app.get('/admin/providers/{provider}/key/status')
def provider_key_status(provider: str, request: Request):
    """Check whether a key exists and its expiry status.  Admin auth required."""
    _require_admin_token(request)
    val = ProviderKeyStore.get_key(provider)
    meta = get_key_metadata(provider)
    result: dict = {'provider': provider, 'configured': bool(val)}
    if meta:
        result['expires_at'] = meta.expires_at
        result['is_expired'] = meta.is_expired()
        result['days_until_expiry'] = meta.days_until_expiry()
        result['last_rotated'] = meta.last_rotated
    return result


@app.get('/admin/providers/{provider}/key/expiry')
def provider_key_expiry(provider: str, request: Request):
    """Return full TTL metadata for a provider key.  Admin auth required.

    Returns 404 if no TTL metadata is found (e.g. key was stored without a TTL).
    """
    _require_admin_token(request)
    meta = get_key_metadata(provider)
    if meta is None:
        raise HTTPException(status_code=404, detail=f'no TTL metadata found for provider {provider!r}')
    return {
        'provider': provider,
        'set_at': meta.set_at,
        'expires_at': meta.expires_at,
        'last_rotated': meta.last_rotated,
        'is_expired': meta.is_expired(),
        'days_until_expiry': meta.days_until_expiry(),
    }


@app.get('/admin/providers/expiring')
def expiring_keys(request: Request, within_days: float = 7.0):
    """List provider keys that will expire within *within_days* days.  Admin auth required."""
    _require_admin_token(request)
    expiring = list_expiring(within_days=within_days)
    return {'expiring': [m.to_dict() for m in expiring]}


@app.delete('/admin/providers/{provider}/key')
def delete_provider_key(provider: str, request: Request):
    """Remove the stored API key for the given provider.  Admin auth required."""
    token = _require_admin_token(request)
    # Store an empty string to effectively clear it
    ProviderKeyStore.set_key(provider, '')
    _audit('delete_provider_key', {'provider': provider}, actor=_actor(token))
    return {'provider': provider, 'status': 'deleted'}


# ---------------------------------------------------------------------------
# Provider health checks
# ---------------------------------------------------------------------------

@app.get('/admin/providers/{provider}/health')
def provider_health(provider: str, request: Request):
    """Check whether a provider key is configured and the adapter reports availability.
    Admin auth required.

    Returns:
        status: 'ok' | 'no_key' | 'unavailable'
        configured: whether a non-empty key is stored
    """
    _require_admin_token(request)
    try:
        adapter = get_adapter(provider)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'unknown provider: {provider}') from exc
    key = ProviderKeyStore.get_key(provider)
    configured = bool(key)
    available = adapter.is_available()
    if not configured:
        status = 'no_key'
    elif not available:
        status = 'unavailable'
    else:
        status = 'ok'
    return {
        'provider': provider,
        'status': status,
        'configured': configured,
        'available': available,
    }


# ---------------------------------------------------------------------------
# Rate-limit admin API
# ---------------------------------------------------------------------------

@app.get('/admin/rate-limits')
def get_rate_limits(request: Request):
    """Return current rate-limit configuration and a live usage snapshot.
    Admin auth required.
    """
    _require_admin_token(request)
    return {
        'config': _rate_limit.get_config(),
        'usage': _rate_limit.usage_snapshot(),
    }


class RateLimitUpdate(BaseModel):
    enabled: bool | None = None
    max_requests: int | None = None
    window_seconds: float | None = None
    burst: int | None = None
    user_max_requests: int | None = None
    user_window_seconds: float | None = None


@app.put('/admin/rate-limits')
def update_rate_limits(body: RateLimitUpdate, request: Request):
    """Update rate-limit settings at runtime (no restart required).
    Admin auth required.
    """
    token = _require_admin_token(request)
    try:
        cfg = _rate_limit.update_config(
            max_requests=body.max_requests,
            window_seconds=body.window_seconds,
            burst=body.burst,
            enabled=body.enabled,
            user_max_requests=body.user_max_requests,
            user_window_seconds=body.user_window_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit('update_rate_limits', {'new_config': cfg}, actor=_actor(token))
    return {'updated': True, 'config': cfg}


@app.delete('/admin/rate-limits/clients/{client_key}')
def reset_rate_limit_client(client_key: str, request: Request):
    """Reset the sliding-window state for a specific client key (IP).
    Admin auth required.
    """
    _require_admin_token(request)
    _rate_limit.reset_client(client_key)
    return {'reset': True, 'client': client_key}


@app.delete('/admin/rate-limits/users/{username}')
def reset_rate_limit_user(username: str, request: Request):
    """Reset the per-user sliding-window state for *username*.
    Admin auth required.
    """
    _require_admin_token(request)
    _rate_limit.reset_user(username)
    return {'reset': True, 'user': username}


# ---------------------------------------------------------------------------
# Webhook registry
# ---------------------------------------------------------------------------

class WebhookCreate(BaseModel):
    url: str
    events: list[str] | None = None
    secret: str = ''  # optional HMAC signing secret; empty = no signing


@app.post('/admin/webhooks', status_code=201)
def create_webhook(body: WebhookCreate, request: Request):
    """Register a webhook URL to receive approval events.
    Admin auth required.

    Body: {"url": "https://...", "events": ["approval.created", ...]}
    events defaults to all three approval events if omitted.
    """
    token = _require_admin_token(request)
    try:
        hook = _webhooks.register_webhook(body.url, body.events, secret=body.secret)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit('register_webhook', {'id': hook['id'], 'url': body.url, 'events': hook['events'],
                                'signed': bool(body.secret)},
           actor=_actor(token))
    return hook


@app.get('/admin/webhooks')
def list_webhooks_endpoint(request: Request):
    """List all registered webhooks.  Admin auth required."""
    _require_admin_token(request)
    return {'webhooks': _webhooks.list_webhooks()}


@app.get('/admin/webhooks/{hook_id}')
def get_webhook_endpoint(hook_id: str, request: Request):
    """Return a single webhook.  Admin auth required."""
    _require_admin_token(request)
    hook = _webhooks.get_webhook(hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail='webhook not found')
    return hook


@app.delete('/admin/webhooks/{hook_id}')
def delete_webhook_endpoint(hook_id: str, request: Request):
    """Delete a registered webhook.  Admin auth required."""
    token = _require_admin_token(request)
    deleted = _webhooks.delete_webhook(hook_id)
    if not deleted:
        raise HTTPException(status_code=404, detail='webhook not found')
    _audit('delete_webhook', {'id': hook_id}, actor=_actor(token))
    return {'deleted': True, 'id': hook_id}


@app.get('/admin/webhooks/{hook_id}/deliveries')
def get_webhook_deliveries(hook_id: str, request: Request, limit: int = 50):
    """Return recent delivery records for a webhook (newest first).

    Admin auth required.  Returns up to *limit* records (max 100).
    Each record contains: timestamp, event, status ('ok'|'error'),
    status_code, and error (if any).
    """
    _require_admin_token(request)
    # 404 if the hook itself doesn't exist
    if _webhooks.get_webhook(hook_id) is None:
        raise HTTPException(status_code=404, detail='webhook not found')
    deliveries = _webhooks.get_deliveries(hook_id, limit=limit)
    return {'hook_id': hook_id, 'deliveries': deliveries, 'count': len(deliveries)}




class ChatRequest(BaseModel):
    provider: str
    messages: list
    model: str = ''
    temperature: float = 0.7
    max_tokens: int = 1024


@app.post('/chat/complete')
def chat_complete(req: ChatRequest, request: Request, _rl=Depends(rate_limiter)):
    """Proxy a chat-completion request to the configured provider.

    Requires a valid Bearer token (any authenticated user).
    Returns the provider's reply in a unified format.
    """
    authh = request.headers.get('authorization') or request.headers.get('Authorization')
    if not authh:
        raise HTTPException(status_code=401, detail='missing authorization')
    parts = authh.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(status_code=401, detail='invalid authorization')
    if not auth.get_user_for_token(parts[1]):
        raise HTTPException(status_code=401, detail='invalid or expired token')

    # Per-user rate limit for chat completions
    chat_user = auth.get_user_for_token(parts[1])
    if chat_user:
        check_user_rate_limit(chat_user['username'])

    # ---- Content policy enforcement -----------------------------------
    _content_filter.check([m.get('content', '') for m in req.messages])

    try:
        adapter = get_adapter(req.provider)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not adapter.is_available():
        raise HTTPException(
            status_code=503,
            detail=f'provider {req.provider!r} is not configured or unreachable',
        )

    kwargs: dict = {}
    if req.model:
        kwargs['model'] = req.model

    try:
        result = adapter.chat_complete(
            messages=req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            **kwargs,
        )
    except Exception as exc:
        _metrics.inc('provider_errors_total', labels={'provider': req.provider})
        raise HTTPException(status_code=502, detail=f'provider error: {exc}')

    _metrics.inc('provider_requests_total', labels={'provider': req.provider})
    return result


# ---------------------------------------------------------------------------
# Kill-switch (emergency stop)
# ---------------------------------------------------------------------------

@app.get('/admin/kill-switch')
def kill_switch_status(request: Request):
    """Return the current kill-switch state.  Admin auth required."""
    _require_admin_token(request)
    return {'active': _kill_switch.is_set(), 'reason': _kill_switch_reason}


@app.post('/admin/kill-switch')
def kill_switch_activate(payload: dict, request: Request):
    """Activate the kill-switch: all /tools/call requests will be rejected with 503.

    Body: {"reason": "incident response – CVE-2025-XXXX"}
    """
    global _kill_switch_reason
    token = _require_admin_token(request)
    _kill_switch_reason = payload.get('reason', 'kill-switch activated')
    _kill_switch.set()
    _audit('kill_switch_activate', {'reason': _kill_switch_reason},
           actor=_actor(token))
    return {'active': True, 'reason': _kill_switch_reason}


@app.delete('/admin/kill-switch')
def kill_switch_deactivate(request: Request):
    """Deactivate the kill-switch and resume normal tool-call processing.  Admin auth required."""
    global _kill_switch_reason
    token = _require_admin_token(request)
    _kill_switch.clear()
    prev_reason = _kill_switch_reason
    _kill_switch_reason = ''
    _audit('kill_switch_deactivate', {'previous_reason': prev_reason},
           actor=_actor(token))
    return {'active': False}


# ---------------------------------------------------------------------------
# Gateway status summary
# ---------------------------------------------------------------------------

_GATEWAY_VERSION = '0.1.0'


@app.get('/admin/status')
def gateway_status(request: Request):
    """Return a high-level operational summary of the gateway.  Admin auth required.

    Response fields:
      - ``version``              – gateway version string
      - ``uptime_seconds``       – seconds since process start
      - ``kill_switch_active``   – whether the emergency kill-switch is armed
      - ``kill_switch_reason``   – reason text if armed, else null
      - ``tool_calls_total``     – cumulative tool-call count from in-process Prometheus counter
      - ``pending_approvals``    – number of requests currently in the approval queue
      - ``scheduler_tasks``      – number of registered scheduler tasks
      - ``memory_agents``        – number of agent memory namespaces on disk
    """
    _require_admin_token(request)
    uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()
    pending = supervisor.queue.list_pending()
    tasks = _scheduler.list_tasks()
    agents = _agent_memory.list_agents()
    return {
        'version': _GATEWAY_VERSION,
        'uptime_seconds': round(uptime, 1),
        'kill_switch_active': _kill_switch.is_set(),
        'kill_switch_reason': _kill_switch_reason if _kill_switch.is_set() else None,
        'tool_calls_total': int(_metrics.get_counter('tool_calls_total')),
        'pending_approvals': len(pending),
        'scheduler_tasks': len(tasks),
        'memory_agents': len(agents),
    }


# ---------------------------------------------------------------------------
# Per-user scoped tool permissions
# ---------------------------------------------------------------------------

@app.get('/admin/users/{username}/permissions')
def get_user_permissions(username: str, request: Request):
    """Return the tool allow-list for *username*.  Admin auth required.

    ``allowed_tools: null`` means no restriction (all tools permitted).
    """
    _require_admin_token(request)
    allowed = auth.get_user_allowed_tools(username)
    return {'username': username, 'allowed_tools': allowed}


@app.put('/admin/users/{username}/permissions')
def set_user_permissions(username: str, payload: dict, request: Request):
    """Set (or clear) the tool allow-list for *username*.  Admin auth required.

    Body: {"allowed_tools": ["file.read", "noop"]}
    Pass ``allowed_tools: null`` or omit the key to remove the restriction.
    """
    token = _require_admin_token(request)
    tools = payload.get('allowed_tools')  # may be None
    ok = auth.set_user_allowed_tools(username, tools)
    if not ok:
        raise HTTPException(status_code=404, detail=f'user {username!r} not found')
    _audit('set_user_permissions', {'username': username, 'allowed_tools': tools},
           actor=_actor(token))
    return {'username': username, 'allowed_tools': tools}


# ---------------------------------------------------------------------------
# User management CRUD
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    username: str
    password: str
    roles: list = ['user']


class ChangePasswordRequest(BaseModel):
    new_password: str


@app.get('/admin/users')
def admin_list_users(request: Request):
    """Return a list of all users (no passwords).  Admin Bearer required."""
    _require_admin_token(request)
    return {'users': auth.list_users()}


@app.post('/admin/users', status_code=201)
def admin_create_user(payload: CreateUserRequest, request: Request):
    """Create a new user.  Admin Bearer required.

    Body: ``{"username": "...", "password": "...", "roles": ["user"]}``
    Returns 409 when the username already exists.
    """
    token = _require_admin_token(request)
    ok = auth.create_user(payload.username, payload.password, roles=payload.roles)
    if not ok:
        raise HTTPException(status_code=409, detail=f'user {payload.username!r} already exists')
    _audit('create_user', {'username': payload.username, 'roles': payload.roles},
           actor=_actor(token))
    return {'username': payload.username, 'roles': payload.roles}


@app.delete('/admin/users/{username}', status_code=200)
def admin_delete_user(username: str, request: Request):
    """Delete a user.  Admin Bearer required.

    Returns 404 if the user does not exist.
    Returns 403 when trying to delete the built-in ``admin`` account.
    """
    token = _require_admin_token(request)
    if username == 'admin':
        raise HTTPException(status_code=403, detail='cannot delete the built-in admin account')
    ok = auth.delete_user(username)
    if not ok:
        raise HTTPException(status_code=404, detail=f'user {username!r} not found')
    _audit('delete_user', {'username': username},
           actor=_actor(token))
    return {'deleted': username}


@app.post('/admin/users/{username}/password')
def admin_change_password(username: str, payload: ChangePasswordRequest, request: Request):
    """Update password for *username*.  Admin Bearer required.

    Body: ``{"new_password": "..."}``
    Returns 404 when the user does not exist.
    """
    token = _require_admin_token(request)
    ok = auth.change_password(username, payload.new_password)
    if not ok:
        raise HTTPException(status_code=404, detail=f'user {username!r} not found')
    _audit('change_password', {'username': username},
           actor=_actor(token))
    return {'username': username, 'password_changed': True}


# ---------------------------------------------------------------------------
# Consent / context-sharing timeline
# ---------------------------------------------------------------------------

@app.get('/consent/timeline')
def consent_timeline(request: Request, origin: str = '', limit: int = 100, actor: str = ''):
    """Return the context-sharing timeline.  Admin Bearer token required.

    Query params:
      origin  – filter to this exact origin (optional)
      limit   – max entries to return, newest first (default 100)
      actor   – filter by actor prefix (optional)
    """
    _require_admin_token(request)
    entries = _consent.get_timeline(
        origin=origin or None,
        limit=min(limit, 1000),
        actor=actor or None,
    )
    return {'count': len(entries), 'entries': entries}


@app.delete('/consent/timeline')
def consent_timeline_clear(request: Request, origin: str = ''):
    """Clear consent timeline entries.  Admin Bearer token required.

    Query param:
      origin  – if set, only remove entries for this origin; else clears all.
    """
    token = _require_admin_token(request)
    removed = _consent.clear_timeline(origin=origin or None)
    _audit('clear_consent_timeline', {'origin': origin or '*', 'removed': removed},
           actor=_actor(token))
    return {'removed': removed}


# ---------------------------------------------------------------------------
# GDPR / data-subject access & erasure endpoints
# ---------------------------------------------------------------------------

@app.get('/consent/export/{actor}')
def consent_export_actor(actor: str, request: Request):
    """Export all consent-timeline entries for *actor* (GDPR Art. 15 DSAR).

    Returns the complete, unbounded list of context-share events attributed to
    the given actor token prefix in chronological order.  Admin auth required.
    """
    _require_admin_token(request)
    entries = _consent.export_actor_data(actor)
    return {'actor': actor, 'count': len(entries), 'entries': entries}


# ---------------------------------------------------------------------------
# Agent memory CRUD
# ---------------------------------------------------------------------------

@app.get('/agents')
def agent_list_all(request: Request):
    """Return all agent IDs that have stored memory."""
    _require_admin_token(request)
    return {'agents': _agent_memory.list_agents()}


@app.get('/agents/{agent_id}/memory')
def agent_memory_list(agent_id: str, request: Request):
    """List all key-value pairs for an agent.  Admin auth required."""
    _require_admin_token(request)
    try:
        return {'agent_id': agent_id, 'memory': _agent_memory.memory_list(agent_id)}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get('/agents/{agent_id}/memory/{key}')
def agent_memory_get(agent_id: str, key: str, request: Request):
    """Retrieve a single memory value for an agent.  Admin auth required."""
    _require_admin_token(request)
    try:
        meta = _agent_memory.memory_get_meta(agent_id, key)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if meta is None:
        raise HTTPException(status_code=404, detail='key not found')
    return {'agent_id': agent_id, 'key': key, 'value': meta['value'], 'expires_at': meta['expires_at']}


class MemoryUpsertRequest(BaseModel):
    key: str
    value: object
    ttl_seconds: float | None = None


@app.post('/agents/{agent_id}/memory')
def agent_memory_set(agent_id: str, body: MemoryUpsertRequest, request: Request):
    """Upsert a key-value pair in an agent's memory.  Admin auth required.

    Supply ``ttl_seconds`` to automatically expire the key after that duration.
    """
    _require_admin_token(request)
    try:
        _agent_memory.memory_set(agent_id, body.key, body.value, body.ttl_seconds)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {'agent_id': agent_id, 'key': body.key, 'stored': True, 'ttl_seconds': body.ttl_seconds}


@app.delete('/agents/{agent_id}/memory/{key}')
def agent_memory_delete_key(agent_id: str, key: str, request: Request):
    """Delete a single memory key for an agent.  Admin auth required."""
    _require_admin_token(request)
    try:
        deleted = _agent_memory.memory_delete(agent_id, key)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail='key not found')
    return {'agent_id': agent_id, 'key': key, 'deleted': True}


@app.delete('/agents/{agent_id}/memory')
def agent_memory_clear(agent_id: str, request: Request):
    """Delete ALL memory for an agent.  Admin auth required."""
    _require_admin_token(request)
    try:
        removed = _agent_memory.memory_clear(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {'agent_id': agent_id, 'cleared': removed}


@app.post('/agents/{agent_id}/memory/prune')
def agent_memory_prune(agent_id: str, request: Request):
    """Remove all expired (TTL-elapsed) keys for an agent.  Admin auth required."""
    _require_admin_token(request)
    try:
        pruned = _agent_memory.memory_prune(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {'agent_id': agent_id, 'pruned': pruned}


# ---------------------------------------------------------------------------
# Memory export / import (backup & recovery)
# ---------------------------------------------------------------------------

class MemoryImportRequest(BaseModel):
    agents: dict
    merge: bool = True


@app.get('/admin/memory/export')
def memory_export(request: Request):
    """Export a JSON snapshot of all agents' live memory.

    Admin auth required.  Returns::

        {
          "agents":      {"agent-id": {"key": value}, ...},
          "agent_count": N,
          "key_count":   M,
          "exported_at": "ISO-8601 timestamp"
        }
    """
    _require_admin_token(request)
    return _agent_memory.export_all()


@app.post('/admin/memory/import')
def memory_import(req: MemoryImportRequest, request: Request):
    """Restore agent memories from a backup snapshot.

    Admin auth required.  Body::

        {"agents": {"agent-id": {"key": value}, ...}, "merge": true}

    When *merge* is ``true`` (default), imported keys are merged into
    existing memory; keys not present in the backup are preserved.
    When ``false``, each imported agent's memory is **replaced**.
    """
    _require_admin_token(request)
    try:
        result = _agent_memory.import_all(req.agents, merge=req.merge)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    token = _require_admin_token(request)  # already validated above; second call is cheap
    _audit('memory_import', {
        'imported_agents': result['imported_agents'],
        'imported_keys': result['imported_keys'],
        'merge': req.merge,
    }, actor=_actor(token))
    return result




class ContentFilterRule(BaseModel):
    pattern: str
    mode: str = 'literal'
    label: str = ''


@app.get('/admin/content-filter/rules')
def content_filter_list_rules(request: Request):
    """List all active content-filter rules.  Admin auth required."""
    _require_admin_token(request)
    return {'rules': _content_filter.get_rules()}


@app.post('/admin/content-filter/rules')
def content_filter_add_rule(rule: ContentFilterRule, request: Request):
    """Add a new content-filter rule.  Admin auth required."""
    _require_admin_token(request)
    try:
        _content_filter.add_rule(rule.pattern, rule.mode, rule.label)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {'added': True, 'pattern': rule.pattern, 'mode': rule.mode}


@app.delete('/admin/content-filter/rules/{index}')
def content_filter_delete_rule(index: int, request: Request):
    """Remove the content-filter rule at *index*.  Admin auth required."""
    _require_admin_token(request)
    removed = _content_filter.delete_rule(index)
    if not removed:
        raise HTTPException(status_code=404, detail='rule index out of range')
    return {'deleted': True, 'index': index}


@app.post('/admin/content-filter/reload')
def content_filter_reload(request: Request):
    """Reload content-filter rules from disk and env.  Admin auth required."""
    _require_admin_token(request)
    count = _content_filter.reload()
    return {'reloaded': True, 'active_rules': count}


@app.delete('/consent/export/{actor}')
def consent_erase_actor(actor: str, request: Request):
    """Erase all consent-timeline data for *actor* (GDPR Art. 17 right to erasure).

    Permanently removes every timeline entry attributed to the given actor and
    returns the count of records deleted.  This operation is irreversible.
    Admin auth required.
    """
    token = _require_admin_token(request)
    removed = _consent.erase_actor_data(actor)
    _audit('erase_actor_consent_data', {'actor': actor, 'removed': removed},
           actor=_actor(token))
    return {'actor': actor, 'removed': removed}


# ---------------------------------------------------------------------------
# Agent task scheduler
# ---------------------------------------------------------------------------

class ScheduleCreate(BaseModel):
    name: str
    tool: str
    args: dict = {}
    interval_seconds: int
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = None
    args: dict | None = None
    interval_seconds: int | None = None
    enabled: bool | None = None


@app.get('/admin/schedule')
def schedule_list(request: Request):
    """Return all scheduled tasks.  Admin Bearer required."""
    _require_admin_token(request)
    return {'tasks': _scheduler.list_tasks()}


@app.post('/admin/schedule', status_code=201)
def schedule_create(payload: ScheduleCreate, request: Request):
    """Register a new recurring tool-call task.  Admin Bearer required.

    Body: ``{"name": "...", "tool": "...", "args": {...}, "interval_seconds": N}``
    Returns 422 for invalid input (empty name, interval < 1, etc.).
    """
    token = _require_admin_token(request)
    try:
        task = _scheduler.add_task(
            name=payload.name,
            tool=payload.tool,
            args=payload.args,
            interval_seconds=payload.interval_seconds,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit('create_scheduled_task', {'id': task['id'], 'name': task['name'], 'tool': task['tool']},
           actor=_actor(token))
    return task


@app.get('/admin/schedule/{task_id}')
def schedule_get(task_id: str, request: Request):
    """Return a single scheduled task by ID.  Admin Bearer required."""
    _require_admin_token(request)
    task = _scheduler.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='task not found')
    return task


@app.patch('/admin/schedule/{task_id}')
def schedule_patch(task_id: str, payload: SchedulePatch, request: Request):
    """Update mutable fields of a scheduled task.  Admin Bearer required.

    Accepts any subset of: ``name``, ``args``, ``interval_seconds``, ``enabled``.
    Returns 404 if the task does not exist, 422 for invalid values.
    """
    token = _require_admin_token(request)
    kwargs = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        task = _scheduler.update_task(task_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if task is None:
        raise HTTPException(status_code=404, detail='task not found')
    _audit('update_scheduled_task', {'id': task_id, 'changes': kwargs},
           actor=_actor(token))
    return task


@app.delete('/admin/schedule/{task_id}')
def schedule_delete(task_id: str, request: Request):
    """Delete a scheduled task.  Admin Bearer required."""
    token = _require_admin_token(request)
    ok = _scheduler.delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail='task not found')
    _audit('delete_scheduled_task', {'id': task_id},
           actor=(token[:6] + '...' if token else None))
    return {'deleted': task_id}


@app.post('/admin/schedule/{task_id}/trigger', status_code=202)
def schedule_trigger(task_id: str, request: Request):
    """Force a task to run on the next scheduler tick (sets next_run_at to now).

    Admin Bearer required.  Returns 404 if the task does not exist.
    """
    token = _require_admin_token(request)
    ok = _scheduler.trigger_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail='task not found')
    _audit('trigger_scheduled_task', {'id': task_id},
           actor=(token[:6] + '...' if token else None))
    return {'triggered': task_id}


@app.get('/admin/schedule/{task_id}/history')
def schedule_history(task_id: str, request: Request):
    """Return recent run history for a scheduled task (newest first, max 50).

    Admin Bearer required.  Returns 404 if the task does not exist.
    """
    _require_admin_token(request)
    history = _scheduler.get_history(task_id)
    if history is None:
        raise HTTPException(status_code=404, detail='task not found')
    return {'task_id': task_id, 'count': len(history), 'history': history}
