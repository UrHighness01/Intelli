from fastapi import FastAPI, HTTPException, Request, Depends, Query, UploadFile, File as FastAPIFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
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
import collections
from rate_limit import rate_limiter, check_user_rate_limit
from providers.adapters import get_adapter, available_providers, ProviderSettingsStore
from providers.provider_adapter import ProviderKeyStore
from providers.key_rotation import store_key_with_ttl, rotate_key, get_key_metadata, list_expiring
from tools.capability import CapabilityVerifier, _MANIFEST_DIR, ToolManifest
from tools.tool_runner import run_tool_loop, build_tool_system_block
import consent_log as _consent
import approval_gate as _approval_gate
import agent_memory as _agent_memory
import content_filter as _content_filter
import rate_limit as _rate_limit
import webhooks as _webhooks
import scheduler as _scheduler
import tab_snapshot as _tab_snapshot
import addons as _addons
import workspace_manager as _workspace
import compaction as _compaction
import canvas_manager as _canvas_mgr
import failover as _failover
from failover import FailoverAdapter as _FailoverAdapter
import memory_store as _memory
import personas as _personas
import sessions as _sessions
import watcher as _watcher
import mcp_client as _mcp
import notifier as _notifier
import notes as _notes
import credential_store as _cred
import a2a as _a2a
import plugin_loader as _plugins
_canvas = _canvas_mgr.get_canvas()

app = FastAPI(title="Intelli Agent Gateway (prototype)")

# ---------------------------------------------------------------------------
# CORS — restrict to localhost by default; override via env var
# ---------------------------------------------------------------------------
_cors_origins_raw = os.environ.get('AGENT_GATEWAY_CORS_ORIGINS', 'http://127.0.0.1:8080')
_cors_origins = [o.strip() for o in _cors_origins_raw.split(',') if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

SCHEMA_PATH = Path(__file__).with_name("tool_schema.json")
RULES_PATH = Path(__file__).with_name('redaction_rules.json')
AUDIT_PATH = Path(__file__).with_name('audit.log')

# ---------------------------------------------------------------------------
# Audit-log encryption (AES-256-GCM) — Item 13
# Set INTELLI_AUDIT_ENCRYPT_KEY to a 64-hex-char (32-byte) random key to
# enable at-rest encryption of every audit log line.
# Generate key: python -c "import secrets; print(secrets.token_hex(32))"
# ---------------------------------------------------------------------------
import base64 as _b64

def _audit_key() -> bytes | None:
    """Return 32-byte AES-256-GCM key from INTELLI_AUDIT_ENCRYPT_KEY, or None."""
    raw = os.environ.get('INTELLI_AUDIT_ENCRYPT_KEY', '').strip()
    if not raw:
        return None
    key = bytes.fromhex(raw)
    if len(key) != 32:
        raise ValueError(
            f'INTELLI_AUDIT_ENCRYPT_KEY must be 64 hex chars (32 bytes), got {len(key)}'
        )
    return key

def _encrypt_audit_line(line: str, key: bytes) -> str:
    """Encrypt a JSONL audit line with AES-256-GCM; return base64(nonce+ciphertext)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import secrets as _sec
    nonce = _sec.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, line.encode('utf-8'), None)
    return _b64.b64encode(nonce + ct).decode('ascii')

def _decrypt_audit_line(enc: str, key: bytes) -> str:
    """Decrypt a base64-encoded AES-256-GCM audit line; return plaintext JSON."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = _b64.b64decode(enc)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode('utf-8')

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
        json_line = json.dumps(entry, ensure_ascii=False)
        key = _audit_key()
        if key:
            json_line = _encrypt_audit_line(json_line, key)
        with AUDIT_PATH.open('a', encoding='utf-8') as f:
            f.write(json_line + "\n")
    except Exception:
        pass

# load persisted rules on startup
_load_rules()

# ---------------------------------------------------------------------------
# Kill-switch: when set all /tools/call requests are immediately rejected
# ---------------------------------------------------------------------------
_kill_switch = threading.Event()
_kill_switch_reason: str = ''

# ---------------------------------------------------------------------------
# Approval-queue depth alert + worker health + validation-error-rate alerts
# ---------------------------------------------------------------------------
_APPROVAL_ALERT_THRESHOLD: int = int(
    os.environ.get('AGENT_GATEWAY_APPROVAL_ALERT_THRESHOLD', '0')
)
_WORKER_CHECK_INTERVAL: float = float(
    os.environ.get('AGENT_GATEWAY_WORKER_CHECK_INTERVAL', '60')
)
_VALIDATION_ERR_WINDOW: float = float(
    os.environ.get('AGENT_GATEWAY_VALIDATION_ERR_WINDOW', '60')
)
_VALIDATION_ERR_THRESHOLD: int = int(
    os.environ.get('AGENT_GATEWAY_VALIDATION_ERR_THRESHOLD', '0')
)
# Runtime-mutable config (PUT /admin/alerts/config can override the env vars)
_alert_config: dict = {
    'approval_queue_threshold':    _APPROVAL_ALERT_THRESHOLD,
    'worker_check_interval_seconds': _WORKER_CHECK_INTERVAL,
    'validation_error_window_seconds': _VALIDATION_ERR_WINDOW,
    'validation_error_threshold':  _VALIDATION_ERR_THRESHOLD,
}
# Sliding window of recent tool-validation-error timestamps (thread-safe append/popleft)
_validation_error_times: collections.deque = collections.deque()
# Last known worker health state — used to detect health transitions
_worker_was_healthy: bool | None = None

# ---------------------------------------------------------------------------
# Approval timeout config (auto-reject pending after N seconds; 0 = disabled)
# ---------------------------------------------------------------------------
_APPROVAL_TIMEOUT: float = float(os.environ.get('AGENT_GATEWAY_APPROVAL_TIMEOUT', '0'))
_approvals_config: dict = {'timeout_seconds': _APPROVAL_TIMEOUT}


def _approval_timeout_reaper() -> None:
    """Daemon: scan pending approvals every 5 s; auto-reject stale ones."""
    while True:
        try:
            time.sleep(5)
            timeout = _approvals_config.get('timeout_seconds', 0)
            if timeout > 0:
                expired = supervisor.queue.expire_pending(timeout)
                for req_id in expired:
                    _audit('reject', {'id': req_id, 'reason': 'timeout'}, actor='system')
                    _webhooks.fire_webhooks('approval.rejected',
                                           {'approval_id': req_id, 'reason': 'timeout'})
                    _webhooks.fire_webhooks('gateway.alert',
                                           {'alert': 'approval_timeout', 'approval_id': req_id})
        except Exception:
            pass


_reaper_thread = threading.Thread(
    target=_approval_timeout_reaper, daemon=True, name='approval-reaper')
_reaper_thread.start()


def _alert_monitor() -> None:
    """Daemon: periodically check worker health and validation error rate.

    Fires ``gateway.alert`` webhooks on two conditions:

    * **worker_unhealthy** / **worker_recovered** — when the sandbox worker
      transitions between healthy and unhealthy states.
    * **validation_error_rate** — when the number of tool schema-validation
      errors in the last ``validation_error_window_seconds`` reaches or exceeds
      ``validation_error_threshold`` (0 = disabled).
    """
    global _worker_was_healthy
    while True:
        try:
            interval = max(5.0, float(_alert_config.get('worker_check_interval_seconds', 60)))
            time.sleep(interval)

            # ── Worker health transition alert ───────────────────────────────
            try:
                ok = _worker_manager.check_health()
            except Exception:
                ok = False
            _metrics.gauge('worker_healthy', 1.0 if ok else 0.0)

            if _worker_was_healthy is not None:
                if not ok and _worker_was_healthy:
                    _webhooks.fire_webhooks('gateway.alert', {'alert': 'worker_unhealthy'})
                    _audit('alert_fired', {'alert': 'worker_unhealthy'}, actor='system')
                elif ok and not _worker_was_healthy:
                    _webhooks.fire_webhooks('gateway.alert', {'alert': 'worker_recovered'})
                    _audit('alert_fired', {'alert': 'worker_recovered'}, actor='system')
            _worker_was_healthy = ok

            # ── Validation error rate alert ──────────────────────────────────
            threshold = int(_alert_config.get('validation_error_threshold', 0))
            if threshold > 0:
                window = float(_alert_config.get('validation_error_window_seconds', 60))
                cutoff = time.time() - window
                while _validation_error_times and _validation_error_times[0] < cutoff:
                    _validation_error_times.popleft()
                count = len(_validation_error_times)
                if count >= threshold:
                    _webhooks.fire_webhooks('gateway.alert', {
                        'alert': 'validation_error_rate',
                        'count': count,
                        'window_seconds': window,
                        'threshold': threshold,
                    })
                    _audit('alert_fired', {
                        'alert': 'validation_error_rate',
                        'count': count,
                        'threshold': threshold,
                    }, actor='system')
        except Exception:
            pass


_alert_monitor_thread = threading.Thread(
    target=_alert_monitor, daemon=True, name='alert-monitor')
_alert_monitor_thread.start()

# Page-diff watcher daemon
_watcher.start()

# MCP server integration — start all configured servers
try:
    _mcp.start_all()
except Exception as _mcp_err:
    import logging as _l; _l.getLogger(__name__).warning('MCP start_all: %s', _mcp_err)

# Plugin system — load all enabled plugins
try:
    _plugins.load_all()
except Exception as _plugins_err:
    import logging as _l; _l.getLogger(__name__).warning('plugin load_all: %s', _plugins_err)

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


def _require_bearer(request: Request) -> str:
    """Accept any valid user token (user or admin).  Returns the token string."""
    if request is None:
        return ''
    authh = request.headers.get('authorization') or request.headers.get('Authorization')
    if not authh:
        raise HTTPException(status_code=401, detail='missing authorization')
    parts = authh.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(status_code=401, detail='invalid authorization')
    token = parts[1]
    if not auth.get_user_for_token(token):
        raise HTTPException(status_code=401, detail='invalid or expired token')
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
    tool: str = Field(..., max_length=256)
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


@app.get('/admin/metrics/tools')
def metrics_tools_endpoint(request: Request):
    """Per-tool invocation count and latency summary.  Admin auth required.

    Returns a sorted list of ``{tool, calls, p50_seconds?, mean_seconds?}``
    objects (highest call count first) plus a ``total`` across all tools.
    Latency fields are only present when at least one duration observation
    exists for that tool.
    """
    _require_admin_token(request)
    rows = _metrics.get_labels_for_counter('tool_calls_total')

    # Build latency map from tool_call_duration_seconds histogram
    hist_map: dict = {}
    for labels, s, c, vals in _metrics.get_labels_for_histogram('tool_call_duration_seconds'):
        tool_name = labels.get('tool', '')
        if tool_name and vals:
            sorted_vals = sorted(vals)
            p50_idx = max(0, int(len(sorted_vals) * 0.5) - 1)
            hist_map[tool_name] = {
                'p50_seconds': round(sorted_vals[p50_idx], 6),
                'mean_seconds': round(s / c, 6) if c else None,
            }

    tools: list = []
    for labels, value in rows:
        tool_name = labels.get('tool', '')
        if not tool_name:
            continue
        entry: dict = {'tool': tool_name, 'calls': int(value)}
        if tool_name in hist_map:
            entry.update(hist_map[tool_name])
        tools.append(entry)
    tools.sort(key=lambda x: x['calls'], reverse=True)
    return {'tools': tools, 'total': sum(t['calls'] for t in tools)}


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

    _key = _audit_key()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _key:
            try:
                line = _decrypt_audit_line(line, _key)
            except Exception:
                pass  # plaintext fallback for mixed / unencrypted lines
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
        _validation_error_times.append(time.time())  # feeds sliding-window rate alert
        raise HTTPException(status_code=400, detail=result)

    # If capability was denied, return 403 Forbidden
    if result.get('status') == 'capability_denied':
        _metrics.inc('capability_denied_total', labels={'tool': call.tool})
        raise HTTPException(status_code=403, detail=result)

    if result.get("status") == "pending_approval":
        _metrics.inc('approvals_queued_total')
        _webhooks.fire_webhooks('approval.created', {'approval_id': result.get('id'), 'tool': call.tool})
        # Fire gateway.alert if approval queue depth reaches or exceeds the configured threshold
        _threshold = _alert_config.get('approval_queue_threshold', 0)
        if _threshold > 0:
            _pending_count = len(supervisor.queue.list_pending())
            if _pending_count >= _threshold:
                _webhooks.fire_webhooks('gateway.alert', {
                    'alert': 'approval_queue_depth',
                    'pending_approvals': _pending_count,
                    'threshold': _threshold,
                })
                _audit('alert_fired', {
                    'alert': 'approval_queue_depth',
                    'pending': _pending_count,
                    'threshold': _threshold,
                })
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
                'allowed_arg_keys': sorted(m.allowed_arg_keys) if m.allowed_arg_keys is not None else None,
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


@app.get('/admin/setup-status')
def admin_setup_status():
    """Return whether first-run setup is needed (no admin user created yet).

    No authentication required — safe to call before any account exists.
    """
    users = auth._load_users()
    return {'needs_setup': 'admin' not in users}


class SetupBody(BaseModel):
    password: str


@app.post('/admin/setup')
def admin_setup(body: SetupBody):
    """First-run only: create the admin account and return a login token.

    Returns 409 if the admin account already exists so the endpoint cannot be
    used to overwrite an established password.  Minimum password length is 8.
    """
    users = auth._load_users()
    if 'admin' in users:
        raise HTTPException(status_code=409, detail='Admin account already exists')
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail='Password must be at least 8 characters')
    auth.create_user('admin', body.password, roles=['admin'])
    t = auth.authenticate_user('admin', body.password)
    if not t:
        raise HTTPException(status_code=500, detail='Setup succeeded but authentication failed')
    return {'token': t['access_token'], 'refresh_token': t['refresh_token']}


class BootstrapTokenBody(BaseModel):
    secret: str


@app.post('/admin/bootstrap-token')
def admin_bootstrap_token(body: BootstrapTokenBody):
    """Mint an admin bearer token using the Electron bootstrap secret.

    The Electron shell generates a random ``INTELLI_BOOTSTRAP_SECRET`` at
    startup, passes it to the gateway via env, then calls this endpoint once
    to obtain a long-lived admin token.  The token is injected automatically
    into all admin UI pages so the user never has to paste it manually.

    Only reachable from 127.0.0.1 (uvicorn is bound to localhost only).
    No auth header required \u2014 the bootstrap secret acts as the credential.
    """
    expected = os.environ.get('INTELLI_BOOTSTRAP_SECRET', '')
    if not expected or body.secret != expected:
        raise HTTPException(status_code=403, detail='invalid bootstrap secret')
    import secrets as _sec
    at = _sec.token_urlsafe(32)
    auth._TOKENS[at] = {
        'username': 'admin',
        'expires':  int(time.time()) + auth.REFRESH_EXPIRE,  # 7-day lifetime
    }
    return {'access_token': at}



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


@app.get('/admin/redaction-rules')
def admin_list_all_redaction_rules(request: Request):
    """List every configured origin and its redaction fields (admin-gated)."""
    _require_admin_token(request)
    return {'rules': {k: sorted(v) for k, v in _redaction_rules.items()}}


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
# Provider settings
# ---------------------------------------------------------------------------

@app.get('/admin/providers/{provider}/settings')
def get_provider_settings(provider: str, request: Request):
    """Get persisted settings (model_id, endpoint) for a provider.  Admin auth required."""
    _require_admin_token(request)
    settings = ProviderSettingsStore.get(provider)
    return {'provider': provider, 'settings': settings}


@app.post('/admin/providers/{provider}/settings')
def set_provider_settings(provider: str, payload: dict, request: Request):
    """Save settings (model_id, endpoint) for a provider.  Admin auth required."""
    token = _require_admin_token(request)
    allowed_keys = {'model_id', 'endpoint'}
    filtered = {k: str(v).strip() for k, v in payload.items() if k in allowed_keys and v is not None}
    ProviderSettingsStore.set(provider, filtered)
    _audit('set_provider_settings', {'provider': provider, 'settings': filtered}, actor=_actor(token))
    return {'provider': provider, 'settings': ProviderSettingsStore.get(provider)}


# ---------------------------------------------------------------------------
# GitHub Copilot — OAuth Device Code Flow
# ---------------------------------------------------------------------------
# GitHub Copilot requires an OAuth token (not a PAT) obtained through the
# device code flow with GitHub's public Copilot OAuth client.
# Client ID below is the same one used by VS Code, gh CLI, and other editors.
_GH_COPILOT_CLIENT_ID = 'Iv1.b507a08c87ecfe98'
_GH_DEVICE_CODE_URL   = 'https://github.com/login/device/code'
_GH_ACCESS_TOKEN_URL  = 'https://github.com/login/oauth/access_token'


@app.post('/admin/providers/github_copilot/oauth/start')
def gh_copilot_oauth_start(request: Request):
    """Initiate the GitHub Device Code OAuth flow for Copilot.

    Returns device_code, user_code, verification_uri and interval so the
    frontend can display the code and start polling.
    """
    import requests as _req_lib
    _require_admin_token(request)
    resp = _req_lib.post(
        _GH_DEVICE_CODE_URL,
        headers={'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'client_id': _GH_COPILOT_CLIENT_ID, 'scope': 'read:user'},
        timeout=15,
    )
    if not resp.ok:
        raise HTTPException(status_code=502, detail=f'GitHub device code request failed: {resp.status_code}')
    data = resp.json()
    if 'device_code' not in data:
        raise HTTPException(status_code=502, detail=f'Unexpected GitHub response: {data}')
    return {
        'device_code':      data['device_code'],
        'user_code':        data['user_code'],
        'verification_uri': data['verification_uri'],
        'expires_in':       data.get('expires_in', 900),
        'interval':         data.get('interval', 5),
    }


@app.get('/admin/providers/github_copilot/oauth/poll')
def gh_copilot_oauth_poll(device_code: str, request: Request):
    """Poll GitHub for the access token after the user has authorized.

    Returns status: 'pending' | 'success' | 'error'.
    On success, the OAuth token is automatically stored as the Copilot key.
    """
    import requests as _req_lib
    token = _require_admin_token(request)
    resp = _req_lib.post(
        _GH_ACCESS_TOKEN_URL,
        headers={'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'},
        data={
            'client_id':   _GH_COPILOT_CLIENT_ID,
            'device_code': device_code,
            'grant_type':  'urn:ietf:params:oauth:grant-type:device_code',
        },
        timeout=15,
    )
    if not resp.ok:
        raise HTTPException(status_code=502, detail=f'GitHub token poll failed: {resp.status_code}')
    data = resp.json()
    error = data.get('error', '')
    if error == 'authorization_pending' or error == 'slow_down':
        return {'status': 'pending', 'error': error}
    if error:
        return {'status': 'error', 'error': data.get('error_description', error)}
    access_token = data.get('access_token', '')
    if not access_token:
        return {'status': 'error', 'error': 'No access_token in response'}
    # Store the OAuth token as the Copilot provider key
    ProviderKeyStore.set_key('github_copilot', access_token)
    _audit('gh_copilot_oauth_success', {}, actor=_actor(token))
    return {'status': 'success'}


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
    requires_key = getattr(adapter, 'requires_key', True)
    key = ProviderKeyStore.get_key(provider)
    configured = (not requires_key) or bool(key)
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
        'requires_key': requires_key,
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


class AlertsConfigUpdate(BaseModel):
    """Body for PUT /admin/alerts/config.

    All fields except ``approval_queue_threshold`` are optional; omit a field
    to leave the corresponding config value unchanged.
    """
    approval_queue_threshold: int                      # ≥ 0; 0 = disable queue-depth alert
    worker_check_interval_seconds: float | None = None # seconds between worker health polls (≥ 5)
    validation_error_window_seconds: float | None = None  # rolling window for error rate (> 0)
    validation_error_threshold: int | None = None      # errors in window to trigger alert; 0 = disable


class ApprovalsConfigUpdate(BaseModel):
    """Body for PUT /admin/approvals/config."""
    timeout_seconds: float  # >= 0; 0 = disable auto-reject


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


# ---------------------------------------------------------------------------
# Alert configuration
# ---------------------------------------------------------------------------

@app.get('/admin/alerts/config')
def get_alerts_config(request: Request):
    """Return the current alert configuration.

    Admin auth required.  Returns a dict with all alert thresholds:
    * ``approval_queue_threshold`` (int) — 0 = disabled
    * ``worker_check_interval_seconds`` (float) — health poll interval
    * ``validation_error_window_seconds`` (float) — sliding window duration
    * ``validation_error_threshold`` (int) — 0 = disabled
    """
    _require_admin_token(request)
    return dict(_alert_config)


@app.put('/admin/alerts/config')
def put_alerts_config(body: AlertsConfigUpdate, request: Request):
    """Update alert configuration at runtime (admin auth required).

    Fields not present in the request body are left unchanged.
    Setting a threshold to 0 disables that alert.
    """
    token = _require_admin_token(request)
    updated: dict = {}
    if body.approval_queue_threshold < 0:
        raise HTTPException(status_code=422, detail='approval_queue_threshold must be >= 0')
    updated['approval_queue_threshold'] = body.approval_queue_threshold
    if body.worker_check_interval_seconds is not None:
        if body.worker_check_interval_seconds < 5:
            raise HTTPException(status_code=422,
                detail='worker_check_interval_seconds must be >= 5')
        updated['worker_check_interval_seconds'] = body.worker_check_interval_seconds
    if body.validation_error_window_seconds is not None:
        if body.validation_error_window_seconds <= 0:
            raise HTTPException(status_code=422,
                detail='validation_error_window_seconds must be > 0')
        updated['validation_error_window_seconds'] = body.validation_error_window_seconds
    if body.validation_error_threshold is not None:
        if body.validation_error_threshold < 0:
            raise HTTPException(status_code=422,
                detail='validation_error_threshold must be >= 0')
        updated['validation_error_threshold'] = body.validation_error_threshold
    _alert_config.update(updated)
    _audit('update_alerts_config', updated, actor=_actor(token))
    return dict(_alert_config)


# ---------------------------------------------------------------------------
# Approval timeout configuration
# ---------------------------------------------------------------------------

@app.get('/admin/approvals/config')
def get_approvals_config(request: Request):
    """Return the current approval auto-reject timeout configuration.

    Admin auth required.
    Returns {"timeout_seconds": float} where 0 means disabled.
    """
    _require_admin_token(request)
    return dict(_approvals_config)


@app.put('/admin/approvals/config')
def put_approvals_config(body: ApprovalsConfigUpdate, request: Request):
    """Update the approval auto-reject timeout at runtime.

    Admin auth required.  Pending approvals older than *timeout_seconds* are
    automatically rejected by the background reaper; fires ``approval.rejected``
    and ``gateway.alert`` webhooks for each expired item.  Set to 0 to disable.
    """
    global _approvals_config
    token = _require_admin_token(request)
    if body.timeout_seconds < 0:
        raise HTTPException(status_code=422, detail='timeout_seconds must be >= 0')
    _approvals_config = {'timeout_seconds': body.timeout_seconds}
    _audit('update_approvals_config', {'timeout_seconds': body.timeout_seconds},
           actor=_actor(token))
    return dict(_approvals_config)




class ChatRequest(BaseModel):
    provider: str
    messages: list
    model: str = ''
    temperature: float = 0.7
    max_tokens: int = 1024
    # Context injection flags
    use_workspace: bool = False    # prepend AGENTS.md + SOUL.md as system prompt
    use_page_context: bool = False # prepend active tab snapshot to system prompt
    use_tools: bool = True         # enable ReAct tool loop (web_search, web_fetch, …)
    system_prompt: str = ''        # extra system prompt injected by caller
    persona: str = ''              # persona slug — injects SOUL.md before everything else
    session_id: str = ''           # persist this conversation to disk for session history


@app.post('/chat/complete')
def chat_complete(
    req: ChatRequest,
    request: Request,
    _rl=Depends(rate_limiter),
    stream: bool = Query(False, description='Return response as SSE text/event-stream'),
):
    """Proxy a chat-completion request to the configured provider.

    Requires a valid Bearer token (any authenticated user).
    Returns the provider's reply in a unified format.

    When ``?stream=true`` is passed the response is sent as ``text/event-stream``.
    Each ``data:`` event carries a JSON object:
      - ``{"token": "<word>", "done": false}``  – one word token at a time
      - ``{"content": "...", "model": "...", "done": true}``  – final event with full reply
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

    # Use FailoverAdapter: transparently retries with fallback providers on 429/5xx
    adapter = _FailoverAdapter(req.provider, req.model or None)
    if not adapter.is_available():
        raise HTTPException(
            status_code=503,
            detail=f'provider {req.provider!r} and all fallbacks are unavailable',
        )

    kwargs: dict = {}
    if req.model:
        kwargs['model'] = req.model

    # ---- Resolve session ID (generate if caller didn't supply one) --------
    sid = req.session_id.strip() or _sessions.new_session_id()

    # ---- Build system prompt ----------------------------------------
    system_parts: list[str] = []
    # Persona SOUL.md injected first so it frames everything that follows
    if req.persona:
        persona_prompt = _personas.build_system_prompt(req.persona)
        if persona_prompt:
            system_parts.append(persona_prompt)
    if req.use_workspace:
        ws_prompt = _workspace.build_system_prompt(include_tools=True)
        if ws_prompt:
            system_parts.append(ws_prompt)
    if req.use_page_context:
        snap = _tab_snapshot.get_snapshot()
        if snap:
            system_parts.append(_workspace.build_page_context_block(snap))
    if req.system_prompt:
        system_parts.append(req.system_prompt)
    # ---- Auto-inject relevant memories from vector store ----------------
    last_user_text = next(
        (m.get('content', '') for m in reversed(req.messages) if m.get('role') == 'user'), ''
    )
    if last_user_text:
        mem_ctx = _memory.build_memory_context(last_user_text)
        if mem_ctx:
            system_parts.append(mem_ctx)
    if req.use_tools:
        system_parts.append(build_tool_system_block())
    if system_parts:
        combined_system = '\n\n---\n\n'.join(system_parts)
        # Inject as a leading system message if the provider supports it
        # (OpenAI/Ollama: role=system; Anthropic: top-level system field)
        kwargs['system'] = combined_system
        # Also prepend as system message for adapters that use messages-only mode
        messages_with_sys = [{'role': 'system', 'content': combined_system}] + list(req.messages)
    else:
        messages_with_sys = list(req.messages)

    if stream:
        # SSE streaming: runs the tool loop in a background thread and pushes
        # events (tool_call, tool_result, approval_required, tokens) in real time.
        # SSE keepalive comments every 10 s prevent long approval waits from
        # dropping the connection.
        async def _sse_gen():
            import asyncio as _asyncio
            import queue as _queue
            ev_queue: _queue.Queue = _queue.Queue()
            _DONE = object()          # sentinel value
            result_holder: list = [None]
            error_holder:  list = [None]

            def _on_tool_call(name, args):
                ev_queue.put({'type': 'tool_call', 'tool': name, 'args': args})

            def _on_tool_result(name, res):
                ev_queue.put({
                    'type': 'tool_result', 'tool': name,
                    'result': (res[:400] + '…') if len(res) > 400 else res,
                })

            def _thread_fn():
                try:
                    if req.use_tools:
                        result_holder[0] = run_tool_loop(
                            adapter,
                            messages=messages_with_sys,
                            temperature=req.temperature,
                            max_tokens=req.max_tokens,
                            session_id=sid,
                            approval_queue=ev_queue,
                            on_tool_call=_on_tool_call,
                            on_tool_result=_on_tool_result,
                            **kwargs,
                        )
                    else:
                        result_holder[0] = adapter.chat_complete(
                            messages=messages_with_sys,
                            temperature=req.temperature,
                            max_tokens=req.max_tokens,
                            **kwargs,
                        )
                    _metrics.inc('provider_requests_total', labels={'provider': req.provider})
                except Exception as exc:
                    error_holder[0] = str(exc)
                    _metrics.inc('provider_errors_total', labels={'provider': req.provider})
                finally:
                    ev_queue.put(_DONE)

            t = threading.Thread(
                target=_thread_fn, daemon=True, name=f'tool-loop-{sid[:6]}')
            t.start()

            loop = _asyncio.get_running_loop()
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: ev_queue.get(timeout=10)
                    )
                    if ev is _DONE:
                        break
                    yield f'data: {json.dumps(ev)}\n\n'
                except _queue.Empty:
                    yield ': keepalive\n\n'  # SSE comment keeps connection alive

            if error_holder[0]:
                yield f'data: {json.dumps({"error": error_holder[0], "done": True})}\n\n'
                return

            result: dict = result_holder[0] or {'content': ''}
            content: str = result.get('content', '')

            # Persist session (best-effort — never fail the response)
            try:
                _msg_meta = {'provider': req.provider, 'model': req.model or ''}
                for m in req.messages:
                    if m.get('role') in ('user', 'assistant'):
                        _sessions.save_message(sid, m['role'], m.get('content', ''), _msg_meta)
                if content:
                    _sessions.save_message(sid, 'assistant', content,
                                           {'provider': req.provider, 'model': result.get('model', req.model)})
            except Exception:
                pass

            # Emit tokens word by word for a streaming-feel UX
            words = content.split(' ')
            for i, word in enumerate(words):
                token = word + (' ' if i < len(words) - 1 else '')
                yield f'data: {json.dumps({"token": token, "done": False})}\n\n'
            # Final event: full result + done=True + failover info + session_id
            _fo = adapter.last_result_meta
            yield f'data: {json.dumps({**result, "done": True, "session_id": sid, **_fo})}\n\n'

        return StreamingResponse(
            _sse_gen(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Access-Control-Allow-Origin': '*',
            },
        )

    try:
        if req.use_tools:
            result = run_tool_loop(
                adapter,
                messages=messages_with_sys,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                session_id=sid,
                **kwargs,
            )
        else:
            result = adapter.chat_complete(
                messages=messages_with_sys,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                **kwargs,
            )
    except Exception as exc:
        _metrics.inc('provider_errors_total', labels={'provider': req.provider})
        raise HTTPException(status_code=502, detail=f'provider error: {exc}')

    _metrics.inc('provider_requests_total', labels={'provider': req.provider})

    # Persist session (best-effort)
    try:
        _msg_meta = {'provider': req.provider, 'model': req.model or ''}
        for m in req.messages:
            if m.get('role') in ('user', 'assistant'):
                _sessions.save_message(sid, m['role'], m.get('content', ''), _msg_meta)
        asst_content = result.get('content', '')
        if asst_content:
            _sessions.save_message(sid, 'assistant', asst_content,
                                   {'provider': req.provider, 'model': result.get('model', req.model)})
    except Exception:
        pass

    # Attach failover metadata + session_id so UI can persist and warn
    result.update(adapter.last_result_meta)
    result['session_id'] = sid
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
        'tool_calls_total': int(sum(v for _, v in _metrics.get_labels_for_counter('tool_calls_total'))),
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
def schedule_history(task_id: str, request: Request,
                     limit: int = Query(50, ge=1, le=500,
                                        description='Max records to return (1-500, newest first)')):
    """Return recent run history for a scheduled task (newest first).

    Admin Bearer required.  Returns 404 if the task does not exist.
    Use ``?limit=N`` to cap the number of returned records (default 50, max 500).
    """
    _require_admin_token(request)
    history = _scheduler.get_history(task_id)
    if history is None:
        raise HTTPException(status_code=404, detail='task not found')
    sliced = history[:limit]
    return {'task_id': task_id, 'count': len(sliced), 'total': len(history), 'history': sliced}


# ─────────────────────────────────────────────────────────────────────────────
# Tab snapshot  (browser → gateway → agents)
# ─────────────────────────────────────────────────────────────────────────────

class TabSnapshotBody(BaseModel):
    url:   str
    title: str = ''
    html:  str = ''


@app.put('/tab/snapshot', status_code=204)
def tab_snapshot_put(body: TabSnapshotBody):
    """Receive a snapshot of the active tab from the Electron browser chrome.

    The browser shell calls this endpoint automatically after each page load so
    agents can retrieve the current page content without needing DOM access.
    No auth required — endpoint is only reachable from localhost.
    """
    _tab_snapshot.set_snapshot(body.url, body.title, body.html)
    # Auto-store page visit in vector memory (fire-and-forget in background thread)
    if body.url and not body.url.startswith(('about:', 'chrome:', 'file:')):
        def _store_page():
            try:
                text = _memory.extract_text_from_html(body.html)
                if len(text) > 80:  # skip blank/error pages
                    _memory.get_store().add(
                        text=text, source='page',
                        url=body.url, title=body.title,
                    )
            except Exception as exc:
                logger.debug('memory auto-store failed: %s', exc)
        import threading as _threading
        _threading.Thread(target=_store_page, daemon=True).start()


@app.get('/tab/snapshot')
def tab_snapshot_get(request: Request):
    """Return the most recent active-tab snapshot.

    Agents call this to read the HTML of the page currently open in the browser.
    Returns 204 with an empty body when no snapshot has been captured yet.
    No auth required — only accessible from localhost.
    """
    snap = _tab_snapshot.get_snapshot()
    if not snap:
        return PlainTextResponse('', status_code=204)
    # Omit full HTML for the summary field to keep the response lightweight
    return {
        'url':       snap.get('url', ''),
        'title':     snap.get('title', ''),
        'timestamp': snap.get('timestamp', ''),
        'length':    snap.get('length', 0),
        'html':      snap.get('html', ''),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Addon injection queue  (gateway → browser chrome)
# ─────────────────────────────────────────────────────────────────────────────

@app.get('/tab/inject-queue')
def inject_queue_poll():
    """Return and drain pending addon injection requests.

    The Electron browser chrome polls this endpoint every few seconds.
    Each item in the returned list contains ``name`` and ``code_js``.
    The queue is cleared after this call so each script runs exactly once.
    No auth required — only reachable from localhost.
    """
    return _addons.pop_inject_queue()


# ─────────────────────────────────────────────────────────────────────────────
# Addon management  (agents / admin UI)
# ─────────────────────────────────────────────────────────────────────────────

class AddonCreate(BaseModel):
    name:        str
    description: str = ''
    code_js:     str


class AddonUpdate(BaseModel):
    description: str | None = None
    code_js:     str | None = None


@app.get('/admin/addons')
def addons_list(request: Request):
    """List all registered addons.  Admin auth required."""
    _require_admin_token(request)
    return {'addons': _addons.list_addons()}


@app.post('/admin/addons', status_code=201)
def addons_create(body: AddonCreate, request: Request):
    """Create a new addon.  Admin auth required.

    ``code_js`` is a JavaScript snippet executed inside the active browser tab
    when the addon is activated.  Agents can write addons on the fly and
    activate them to extend tab behaviour without requiring a full extension.
    """
    _require_admin_token(request)
    try:
        addon = _addons.create_addon(body.name, body.description, body.code_js)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return addon


@app.get('/admin/addons/{name}')
def addons_get(name: str, request: Request):
    """Return a single addon by name.  Admin auth required."""
    _require_admin_token(request)
    addon = _addons.get_addon(name)
    if addon is None:
        raise HTTPException(status_code=404, detail='addon not found')
    return addon


@app.put('/admin/addons/{name}')
def addons_update(name: str, body: AddonUpdate, request: Request):
    """Update an addon's description and/or code.  Admin auth required."""
    _require_admin_token(request)
    try:
        addon = _addons.update_addon(name, body.description, body.code_js)
    except KeyError:
        raise HTTPException(status_code=404, detail='addon not found')
    return addon


@app.delete('/admin/addons/{name}', status_code=204)
def addons_delete(name: str, request: Request):
    """Delete an addon permanently.  Admin auth required."""
    _require_admin_token(request)
    try:
        _addons.delete_addon(name)
    except KeyError:
        raise HTTPException(status_code=404, detail='addon not found')


@app.post('/admin/addons/{name}/activate')
def addons_activate(name: str, request: Request):
    """Activate an addon — marks it active and queues its JS for injection.

    The browser chrome picks up the injection within its next poll cycle
    (≤ 3 seconds) and executes the code inside the currently active tab.
    Admin auth required.
    """
    _require_admin_token(request)
    try:
        addon = _addons.activate_addon(name)
    except KeyError:
        raise HTTPException(status_code=404, detail='addon not found')
    return {'activated': True, 'addon': addon}


@app.post('/admin/addons/{name}/deactivate')
def addons_deactivate(name: str, request: Request):
    """Deactivate an addon.  Does not undo any already-injected JS.

    Admin auth required.
    """
    _require_admin_token(request)
    try:
        addon = _addons.deactivate_addon(name)
    except KeyError:
        raise HTTPException(status_code=404, detail='addon not found')
    return {'deactivated': True, 'addon': addon}


# ─────────────────────────────────────────────────────────────────────────────
# Workspace API  (agent persistent workspace — inspired by OpenClaw)
# ─────────────────────────────────────────────────────────────────────────────

class WorkspaceWriteBody(BaseModel):
    content: str


@app.get('/workspace/files')
def workspace_list_files(request: Request):
    """List all files in the agent workspace.  Admin auth required."""
    _require_admin_token(request)
    return {'files': _workspace.list_files()}


@app.get('/workspace/file')
def workspace_read_file(path: str, request: Request):
    """Read a workspace file by relative path.  Admin auth required."""
    _require_admin_token(request)
    try:
        content = _workspace.read_file(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'path': path, 'content': content}


@app.post('/workspace/file')
def workspace_write_file(path: str, body: WorkspaceWriteBody, request: Request):
    """Create or overwrite a workspace file.  Admin auth required."""
    token = _require_admin_token(request)
    try:
        meta = _workspace.write_file(path, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit('workspace_write_file', {'path': path}, actor=_actor(token))
    return meta


@app.delete('/workspace/file')
def workspace_delete_file(path: str, request: Request):
    """Delete a workspace file.  Admin auth required."""
    token = _require_admin_token(request)
    try:
        _workspace.delete_file(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit('workspace_delete_file', {'path': path}, actor=_actor(token))
    return {'deleted': True, 'path': path}


@app.get('/workspace/skills')
def workspace_list_skills(request: Request):
    """List installed workspace skills.  Admin auth required."""
    _require_admin_token(request)
    return {'skills': _workspace.list_skills()}


class WorkspaceSkillCreate(BaseModel):
    slug: str
    name: str
    description: str = ''
    content: str = ''


@app.post('/workspace/skills')
def workspace_create_skill(body: WorkspaceSkillCreate, request: Request):
    """Create a new workspace skill.  Admin auth required."""
    token = _require_admin_token(request)
    try:
        skill = _workspace.create_skill(body.slug, body.name, body.description, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit('workspace_create_skill', {'slug': body.slug}, actor=_actor(token))
    return skill


@app.delete('/workspace/skills/{slug}')
def workspace_delete_skill(slug: str, request: Request):
    """Delete a workspace skill.  Admin auth required."""
    token = _require_admin_token(request)
    try:
        _workspace.delete_skill(slug)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _audit('workspace_delete_skill', {'slug': slug}, actor=_actor(token))
    return {'deleted': True, 'slug': slug}


@app.get('/workspace/skills/{slug}')
def workspace_get_skill(slug: str, request: Request):
    """Return full content + metadata for a single skill.  Admin auth required."""
    _require_admin_token(request)
    try:
        return _workspace.get_skill(slug)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


class WorkspaceSkillUpdate(BaseModel):
    content: str


@app.put('/workspace/skills/{slug}')
def workspace_update_skill(slug: str, body: WorkspaceSkillUpdate, request: Request):
    """Overwrite the SKILL.md of an existing skill.  Admin auth required."""
    token = _require_admin_token(request)
    try:
        result = _workspace.update_skill(slug, body.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _audit('workspace_update_skill', {'slug': slug}, actor=_actor(token))
    return result


@app.post('/workspace/skills/{slug}/test')
def workspace_test_skill(slug: str, request: Request):
    """Validate a skill's SKILL.md and return a lint report.  Admin auth required."""
    _require_admin_token(request)
    try:
        skill = _workspace.get_skill(slug)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    report = _workspace.validate_skill(skill['content'])
    return {**report, 'slug': slug, 'name': skill['name']}


@app.get('/workspace/system-prompt')
def workspace_system_prompt(request: Request):
    """Return the assembled system prompt (AGENTS.md + SOUL.md + TOOLS.md).

    Unauthenticated — only accessible from localhost so the chat UI can load it.
    """
    prompt = _workspace.build_system_prompt(include_tools=True)
    return {'system_prompt': prompt}


# ─────────────────────────────────────────────────────────────────────────────
# Canvas  (agent → live HTML panel)
# ─────────────────────────────────────────────────────────────────────────────

class CanvasRenderBody(BaseModel):
    html:  str
    title: str = ''


@app.post('/canvas/render', status_code=204)
def canvas_render(body: CanvasRenderBody, request: Request):
    """Push new HTML to the canvas panel.  Auth required."""
    _require_admin_token(request)
    _canvas.render(body.html, body.title)


@app.post('/canvas/clear', status_code=204)
def canvas_clear(request: Request):
    """Clear the canvas panel.  Auth required."""
    _require_admin_token(request)
    _canvas.clear()


@app.get('/canvas/snapshot')
def canvas_snapshot():
    """Return the current canvas HTML snapshot (no auth — localhost only)."""
    return {'html': _canvas.get_html(), 'title': ''}


@app.get('/canvas/stream')
async def canvas_stream(token: str = Query('')):
    """SSE stream of canvas update events.  Accepts token as query param."""
    import json as _json
    import asyncio as _asyncio

    # Validate token if provided
    if token:
        if not auth.get_user_for_token(token):
            from fastapi.responses import Response
            return Response(status_code=401)

    q = _canvas.subscribe()

    async def _gen():
        try:
            # Send current snapshot immediately so the panel loads without waiting
            snap = _canvas.get_html()
            yield f"data: {_json.dumps({'type': 'render', 'html': snap, 'title': '', 'ts': 0})}\n\n"
            while True:
                try:
                    event = await _asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {_json.dumps(event)}\n\n"
                except _asyncio.TimeoutError:
                    yield f"data: {_json.dumps({'type': 'ping'})}\n\n"
        except _asyncio.CancelledError:
            pass
        finally:
            _canvas.unsubscribe(q)

    return StreamingResponse(
        _gen(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                 'Access-Control-Allow-Origin': '*'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Session compaction
# ─────────────────────────────────────────────────────────────────────────────

class CompactRequest(BaseModel):
    messages: list
    provider: str
    model:    str = ''


@app.post('/chat/compact')
def chat_compact(req: CompactRequest, request: Request):
    """Summarize old messages and return a compacted message list.

    Returns:
        compacted_messages: shortened list with a summary block prepended
        summary:            the plain-text summary that was generated
        tokens_saved:       estimated tokens freed up
        usage_before:       fraction of context used before compaction
        usage_after:        fraction after
    """
    authh = request.headers.get('authorization', '')
    parts = authh.split()
    if len(parts) != 2 or not auth.get_user_for_token(parts[1]):
        raise HTTPException(status_code=401, detail='unauthorized')

    try:
        adapter = get_adapter(req.provider)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    usage_before = _compaction.usage_fraction(req.messages, req.model)

    try:
        compacted, summary, tokens_saved = _compaction.compact_messages(
            req.messages, adapter, model=req.model
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Compaction failed: {exc}')

    usage_after = _compaction.usage_fraction(compacted, req.model)

    return {
        'compacted_messages': compacted,
        'summary':            summary,
        'tokens_saved':       tokens_saved,
        'usage_before':       round(usage_before, 3),
        'usage_after':        round(usage_after, 3),
    }


@app.get('/chat/token-usage')
def chat_token_usage(model: str = '', request: Request = None):
    """Return context limit info for a model (no auth needed)."""
    limit = _compaction.context_limit_for(model)
    return {'model': model, 'context_limit': limit}


# ---------------------------------------------------------------------------
# Failover chain management
# ---------------------------------------------------------------------------

@app.get('/admin/failover/chain')
def get_failover_chain(request: Request):
    """Return the current provider failover chain.  Admin auth required."""
    _require_admin_token(request)
    return {
        'chain':     _failover.get_chain(),
        'cooldowns': _failover.cooldown_status(),
    }


class FailoverChainEntry(BaseModel):
    provider: str
    model:    str | None = None


@app.put('/admin/failover/chain')
def set_failover_chain(entries: list[FailoverChainEntry], request: Request):
    """Replace the provider failover chain.  Admin auth required.

    Body: [{"provider": "openai"}, {"provider": "anthropic"}, {"provider": "ollama"}]
    """
    _require_admin_token(request)
    _failover.set_chain([{'provider': e.provider, 'model': e.model} for e in entries])
    return {'ok': True, 'chain': _failover.get_chain()}


@app.get('/admin/failover/cooldowns')
def get_failover_cooldowns(request: Request):
    """Return which providers are currently on cooldown.  Admin auth required."""
    _require_admin_token(request)
    return {'cooldowns': _failover.cooldown_status()}


# ---------------------------------------------------------------------------
# Vector Memory REST API
# ---------------------------------------------------------------------------

class MemoryAddBody(BaseModel):
    text:   str
    source: str = 'manual'
    url:    str = ''
    title:  str = ''
    pinned: bool = False


class MemorySearchQuery(BaseModel):
    q:      str
    n:      int  = 5
    source: str  = ''


@app.post('/memory/add')
def memory_add(body: MemoryAddBody, request: Request):
    """Pin a memory (fact, note, or bookmark).  Bearer token required."""
    _require_bearer(request)
    doc_id = _memory.get_store().add(
        text=body.text, source=body.source,
        url=body.url, title=body.title, pinned=body.pinned,
    )
    return {'ok': True, 'id': doc_id}


@app.get('/memory/search')
def memory_search(q: str, n: int = 5, source: str = '', request: Request = None):
    """Semantic search over stored memories.  Bearer token required."""
    _require_bearer(request)
    results = _memory.get_store().search(
        query=q, n=n,
        source_filter=source or None,
    )
    return {'results': results, 'total': len(results)}


@app.delete('/memory/{doc_id}')
def memory_delete(doc_id: str, request: Request):
    """Forget a memory by ID.  Bearer token required."""
    _require_bearer(request)
    ok = _memory.get_store().delete(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail='memory not found')
    return {'ok': True}


@app.get('/memory/list')
def memory_list(n: int = 20, request: Request = None):
    """List most recent memories.  Bearer token required."""
    _require_bearer(request)
    return {'memories': _memory.get_store().list_recent(n)}


@app.get('/memory/stats')
def memory_stats(request: Request = None):
    """Return memory store statistics."""
    store = _memory.get_store()
    return {
        'count':   store.count(),
        'backend': store.backend_name,
        'data_dir': _memory._DATA_DIR,
    }


# ---------------------------------------------------------------------------
# Coding-agent info
# ---------------------------------------------------------------------------

@app.get('/coding/info')
def coding_info(request: Request = None):
    """Return coding workspace information.  Bearer token required."""
    _require_bearer(request)
    try:
        from tools.coding_tools import code_root, _CODE_ROOT, _SHELL_DISABLED
        root = code_root()
        shell_enabled = not _SHELL_DISABLED
    except Exception:
        root = str(__import__('pathlib').Path.home() / 'intelli-workspace')
        shell_enabled = True
    return {
        'root':          root,
        'shell_enabled': shell_enabled,
        'tools':         ['file_read', 'file_write', 'file_patch', 'file_delete', 'file_list', 'shell_exec'],
    }


# ---------------------------------------------------------------------------
# Browser automation command queue
# ---------------------------------------------------------------------------

@app.get('/browser/command-queue')
def browser_command_queue(request: Request = None):
    """Poll for pending browser automation commands (called by Electron shell).
    Bearer token required."""
    _require_bearer(request)
    try:
        from tools.browser_tools import pop_command_queue
        cmd = pop_command_queue()
        if cmd:
            return cmd
        return {'command': None}
    except Exception as exc:
        return {'command': None, 'error': str(exc)}


@app.post('/browser/result')
def browser_command_result(payload: dict, request: Request = None):
    """Receive browser command execution result from Electron shell.
    Bearer token required."""
    _require_bearer(request)
    try:
        from tools.browser_tools import post_command_result
        cmd_id = payload.get('id')
        result = payload.get('result')
        if cmd_id:
            post_command_result(cmd_id, result)
        return {'ok': True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Personas API
# ---------------------------------------------------------------------------

class PersonaCreate(BaseModel):
    name: str
    soul: str
    avatar: str = '🤖'
    model: str = ''
    provider: str = ''


class PersonaUpdate(BaseModel):
    name: str = ''
    soul: str = ''
    avatar: str = ''
    model: str = ''
    provider: str = ''


@app.get('/personas')
def personas_list(request: Request):
    """List all agent personas (built-in + user-created). Auth required."""
    _require_bearer(request)
    return _personas.list_personas()


@app.get('/personas/{slug}')
def personas_get(slug: str, request: Request):
    """Return a single persona by slug. Auth required."""
    _require_bearer(request)
    p = _personas.get_persona(slug)
    if not p:
        raise HTTPException(status_code=404, detail=f'persona {slug!r} not found')
    return p


@app.post('/personas', status_code=201)
def personas_create(body: PersonaCreate, request: Request):
    """Create a new agent persona. Auth required."""
    _require_bearer(request)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail='name is required')
    return _personas.create_persona(
        name=body.name, soul=body.soul, avatar=body.avatar,
        model=body.model, provider=body.provider,
    )


@app.put('/personas/{slug}')
def personas_update(slug: str, body: PersonaUpdate, request: Request):
    """Update an existing persona. Built-in 'intelli' persona cannot be changed."""
    _require_bearer(request)
    kwargs = {k: v for k, v in body.model_dump().items() if v}
    p = _personas.update_persona(slug, **kwargs)
    if p is None:
        raise HTTPException(status_code=404, detail=f'persona {slug!r} not found or is immutable')
    return p


@app.delete('/personas/{slug}')
def personas_delete(slug: str, request: Request):
    """Delete a user-created persona. Built-in 'intelli' cannot be deleted."""
    _require_bearer(request)
    if not _personas.delete_persona(slug):
        raise HTTPException(status_code=404, detail=f'persona {slug!r} not found or cannot be deleted')
    return {'deleted': slug}


# ---------------------------------------------------------------------------
# Session History API
# ---------------------------------------------------------------------------

@app.get('/sessions')
def sessions_list(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query('', description='Search query'),
    request: Request = None,
):
    """List chat sessions sorted by most-recently-active. Auth required."""
    _require_bearer(request)
    if q:
        return _sessions.search_sessions(q, limit=limit)
    return _sessions.list_sessions(limit=limit, offset=offset)


@app.get('/sessions/{session_id}')
def sessions_get(session_id: str, request: Request):
    """Return all messages in a session in chronological order. Auth required."""
    _require_bearer(request)
    msgs = _sessions.get_session(session_id)
    if not msgs:
        raise HTTPException(status_code=404, detail=f'session {session_id!r} not found')
    return {'session_id': session_id, 'messages': msgs, 'count': len(msgs)}


@app.get('/sessions/{session_id}/stats')
def sessions_stats(session_id: str, request: Request):
    """Return basic stats for a session (message counts, token estimates)."""
    _require_bearer(request)
    return _sessions.session_stats(session_id)


@app.delete('/sessions/{session_id}')
def sessions_delete(session_id: str, request: Request):
    """Permanently delete a session and all its messages. Auth required."""
    _require_bearer(request)
    if not _sessions.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f'session {session_id!r} not found')
    return {'deleted': session_id}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Page-diff Watcher API
# ---------------------------------------------------------------------------

class _WatcherCreate(BaseModel):
    url:                str
    label:              str  = ''
    interval_minutes:   int  = 60
    notify_threshold:   float = 0.02


class _WatcherUpdate(BaseModel):
    label:              str   | None = None
    interval_minutes:   int   | None = None
    notify_threshold:   float | None = None
    enabled:            bool  | None = None


@app.get('/watchers')
def watchers_list(request: Request):
    """List all page-diff watchers."""
    _require_bearer(request)
    return {'watchers': _watcher.list_watchers()}


@app.post('/watchers', status_code=201)
def watchers_create(body: _WatcherCreate, request: Request):
    """Create a new page-diff watcher."""
    _require_bearer(request)
    try:
        w = _watcher.add_watcher(
            url=body.url,
            label=body.label,
            interval_minutes=body.interval_minutes,
            notify_threshold=body.notify_threshold,
        )
        return w
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/watchers/alerts')
def watchers_all_alerts(limit: int = Query(50, ge=1, le=500), request: Request = None):
    """Return recent alerts across all watchers."""
    _require_bearer(request)
    return {'alerts': _watcher.get_all_alerts(limit=limit)}


@app.get('/watchers/{wid}')
def watchers_get(wid: str, request: Request):
    """Return a single watcher by ID."""
    _require_bearer(request)
    w = _watcher.get_watcher(wid)
    if w is None:
        raise HTTPException(status_code=404, detail=f'watcher {wid!r} not found')
    return w


@app.put('/watchers/{wid}')
def watchers_update(wid: str, body: _WatcherUpdate, request: Request):
    """Update watcher fields."""
    _require_bearer(request)
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=400, detail='No fields to update')
    w = _watcher.update_watcher(wid, **kwargs)
    if w is None:
        raise HTTPException(status_code=404, detail=f'watcher {wid!r} not found')
    return w


@app.delete('/watchers/{wid}', status_code=204)
def watchers_delete(wid: str, request: Request):
    """Delete a watcher and its alert history."""
    _require_bearer(request)
    if not _watcher.delete_watcher(wid):
        raise HTTPException(status_code=404, detail=f'watcher {wid!r} not found')


@app.get('/watchers/{wid}/alerts')
def watchers_alerts(wid: str, clear: bool = Query(False), request: Request = None):
    """Return (and optionally clear) alerts for a watcher."""
    _require_bearer(request)
    w = _watcher.get_watcher(wid)
    if w is None:
        raise HTTPException(status_code=404, detail=f'watcher {wid!r} not found')
    return {'wid': wid, 'alerts': _watcher.get_alerts(wid, clear=clear)}


@app.post('/watchers/{wid}/trigger')
def watchers_trigger(wid: str, request: Request):
    """Force an immediate poll of the watcher URL."""
    _require_bearer(request)
    ok = _watcher.trigger_watcher(wid)
    if not ok:
        raise HTTPException(status_code=404, detail=f'watcher {wid!r} not found')
    return {'triggered': wid}


# ---------------------------------------------------------------------------
# Agent Tool Approval API
# ---------------------------------------------------------------------------

@app.get('/agent/approvals')
def agent_approvals_list(
    session_id: str = Query('', description='Filter by session id'),
    request: Request = None,
):
    """Return all pending tool-call approvals (optionally scoped to a session)."""
    _require_bearer(request)
    return {'approvals': _approval_gate.list_pending(session_id=session_id)}


@app.post('/agent/approvals/{aid}/approve')
def agent_approvals_approve(aid: str, request: Request):
    """Approve a pending tool-call. The blocked agent thread then executes the tool."""
    _require_bearer(request)
    if not _approval_gate.approve(aid):
        raise HTTPException(status_code=404, detail=f'approval {aid!r} not found')
    return {'approved': aid}


@app.post('/agent/approvals/{aid}/deny')
def agent_approvals_deny(aid: str, request: Request):
    """Deny a pending tool-call. The agent thread receives a [DENIED] message."""
    _require_bearer(request)
    if not _approval_gate.deny(aid):
        raise HTTPException(status_code=404, detail=f'approval {aid!r} not found')
    return {'denied': aid}


# ---------------------------------------------------------------------------
# Navigation Guard / Security Check
# ---------------------------------------------------------------------------

@app.get('/security/check_url')
def security_check_url(url: str = Query(..., description='URL to check')):
    """Synchronous navigation guard: check whether a URL is safe to visit.

    Used by the Electron shell's ``will-navigate`` handler to block:
    - Private / loopback IP ranges (SSRF prevention)
    - Non-http(s)/file:// schemes
    - Known risky patterns

    Returns ``{"allow": bool, "reason": str}``.
    """
    import ipaddress
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except Exception:
        return {'allow': False, 'reason': 'Malformed URL'}

    scheme = (parsed.scheme or '').lower()

    # Block dangerous schemes
    if scheme in ('javascript', 'data', 'vbscript'):
        return {'allow': False, 'reason': f'Blocked scheme: {scheme}'}

    # Only check host-based rules for http/https/ftp
    if scheme not in ('http', 'https', 'ftp', ''):
        return {'allow': True, 'reason': 'Non-web scheme — allowed'}  # e.g. mailto:

    host = (parsed.hostname or '').lower()

    # Loopback / localhost (except the gateway itself)
    if host in ('localhost', '::1', '0.0.0.0'):
        port = parsed.port
        if host == 'localhost' and port == 8080:
            return {'allow': True, 'reason': 'Gateway origin'}
        return {'allow': False, 'reason': f'Blocked: loopback host {host!r}'}

    # Private IP ranges
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            if host == '127.0.0.1' and parsed.port == 8080:
                return {'allow': True, 'reason': 'Gateway origin'}
            return {'allow': False, 'reason': f'Blocked: loopback IP {ip}'}
        if ip.is_private or ip.is_link_local or ip.is_reserved:
            return {'allow': False, 'reason': f'Blocked: private/reserved IP {ip}'}
    except ValueError:
        pass  # hostname — fine

    # .local / .internal hostnames
    if host.endswith('.local') or host.endswith('.internal') or host.endswith('.lan'):
        return {'allow': False, 'reason': f'Blocked: internal hostname {host!r}'}

    return {'allow': True, 'reason': 'OK'}


# ---------------------------------------------------------------------------
# Voice I/O  —  Whisper STT  +  edge-tts TTS
# ---------------------------------------------------------------------------

import voice as _voice


@app.post('/voice/transcribe')
async def voice_transcribe(
    file: UploadFile = FastAPIFile(...),
    request: Request = None,
):
    """Transcribe uploaded audio to text using OpenAI Whisper.

    Accepts any format supported by Whisper (WebM, WAV, MP3, MP4, M4A, OGG).
    Requires a ``Bearer`` token.  Uses the provider's OpenAI API key if set.
    """
    _require_bearer(request)
    audio_bytes = await file.read()
    # Try to get the provider's OpenAI key for Whisper
    provider_key: str | None = None
    try:
        prov_data = _providers.load_providers()
        openai_entry = next((p for p in prov_data if p.get('id') == 'openai'), None)
        if openai_entry:
            provider_key = openai_entry.get('api_key') or None
    except Exception:
        pass

    text = await asyncio.to_thread(
        _voice.transcribe, audio_bytes, file.filename or 'audio.webm', provider_key
    )
    if text.startswith('[ERROR]'):
        raise HTTPException(status_code=422, detail=text)
    return {'text': text}


@app.post('/voice/speak')
async def voice_speak(payload: dict, request: Request = None):
    """Convert text to speech and return streaming MP3 audio.

    Body: ``{ "text": "...", "voice": "en-US-JennyNeural",
               "rate": "+0%", "pitch": "+0Hz" }``
    """
    _require_bearer(request)
    text  = payload.get('text', '').strip()
    voice = payload.get('voice', _voice.DEFAULT_VOICE)
    rate  = payload.get('rate',  _voice.DEFAULT_RATE)
    pitch = payload.get('pitch', _voice.DEFAULT_PITCH)

    if not text:
        raise HTTPException(status_code=400, detail='text is required')

    async def _gen():
        async for chunk in _voice.speak_stream(text, voice=voice, rate=rate, pitch=pitch):
            yield chunk

    return StreamingResponse(_gen(), media_type='audio/mpeg')


@app.get('/voice/voices')
async def voice_voices(locale: str = '', request: Request = None):
    """List available edge-tts voices, optionally filtered by locale prefix."""
    _require_bearer(request)
    try:
        voices = await _voice.list_voices(locale_prefix=locale)
        return {'voices': voices}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# MCP Server Integration
# ---------------------------------------------------------------------------

class _MCPServerEntry(BaseModel):
    name: str = Field(..., description='Unique server identifier')
    command: str = Field(..., description='Executable to run (e.g. npx, uvx, python)')
    args: list[str] = Field(default_factory=list, description='Command arguments')
    env: dict[str, str] = Field(default_factory=dict, description='Extra environment variables')


@app.get('/mcp/servers')
async def mcp_list_servers(request: Request = None):
    """List all configured MCP servers with their status and discovered tools."""
    _require_bearer(request)
    return {'servers': _mcp.list_servers()}


@app.get('/mcp/servers/{name}')
async def mcp_get_server(name: str, request: Request = None):
    """Get detailed info for a single MCP server."""
    _require_bearer(request)
    info = _mcp.get_server(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f'MCP server {name!r} not found')
    return info


@app.post('/mcp/servers', status_code=201)
async def mcp_add_server(entry: _MCPServerEntry, request: Request = None):
    """Add (or replace) an MCP server and start it immediately."""
    _require_bearer(request)
    srv = _mcp.add_server(entry.model_dump())
    return srv.public_info()


@app.delete('/mcp/servers/{name}')
async def mcp_remove_server(name: str, request: Request = None):
    """Stop and remove an MCP server."""
    _require_bearer(request)
    removed = _mcp.remove_server(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f'MCP server {name!r} not found')
    return {'removed': name}


@app.post('/mcp/servers/{name}/restart')
async def mcp_restart_server(name: str, request: Request = None):
    """Restart an MCP server and re-register its tools."""
    _require_bearer(request)
    ok = _mcp.restart_server(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f'MCP server {name!r} not found')
    info = _mcp.get_server(name)
    return info


@app.post('/mcp/reload')
async def mcp_reload(request: Request = None):
    """Hot-reload MCP config without restarting the gateway."""
    _require_bearer(request)
    result = await asyncio.get_event_loop().run_in_executor(None, _mcp.reload)
    return result


@app.get('/mcp/tools')
async def mcp_list_tools(request: Request = None):
    """List all tools exposed by running MCP servers."""
    _require_bearer(request)
    from tools import tool_runner as _tr
    mcp_tools = [
        {'name': k, 'description': v.get('description', '')}
        for k, v in _tr._REGISTRY.items()
        if '__' in k and any(k.startswith(s['name'] + '__') for s in _mcp.list_servers())
    ]
    return {'tools': mcp_tools, 'count': len(mcp_tools)}


# ---------------------------------------------------------------------------
# #22  Notification & Webhook Push  (/notify/*)
# ---------------------------------------------------------------------------

@app.get('/notify/channels')
async def notify_list_channels(request: Request = None):
    """List all supported notification channels and their configured state."""
    _require_bearer(request)
    return {'channels': _notifier.list_channels()}


@app.post('/notify/{channel}')
async def notify_send(channel: str, request: Request = None):
    """Send a notification via *channel* (telegram | discord | slack).

    Body JSON: ``{message, title?, image_url?}``
    """
    _require_bearer(request)
    body = await request.json()
    message = body.get('message', '').strip()
    if not message:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='message is required')
    result = _notifier.send(
        channel=channel,
        message=message,
        title=body.get('title', ''),
        image_url=body.get('image_url', ''),
    )
    return result


# ---------------------------------------------------------------------------
# #19  Knowledge Base / Notes  (/notes/*)
# ---------------------------------------------------------------------------

@app.post('/notes/save')
async def notes_save(request: Request = None):
    """Append a note to today's knowledge-base file.

    Body JSON: ``{content, url?, title?, tags?}``
    """
    _require_bearer(request)
    body = await request.json()
    content = body.get('content', '').strip()
    if not content:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='content is required')
    result = _notes.save(
        content=content,
        url=body.get('url', ''),
        title=body.get('title', ''),
        tags=body.get('tags', []),
    )
    return result


@app.get('/notes')
async def notes_list(max_days: int = 7, request: Request = None):
    """List recent note files (metadata only)."""
    _require_bearer(request)
    return {'notes': _notes.list_notes(max_days=max(1, min(int(max_days), 90)))}


@app.get('/notes/search')
async def notes_search(q: str = '', request: Request = None):
    """Full-text search across all note files."""
    _require_bearer(request)
    if not q.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='q query parameter is required')
    return {'query': q, 'results': _notes.search(q)}


@app.get('/notes/file')
async def notes_get_file(date: str = '', request: Request = None):
    """Return raw Markdown content of a note file (YYYY-MM-DD, default today)."""
    _require_bearer(request)
    return {'content': _notes.get_note_file(date)}


# ---------------------------------------------------------------------------
# #27  Video Frame Analysis  (/tools/video/*)
# ---------------------------------------------------------------------------

@app.post('/tools/video/frames')
async def video_extract_frames(request: Request = None):
    """Extract evenly-spaced frames from a video URL or local path.

    Body JSON: ``{url, n_frames?: int, quality?: int}``

    Returns ``{frames: [{frame, timestamp_s, b64}]}`` — base-64 JPEG frames.
    """
    _require_bearer(request)
    body = await request.json()
    url = body.get('url', '').strip()
    if not url:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='url is required')

    from tools.video_frames import extract_frames, ffmpeg_available
    if not ffmpeg_available():
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail='ffmpeg is not installed on the server')

    n_frames = int(body.get('n_frames', 5))
    quality = int(body.get('quality', 3))

    loop = asyncio.get_event_loop()
    try:
        frames = await loop.run_in_executor(None, lambda: extract_frames(url, n_frames, quality))
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(exc))

    return {'frames': frames, 'count': len(frames)}


@app.post('/tools/video/describe')
async def video_describe(request: Request = None):
    """Extract frames and describe the video using a vision-capable LLM.

    Body JSON: ``{url, n_frames?: int, provider?: str, model?: str, prompt?: str}``
    """
    _require_bearer(request)
    body = await request.json()
    url = body.get('url', '').strip()
    if not url:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='url is required')

    from tools.video_frames import describe_video
    loop = asyncio.get_event_loop()
    description = await loop.run_in_executor(
        None,
        lambda: describe_video(
            source=url,
            n_frames=int(body.get('n_frames', 5)),
            provider=body.get('provider', ''),
            model=body.get('model', ''),
            prompt=body.get('prompt', ''),
        ),
    )
    return {'description': description}


# ---------------------------------------------------------------------------
# #20  Secure Credential Store  (/credentials/*)
# ---------------------------------------------------------------------------

@app.get('/credentials')
async def credentials_list(request: Request = None):
    """List stored credential names (never the secrets)."""
    _require_bearer(request)
    return {'names': _cred.list_names()}


@app.post('/credentials')
async def credentials_store(request: Request = None):
    """Store a credential.

    Body JSON: ``{name, secret}``
    """
    _require_bearer(request)
    body = await request.json()
    name = body.get('name', '').strip()
    secret = body.get('secret', '')
    if not name or not secret:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='name and secret are required')
    _cred.store(name, secret)
    return {'ok': True, 'name': name}


@app.get('/credentials/{name}')
async def credentials_retrieve(name: str, request: Request = None):
    """Retrieve a stored credential value."""
    _require_bearer(request)
    try:
        value = _cred.retrieve(name)
    except PermissionError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=423, detail=str(exc))  # 423 = Locked
    if value is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f'Credential "{name}" not found')
    return {'name': name, 'secret': value}


@app.delete('/credentials/{name}')
async def credentials_delete(name: str, request: Request = None):
    """Delete a stored credential."""
    _require_bearer(request)
    deleted = _cred.delete(name)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f'Credential "{name}" not found')
    return {'ok': True, 'name': name}


@app.post('/credentials/lock')
async def credentials_lock(request: Request = None):
    """Manually lock the credential store (clears idle timer)."""
    _require_bearer(request)
    _cred.lock()
    return {'ok': True, 'locked': True}


# ---------------------------------------------------------------------------
# #26  A2A — Agent-to-Agent Sessions  (/a2a/*)
# ---------------------------------------------------------------------------

@app.post('/a2a/send')
async def a2a_send(request: Request = None):
    """Dispatch a task to another persona's agent session.

    Body JSON: ``{from_persona, to_persona, task, context?}``

    Returns immediately with a task record (status='pending').  Poll
    ``GET /a2a/tasks/{id}`` for the result.
    """
    _require_bearer(request)
    body = await request.json()
    to_persona = body.get('to_persona', '').strip()
    task = body.get('task', '').strip()
    if not to_persona or not task:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='to_persona and task are required')
    record = _a2a.submit(
        from_persona=body.get('from_persona', 'user'),
        to_persona=to_persona,
        task=task,
        context=body.get('context', ''),
    )
    return record


@app.get('/a2a/tasks')
async def a2a_list_tasks(limit: int = 20, request: Request = None):
    """List recent A2A tasks, newest first."""
    _require_bearer(request)
    return {'tasks': _a2a.list_tasks(limit=min(limit, 100))}


@app.get('/a2a/tasks/{task_id}')
async def a2a_get_task(task_id: str, request: Request = None):
    """Get the status and result of a specific A2A task."""
    _require_bearer(request)
    record = _a2a.get_task(task_id)
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f'Task {task_id!r} not found')
    return record


@app.delete('/a2a/tasks/{task_id}')
async def a2a_cancel_task(task_id: str, request: Request = None):
    """Request cancellation of a pending or running A2A task."""
    _require_bearer(request)
    cancelled = _a2a.cancel(task_id)
    if not cancelled:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f'Task {task_id!r} not found or already finished')
    return {'ok': True, 'task_id': task_id}


# ---------------------------------------------------------------------------
# #21  Extension / Plugin System  (/admin/plugins/*)
# ---------------------------------------------------------------------------

@app.get('/admin/plugins')
async def plugins_list(request: Request = None):
    """List all installed plugins and their status."""
    _require_bearer(request)
    return {'plugins': _plugins.list_plugins()}


@app.get('/admin/plugins/{slug}')
async def plugins_get(slug: str, request: Request = None):
    """Get details for a single installed plugin."""
    _require_bearer(request)
    plugin = _plugins.get_plugin(slug)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f'Plugin "{slug}" not found')
    return plugin


@app.post('/admin/plugins/install')
async def plugins_install(request: Request = None):
    """Install a plugin from a source.

    Body JSON: ``{source}``

    *source* can be:
    - A local directory path
    - An HTTP/HTTPS URL to a ``.zip`` archive
    - A GitHub shorthand ``owner/repo`` or ``owner/repo@branch``
    """
    _require_bearer(request)
    body = await request.json()
    source = body.get('source', '').strip()
    if not source:
        raise HTTPException(status_code=422, detail='source is required')
    loop = asyncio.get_event_loop()
    try:
        manifest = await loop.run_in_executor(None, lambda: _plugins.install(source))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return manifest


@app.delete('/admin/plugins/{slug}')
async def plugins_uninstall(slug: str, request: Request = None):
    """Uninstall a plugin and remove its tools from the registry."""
    _require_bearer(request)
    removed = _plugins.uninstall(slug)
    if not removed:
        raise HTTPException(status_code=404, detail=f'Plugin "{slug}" not found')
    return {'ok': True, 'slug': slug}


@app.post('/admin/plugins/{slug}/enable')
async def plugins_enable(slug: str, request: Request = None):
    """Enable a previously disabled plugin."""
    _require_bearer(request)
    ok = _plugins.enable(slug)
    if not ok:
        raise HTTPException(status_code=404, detail=f'Plugin "{slug}" not found')
    return {'ok': True, 'slug': slug, 'enabled': True}


@app.post('/admin/plugins/{slug}/disable')
async def plugins_disable(slug: str, request: Request = None):
    """Disable a plugin (keeps it installed but unregisters its tools)."""
    _require_bearer(request)
    ok = _plugins.disable(slug)
    if not ok:
        raise HTTPException(status_code=404, detail=f'Plugin "{slug}" not installed or already disabled')
    return {'ok': True, 'slug': slug, 'enabled': False}


@app.post('/admin/plugins/{slug}/reload')
async def plugins_reload(slug: str, request: Request = None):
    """Hot-reload a plugin without restarting the gateway."""
    _require_bearer(request)
    try:
        result = _plugins.reload_plugin(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result
