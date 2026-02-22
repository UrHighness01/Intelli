#!/usr/bin/env python3
"""gateway-ctl — Intelli Agent Gateway operator command-line tool.

A thin CLI that wraps the gateway admin API so operators can manage the
gateway without needing to hand-craft HTTP requests.

Usage
-----
    python gateway_ctl.py --help
    python gateway_ctl.py --url http://localhost:8080 login -u admin -p changeme
    python gateway_ctl.py kill-switch on --reason "maintenance"
    python gateway_ctl.py kill-switch off
    python gateway_ctl.py kill-switch status
    python gateway_ctl.py permissions get alice
    python gateway_ctl.py permissions set alice file.read,noop
    python gateway_ctl.py permissions clear alice
    python gateway_ctl.py audit tail --n 20
    python gateway_ctl.py audit tail --actor alice --action approve --since 2025-01-01T00:00:00Z
    python gateway_ctl.py audit export-csv --output report.csv --actor alice
    python gateway_ctl.py key set openai sk-...
    python gateway_ctl.py key rotate openai sk-new...
    python gateway_ctl.py key status openai
    python gateway_ctl.py key expiry openai
    python gateway_ctl.py key delete openai
    python gateway_ctl.py providers list
    python gateway_ctl.py provider-health list
    python gateway_ctl.py provider-health check openai
    python gateway_ctl.py provider-health expiring --within-days 14
    python gateway_ctl.py consent export alice…
    python gateway_ctl.py consent erase alice…
    python gateway_ctl.py status
    python gateway_ctl.py webhooks add https://my.server/hook --secret mysecret
    python gateway_ctl.py memory agents
    python gateway_ctl.py memory list my-agent
    python gateway_ctl.py memory set my-agent greeting "hello" --ttl 3600
    python gateway_ctl.py memory get my-agent greeting
    python gateway_ctl.py memory delete my-agent greeting
    python gateway_ctl.py memory prune my-agent
    python gateway_ctl.py memory export --output backup.json
    python gateway_ctl.py memory import backup.json
    python gateway_ctl.py content-filter list
    python gateway_ctl.py content-filter add badword --mode literal --label profanity
    python gateway_ctl.py content-filter add '\\bsecret\\b' --mode regex --label secrets
    python gateway_ctl.py content-filter delete 0
    python gateway_ctl.py content-filter reload
    python gateway_ctl.py users list
    python gateway_ctl.py users create alice s3cret
    python gateway_ctl.py users create alice s3cret --role admin
    python gateway_ctl.py users delete alice
    python gateway_ctl.py users password alice newpass

Configuration
-------------
The gateway URL defaults to http://localhost:8080.  Override with --url or
the GATEWAY_URL environment variable.

The admin Bearer token is read from the GATEWAY_TOKEN environment variable or
from the local token cache file (~/.config/intelli/gateway_token).  Use the
``login`` command to populate the cache.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Optional httpx / requests import
# ---------------------------------------------------------------------------
_http_lib: Any = None
_urllib_request: Any = None
_urllib_error: Any = None
_USE_HTTPX: bool = False

try:
    import httpx as _http_lib  # type: ignore[assignment]
    _USE_HTTPX = True
except ImportError:
    try:
        import urllib.request as _urllib_request  # type: ignore[assignment]
        import urllib.error as _urllib_error  # type: ignore[assignment]
    except ImportError:
        print('ERROR: install httpx  (pip install httpx)  to use gateway-ctl', file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------
_TOKEN_CACHE = Path(os.environ.get('GATEWAY_TOKEN_CACHE', '~/.config/intelli/gateway_token')).expanduser()


def _load_cached_token() -> Optional[str]:
    try:
        return _TOKEN_CACHE.read_text().strip() or None
    except FileNotFoundError:
        return None


def _save_token(token: str) -> None:
    _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_CACHE.write_text(token)


def _get_token(args: argparse.Namespace) -> str:
    token = (
        getattr(args, 'token', None)
        or os.environ.get('GATEWAY_TOKEN')
        or _load_cached_token()
    )
    if not token:
        print(
            'ERROR: no auth token found.\n'
            '  Run:  gateway-ctl login -u admin -p <password>\n'
            '  Or set GATEWAY_TOKEN env var.',
            file=sys.stderr,
        )
        sys.exit(1)
    return token


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _url(args: argparse.Namespace, path: str) -> str:
    base = args.url.rstrip('/')
    return f'{base}{path}'


def _request(method: str, url: str, token: Optional[str] = None,
              body: Any = None, *, exit_on_error: bool = True) -> Any:
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    data = json.dumps(body).encode() if body is not None else None

    if _USE_HTTPX:
        with _http_lib.Client(timeout=10.0) as c:
            resp = c.request(method, url, content=data, headers=headers)
        status = resp.status_code
        try:
            result = resp.json()
        except Exception:
            result = resp.text
    else:
        req = _urllib_request.Request(url, data=data, headers=headers, method=method)
        try:
            with _urllib_request.urlopen(req) as r:
                status = r.status
                result = json.loads(r.read())
        except _urllib_error.HTTPError as e:
            status = e.code
            try:
                result = json.loads(e.read())
            except Exception:
                result = str(e)

    if exit_on_error and status >= 400:
        print(f'ERROR HTTP {status}:', file=sys.stderr)
        _pretty(result, file=sys.stderr)
        sys.exit(1)

    return result


def _pretty(data: Any, file=None) -> None:
    if file is None:
        file = sys.stdout
    print(json.dumps(data, indent=2, ensure_ascii=False), file=file)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_login(args: argparse.Namespace) -> None:
    """Authenticate and cache the admin token."""
    result = _request(
        'POST', _url(args, '/admin/login'),
        body={'username': args.username, 'password': args.password},
    )
    token = result.get('token')
    if not token:
        print('ERROR: login response did not include a token', file=sys.stderr)
        _pretty(result, file=sys.stderr)
        sys.exit(1)
    _save_token(token)
    print(f'Logged in as {args.username!r}. Token cached to {_TOKEN_CACHE}')


def cmd_kill_switch(args: argparse.Namespace) -> None:
    """Manage the gateway kill-switch."""
    token = _get_token(args)
    action = args.ks_action

    if action == 'status':
        result = _request('GET', _url(args, '/admin/kill-switch'), token=token)
        _pretty(result)

    elif action == 'on':
        reason = args.reason or ''
        result = _request(
            'POST', _url(args, '/admin/kill-switch'),
            token=token,
            body={'reason': reason},
        )
        _pretty(result)

    elif action == 'off':
        result = _request('DELETE', _url(args, '/admin/kill-switch'), token=token)
        _pretty(result)


def cmd_permissions(args: argparse.Namespace) -> None:
    """Manage per-user tool permissions."""
    token = _get_token(args)
    action = args.perm_action
    username = args.username

    if action == 'get':
        result = _request('GET', _url(args, f'/admin/users/{username}/permissions'), token=token)
        _pretty(result)

    elif action == 'set':
        tools = [t.strip() for t in args.tools.split(',') if t.strip()]
        result = _request(
            'PUT', _url(args, f'/admin/users/{username}/permissions'),
            token=token,
            body={'allowed_tools': tools},
        )
        _pretty(result)

    elif action == 'clear':
        result = _request(
            'PUT', _url(args, f'/admin/users/{username}/permissions'),
            token=token,
            body={'allowed_tools': None},
        )
        _pretty(result)


def cmd_audit(args: argparse.Namespace) -> None:
    """Fetch or export audit log entries."""
    token = _get_token(args)
    action = getattr(args, 'audit_action', 'tail')

    # Build query string from common filter args
    def _audit_params(tail_default: int = 20) -> str:
        from urllib.parse import urlencode
        params: dict = {'tail': getattr(args, 'n', tail_default)}
        for k in ('actor', 'action', 'since', 'until'):
            v = getattr(args, k, '') or ''
            if v:
                params[k] = v
        return urlencode(params)

    if action == 'tail':
        qs = _audit_params()
        result = _request('GET', _url(args, f'/admin/audit?{qs}'), token=token)
        entries = result.get('entries', [])
        for entry in entries:
            ts    = entry.get('ts', '')
            event = entry.get('event', '')
            actor = entry.get('actor', '')
            details = entry.get('details', {})
            print(f'{ts}  [{actor}]  {event}  {json.dumps(details)}')
        print(f'\n--- {len(entries)} entries ---')

    elif action == 'export-csv':
        qs = _audit_params(tail_default=1000)
        url = _url(args, f'/admin/audit/export.csv?{qs}')
        # Fetch raw CSV bytes
        raw = _request('GET', url, token=token, exit_on_error=True)
        # _request JSON-decodes; for CSV we need to redo the call with raw response
        import urllib.request as _ur
        req = _ur.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with _ur.urlopen(req, timeout=30) as resp:
                content = resp.read().decode('utf-8')
        except Exception as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            sys.exit(1)
        out_path = getattr(args, 'output', None) or 'audit.csv'
        Path(out_path).write_text(content, encoding='utf-8')
        line_count = content.count('\n') - 1  # subtract header
        print(f'Saved {line_count} entries to {out_path}')

    elif action == 'follow':
        import time as _time
        interval = getattr(args, 'interval', 5.0)
        seen: set = set()
        print(f'Following audit log — polling every {interval}s. Ctrl-C to stop.')
        try:
            while True:
                qs = _audit_params(tail_default=50)
                result = _request('GET', _url(args, f'/admin/audit?{qs}'), token=token)
                entries = result.get('entries', [])
                new_entries = [e for e in entries if e.get('ts', '') not in seen]
                for entry in sorted(new_entries, key=lambda e: e.get('ts', '')):
                    ts      = entry.get('ts', '')
                    event   = entry.get('event', '')
                    actor   = entry.get('actor', '')
                    details = entry.get('details', {})
                    print(f'{ts}  [{actor}]  {event}  {json.dumps(details)}')
                    seen.add(ts)
                _time.sleep(interval)
        except KeyboardInterrupt:
            print('\nStopped.')


def cmd_key(args: argparse.Namespace) -> None:
    """Manage provider API keys."""
    token = _get_token(args)
    action = args.key_action
    provider = args.provider

    if action == 'set':
        body: dict = {'key': args.key}
        if args.ttl_days is not None:
            body['ttl_days'] = args.ttl_days
        result = _request('POST', _url(args, f'/admin/providers/{provider}/key'),
                          token=token, body=body)
        _pretty(result)

    elif action == 'rotate':
        body = {'key': args.key}
        if args.ttl_days is not None:
            body['ttl_days'] = args.ttl_days
        result = _request('POST', _url(args, f'/admin/providers/{provider}/key/rotate'),
                          token=token, body=body)
        _pretty(result)

    elif action == 'status':
        result = _request('GET', _url(args, f'/admin/providers/{provider}/key/status'),
                          token=token)
        _pretty(result)

    elif action == 'expiry':
        result = _request('GET', _url(args, f'/admin/providers/{provider}/key/expiry'),
                          token=token)
        _pretty(result)

    elif action == 'delete':
        result = _request('DELETE', _url(args, f'/admin/providers/{provider}/key'),
                          token=token)
        _pretty(result)


def cmd_providers(args: argparse.Namespace) -> None:
    """List configured providers."""
    token = _get_token(args)
    action = args.prov_action

    if action == 'list':
        result = _request('GET', _url(args, '/providers'), token=token)
        providers = result.get('providers', [])
        for p in providers:
            status = '✓ configured' if p.get('configured') else '✗ not configured'
            print(f"  {p['name']:15s}  {status}")

    elif action == 'expiring':
        within = getattr(args, 'within_days', 7)
        result = _request('GET', _url(args, f'/admin/providers/expiring?within_days={within}'),
                          token=token)
        _pretty(result)


def cmd_consent(args: argparse.Namespace) -> None:
    """Manage GDPR consent data."""
    token = _get_token(args)
    action = args.consent_action
    actor = args.actor

    if action == 'export':
        result = _request('GET', _url(args, f'/consent/export/{actor}'), token=token)
        _pretty(result)

    elif action == 'erase':
        confirm = getattr(args, 'yes', False)
        if not confirm:
            ans = input(f'This will permanently erase ALL consent data for actor {actor!r}. '
                        f'Type yes to confirm: ')
            if ans.strip().lower() != 'yes':
                print('Aborted.')
                return
        result = _request('DELETE', _url(args, f'/consent/export/{actor}'), token=token)
        _pretty(result)

    elif action == 'timeline':
        n = getattr(args, 'n', 100)
        origin = getattr(args, 'origin', '') or ''
        params = f'limit={n}'
        if origin:
            params += f'&origin={origin}'
        result = _request('GET', _url(args, f'/consent/timeline?{params}'), token=token)
        _pretty(result)


def cmd_webhooks(args: argparse.Namespace) -> None:
    """Manage approval webhooks."""
    token = _get_token(args)
    action = args.wh_action

    if action == 'list':
        result = _request('GET', _url(args, '/admin/webhooks'), token=token)
        hooks = result.get('webhooks', [])
        if not hooks:
            print('No webhooks registered.')
        for h in hooks:
            events = ', '.join(h.get('events', []))
            print(f"  {h['id']}  {h['url']}  [{events}]  created: {h.get('created_at', '')}")

    elif action == 'add':
        body: dict = {'url': args.url}
        if args.events:
            body['events'] = [e.strip() for e in args.events.split(',')]
        if getattr(args, 'secret', None):
            body['secret'] = args.secret
        result = _request('POST', _url(args, '/admin/webhooks'), token=token, body=body)
        _pretty(result)

    elif action == 'delete':
        result = _request('DELETE', _url(args, f'/admin/webhooks/{args.id}'), token=token)
        _pretty(result)


def cmd_schedule(args: argparse.Namespace) -> None:
    """Manage scheduled tasks."""
    token = _get_token(args)
    action = args.sched_action

    if action == 'list':
        result = _request('GET', _url(args, '/admin/schedule'), token=token)
        tasks = result.get('tasks', [])
        if not tasks:
            print('No scheduled tasks.')
            return
        show_next = getattr(args, 'next', False)
        if show_next:
            from datetime import datetime, timezone

            def _parse_nxt(t: dict) -> datetime:
                nxt = t.get('next_run_at', '')
                try:
                    return datetime.fromisoformat(nxt.replace('Z', '+00:00'))
                except Exception:
                    return datetime.max.replace(tzinfo=timezone.utc)

            tasks = sorted(tasks, key=_parse_nxt)
        for t in tasks:
            enabled  = '\u25cf' if t.get('enabled') else '\u25cb'
            interval = t.get('interval_seconds', '?')
            runs     = t.get('run_count', 0)
            extra    = ''
            if show_next:
                from datetime import datetime, timezone
                nxt = t.get('next_run_at', '')
                try:
                    dt   = datetime.fromisoformat(nxt.replace('Z', '+00:00'))
                    secs = (dt - datetime.now(tz=timezone.utc)).total_seconds()
                    if secs < 0:
                        extra = '  (overdue)'
                    elif secs < 60:
                        extra = f'  in {secs:.0f}s'
                    elif secs < 3600:
                        extra = f'  in {secs/60:.0f}m'
                    else:
                        extra = f'  in {secs/3600:.1f}h'
                except Exception:
                    pass
            print(f"  {enabled} {t['id'][:8]}  {t['name']:24s}  "
                  f"tool={t.get('tool'):16s}  every={interval}s  runs={runs}{extra}")

    elif action == 'get':
        result = _request('GET', _url(args, f"/admin/schedule/{args.task_id}"), token=token)
        _pretty(result)

    elif action == 'create':
        import json as _json
        try:
            task_args = _json.loads(args.args) if args.args else {}
        except ValueError as exc:
            print(f'Error: --args must be valid JSON ({exc})')
            return
        body = {
            'name': args.name,
            'tool': args.tool,
            'args': task_args,
            'interval_seconds': args.interval,
        }
        if args.disabled:
            body['enabled'] = False
        result = _request('POST', _url(args, '/admin/schedule'), token=token, body=body)
        _pretty(result)

    elif action == 'delete':
        result = _request('DELETE', _url(args, f"/admin/schedule/{args.task_id}"), token=token)
        _pretty(result)

    elif action == 'enable':
        result = _request('PATCH', _url(args, f"/admin/schedule/{args.task_id}"),
                          token=token, body={'enabled': True})
        _pretty(result)

    elif action == 'disable':
        result = _request('PATCH', _url(args, f"/admin/schedule/{args.task_id}"),
                          token=token, body={'enabled': False})
        _pretty(result)

    elif action == 'trigger':
        result = _request('POST', _url(args, f"/admin/schedule/{args.task_id}/trigger"),
                          token=token)
        _pretty(result)

    elif action == 'history':
        hist_url = _url(args, f"/admin/schedule/{args.task_id}/history")
        if getattr(args, 'n', None):
            hist_url += f"?limit={args.n}"
        result = _request('GET', hist_url, token=token)
        records = result.get('history', [])
        if not records:
            print('No history yet.')
            return
        for rec in records:
            ok  = '\u2713' if rec.get('ok') else '\u2717'
            dur = rec.get('duration_seconds', '?')
            ts  = rec.get('timestamp', '')
            err = rec.get('error') or ''
            print(f"  {ok}  #{rec.get('run', '?'):4}  {ts}  {dur:.3f}s  {err}")


def cmd_approvals(args: argparse.Namespace) -> None:
    """Manage the approval queue and auto-reject timeout."""
    token = _get_token(args)
    action = args.appr_action

    if action == 'list':
        result = _request('GET', _url(args, '/approvals'), token=token)
        pending = result.get('pending', {})
        if not pending:
            print('Queue is empty \u2014 no pending approvals.')
            return
        print(f'Pending approvals ({len(pending)}):')
        for id_, item in (pending.items() if hasattr(pending, 'items') else enumerate(pending)):
            item_dict = item if isinstance(item, dict) else {}
            payload = item_dict.get('payload', {})
            tool = (payload.get('tool') if isinstance(payload, dict) else None) or '?'
            risk = item_dict.get('risk', '?')
            enqueued = item_dict.get('enqueued_at', '')
            age = ''
            if enqueued:
                import time as _t
                secs = int(_t.time() - enqueued)
                age = f'  age={secs}s'
            print(f'  #{id_}  tool={tool}  risk={risk}{age}')

    elif action == 'approve':
        result = _request('POST', _url(args, f'/approvals/{args.id}/approve'), token=token)
        print(f"Approved \u2713 #{args.id}: status={result.get('status')}")

    elif action == 'reject':
        result = _request('POST', _url(args, f'/approvals/{args.id}/reject'), token=token)
        print(f"Rejected \u2717 #{args.id}: status={result.get('status')}")

    elif action == 'timeout':
        sub_action = args.timeout_action
        if sub_action == 'get':
            result = _request('GET', _url(args, '/admin/approvals/config'), token=token)
            secs = result.get('timeout_seconds', 0)
            state = 'disabled' if secs == 0 else f'auto-reject after {secs}s'
            print(f'Approval timeout: {state}')
            print(f'  timeout_seconds: {secs}')
        elif sub_action == 'set':
            if args.seconds < 0:
                print('Error: seconds must be >= 0 (0 = disabled)',
                      file=__import__('sys').stderr)
                raise SystemExit(1)
            result = _request('PUT', _url(args, '/admin/approvals/config'), token=token,
                              body={'timeout_seconds': args.seconds})
            s = result.get('timeout_seconds', 0)
            print(f'Updated: timeout_seconds = {s}  '
                  f'({"disabled" if s == 0 else f"auto-reject after {s}s"})')


def cmd_capabilities(args: argparse.Namespace) -> None:
    """Browse tool capability manifests."""
    token = _get_token(args)
    action = args.cap_action

    if action == 'list':
        result = _request('GET', _url(args, '/tools/capabilities'), token=token)
        tools = result.get('tools', [])
        if not tools:
            print('No capability manifests found.')
            return
        # Column widths
        w_tool  = max(len(t.get('tool', '')) for t in tools)
        w_risk  = 6
        header = f"{'Tool':<{w_tool}}  {'Risk':<{w_risk}}  Approval  Capabilities"
        print(header)
        print('-' * len(header))
        for t in sorted(tools, key=lambda x: x.get('tool', '')):
            tool = t.get('tool', '?')
            risk = t.get('risk_level', '?')
            appr = 'yes' if t.get('requires_approval') else 'no '
            req  = ', '.join(t.get('required_capabilities', [])) or '—'
            opt  = t.get('optional_capabilities', [])
            caps = req + (f'  (+opt: {", ".join(opt)})' if opt else '')
            print(f"{tool:<{w_tool}}  {risk:<{w_risk}}  {appr}       {caps}")
        print(f'\n{len(tools)} manifest(s) shown.')

    elif action == 'show':
        result = _request('GET', _url(args, '/tools/capabilities'), token=token)
        tools  = result.get('tools', [])
        target = args.tool.lower()
        match  = next((t for t in tools if t.get('tool', '').lower() == target), None)
        if match is None:
            print(f'No manifest found for tool: {args.tool}', file=__import__('sys').stderr)
            raise SystemExit(1)
        print(f"Tool:           {match.get('tool')}")
        print(f"Display name:   {match.get('display_name', '—')}")
        print(f"Description:    {match.get('description', '—')}")
        print(f"Risk level:     {match.get('risk_level', '?')}")
        print(f"Requires appr.: {'yes' if match.get('requires_approval') else 'no'}")
        req  = match.get('required_capabilities', [])
        opt  = match.get('optional_capabilities', [])
        print(f"Required caps:  {', '.join(req) if req else '—'}")
        print(f"Optional caps:  {', '.join(opt) if opt else '—'}")


def cmd_alerts(args: argparse.Namespace) -> None:
    """Manage the approval-queue depth alert configuration."""
    token = _get_token(args)
    action = args.alert_action

    if action == 'status':
        result = _request('GET', _url(args, '/admin/alerts/config'), token=token)
        threshold = result.get('approval_queue_threshold', 0)
        state = 'disabled' if threshold == 0 else f'fires when pending ≥ {threshold}'
        print(f'Approval-queue alert: {state}')
        print(f'  approval_queue_threshold: {threshold}')

    elif action == 'set':
        if args.threshold < 0:
            print('Error: threshold must be >= 0 (0 = disabled)', file=__import__('sys').stderr)
            raise SystemExit(1)
        result = _request('PUT', _url(args, '/admin/alerts/config'), token=token,
                          body={'approval_queue_threshold': args.threshold})
        t = result.get('approval_queue_threshold', 0)
        print(f'Updated: approval_queue_threshold = {t}  '
              f'({"disabled" if t == 0 else f"fires when pending ≥ {t}"})')


def cmd_rate_limits(args: argparse.Namespace) -> None:
    """Manage runtime rate-limit configuration."""
    token = _get_token(args)
    action = args.rl_action

    if action == 'status':
        result = _request('GET', _url(args, '/admin/rate-limits'), token=token)
        cfg = result.get('config', {})
        print('Rate-limit config:')
        for k, v in cfg.items():
            print(f'  {k}: {v}')
        usage = result.get('usage', {})
        total = usage.get('total_tracked', 0)
        print(f'\nActive clients: {total}')

    elif action == 'set':
        body: dict = {}
        if args.max_requests is not None:
            body['max_requests'] = args.max_requests
        if args.window_seconds is not None:
            body['window_seconds'] = args.window_seconds
        if args.burst is not None:
            body['burst'] = args.burst
        if args.enabled is not None:
            body['enabled'] = args.enabled
        if args.user_max_requests is not None:
            body['user_max_requests'] = args.user_max_requests
        if args.user_window_seconds is not None:
            body['user_window_seconds'] = args.user_window_seconds
        result = _request('PUT', _url(args, '/admin/rate-limits'), token=token, body=body)
        _pretty(result)

    elif action == 'reset-client':
        result = _request('DELETE', _url(args, f'/admin/rate-limits/clients/{args.client}'),
                          token=token)
        _pretty(result)

    elif action == 'reset-user':
        result = _request('DELETE', _url(args, f'/admin/rate-limits/users/{args.username}'),
                          token=token)
        _pretty(result)


def cmd_provider_health(args: argparse.Namespace) -> None:
    """Check provider key and adapter availability."""
    token = _get_token(args)
    ph_action = getattr(args, 'ph_action', 'check')
    _ICONS = {'ok': '✓', 'no_key': '✗', 'unavailable': '!'}

    def _print_health(name: str, result: dict) -> None:
        icon = _ICONS.get(result.get('status', ''), '?')
        print(f"  {icon} {name}: {result.get('status')}  "
              f"(configured={result.get('configured')}, available={result.get('available')})")

    if ph_action == 'check':
        result = _request('GET', _url(args, f'/admin/providers/{args.provider}/health'), token=token)
        _print_health(args.provider, result)

    elif ph_action == 'list':
        for prov in ('openai', 'anthropic', 'openrouter', 'ollama'):
            result = _request('GET', _url(args, f'/admin/providers/{prov}/health'), token=token)
            _print_health(prov, result)

    elif ph_action == 'expiring':
        within_days = getattr(args, 'within_days', 7)
        result = _request('GET', _url(args, f'/admin/providers/expiring?within_days={within_days}'),
                          token=token)
        rows = result.get('expiring', [])
        if not rows:
            print(f'No keys expiring within {within_days} days.')
        else:
            for row in rows:
                print(f"  {row.get('provider')}  expires {row.get('expires_at')}")


def cmd_users(args: argparse.Namespace) -> None:
    """Manage gateway user accounts."""
    token = _get_token(args)
    action = args.user_action

    if action == 'list':
        result = _request('GET', _url(args, '/admin/users'), token=token)
        users = result.get('users', [])
        if not users:
            print('No users found.')
            return
        print(f"  {'Username':<24}  {'Roles':<16}  Restrictions")
        print(f"  {'─'*24}  {'─'*16}  {'─'*12}")
        for u in users:
            roles = ', '.join(u.get('roles', []))
            restr = 'yes' if u.get('has_tool_restrictions') else 'no'
            print(f"  {u.get('username', ''):<24}  {roles:<16}  {restr}")
        print(f'\n{len(users)} user(s).')

    elif action == 'create':
        body: dict = {
            'username': args.username,
            'password': args.password,
            'roles': [args.role],
        }
        result = _request('POST', _url(args, '/admin/users'), token=token, body=body)
        _pretty(result)

    elif action == 'delete':
        result = _request('DELETE',
                          _url(args, f'/admin/users/{args.username}'),
                          token=token)
        _pretty(result)

    elif action == 'password':
        body = {'new_password': args.new_password}
        result = _request('POST',
                          _url(args, f'/admin/users/{args.username}/password'),
                          token=token, body=body)
        _pretty(result)

    elif action == 'permissions':
        perm_action = args.user_perm_action
        username    = args.username
        if perm_action == 'get':
            result  = _request('GET',
                               _url(args, f'/admin/users/{username}/permissions'),
                               token=token)
            allowed = result.get('allowed_tools')
            if allowed is None:
                print(f'{username}: unrestricted (all tools allowed)')
            elif not allowed:
                print(f'{username}: restricted — no tools allowed')
            else:
                print(f'{username}: allowed tools ({len(allowed)}):')
                for t in sorted(allowed):
                    print(f'  \u2022 {t}')
        elif perm_action == 'set':
            tools  = [t.strip() for t in args.tools.split(',') if t.strip()]
            result = _request('PUT',
                              _url(args, f'/admin/users/{username}/permissions'),
                              token=token, body={'allowed_tools': tools})
            _pretty(result)
        elif perm_action == 'clear':
            result = _request('PUT',
                              _url(args, f'/admin/users/{username}/permissions'),
                              token=token, body={'allowed_tools': None})
            _pretty(result)


def cmd_content_filter(args: argparse.Namespace) -> None:
    """Manage runtime content-filter deny rules."""
    token = _get_token(args)
    action = args.cf_action

    if action == 'list':
        result = _request('GET', _url(args, '/admin/content-filter/rules'), token=token)
        rules = result.get('rules', [])
        if not rules:
            print('No content-filter rules defined.')
            return
        w = max((len(r.get('pattern', '')) for r in rules), default=10)
        print(f"  {'#':>3}  {'Mode':<8}  {'Label':<20}  Pattern")
        print(f"  {'─'*3}  {'─'*8}  {'─'*20}  {'─'*w}")
        for i, r in enumerate(rules):
            label   = r.get('label', '') or ''
            mode    = r.get('mode', 'literal')
            pattern = r.get('pattern', '')
            print(f"  {i:>3}  {mode:<8}  {label:<20}  {pattern}")
        print(f'\n{len(rules)} rule(s).')

    elif action == 'add':
        body: dict = {'pattern': args.pattern, 'mode': args.mode}
        if args.label:
            body['label'] = args.label
        result = _request('POST', _url(args, '/admin/content-filter/rules'),
                          token=token, body=body)
        _pretty(result)

    elif action == 'delete':
        result = _request('DELETE',
                          _url(args, f'/admin/content-filter/rules/{args.index}'),
                          token=token)
        _pretty(result)

    elif action == 'reload':
        result = _request('POST', _url(args, '/admin/content-filter/reload'), token=token)
        count = result.get('rules_loaded', result.get('count', '?'))
        print(f'Reloaded {count} rule(s) from disk and environment.')


def cmd_metrics(args: argparse.Namespace) -> None:
    """Print per-tool invocation counts and latency from /admin/metrics/tools."""
    token = _get_token(args)
    action = args.met_action
    result = _request('GET', _url(args, '/admin/metrics/tools'), token=token)
    tools = result.get('tools', [])

    if action == 'top':
        n = getattr(args, 'n', 5)
        tools = tools[:n]

    if not tools:
        print('No tool calls recorded yet.')
        return

    HDR = f"{'Tool':<35} {'Calls':>8} {'p50 ms':>10} {'Mean ms':>10}"
    SEP = '\u2500' * len(HDR)
    print(HDR)
    print(SEP)
    for t in tools:
        p50  = t.get('p50_seconds')
        mean = t.get('mean_seconds')
        p50_s  = f'{p50  * 1000:.1f}' if p50  is not None else '\u2014'
        mean_s = f'{mean * 1000:.1f}' if mean is not None else '\u2014'
        print(f"{t.get('tool', ''):<35} {t.get('calls', 0):>8} {p50_s:>10} {mean_s:>10}")
    print(SEP)
    print(f"Total: {result.get('total', 0)} calls across {len(result.get('tools', []))} tool(s)")


def cmd_status(args: argparse.Namespace) -> None:
    """Print a high-level gateway status summary."""
    token = _get_token(args)
    result = _request('GET', _url(args, '/admin/status'), token=token)
    ks = result.get('kill_switch_active', False)
    ks_icon = '\U0001f534' if ks else '\U0001f7e2'
    print(f"{ks_icon}  Intelli Gateway  v{result.get('version', '?')}")
    print(f"   Uptime            : {result.get('uptime_seconds', '?')} s")
    print(f"   Kill-switch       : {'ACTIVE \u2014 ' + str(result.get('kill_switch_reason')) if ks else 'off'}")
    print(f"   Tool calls total  : {result.get('tool_calls_total', 0)}")
    print(f"   Pending approvals : {result.get('pending_approvals', 0)}")
    print(f"   Scheduler tasks   : {result.get('scheduler_tasks', 0)}")
    print(f"   Memory agents     : {result.get('memory_agents', 0)}")


def cmd_memory(args: argparse.Namespace) -> None:
    """Manage per-agent memory entries."""
    token = _get_token(args)
    action = args.mem_action

    if action == 'agents':
        result = _request('GET', _url(args, '/agents'), token=token)
        agents = result.get('agents', [])
        if not agents:
            print('No agents with stored memory.')
        for agent_id in agents:
            print(f'  {agent_id}')

    elif action == 'list':
        result = _request('GET', _url(args, f'/agents/{args.agent_id}/memory'), token=token)
        entries = result.get('memory', {})
        if not entries:
            print('No memory entries.')
            return
        show_meta = getattr(args, 'meta', False)
        import time as _t
        now = _t.time()
        for key, value in entries.items():
            if show_meta:
                meta = _request('GET', _url(args, f'/agents/{args.agent_id}/memory/{key}'),
                                token=token, exit_on_error=False)
                exp = meta.get('expires_at') if isinstance(meta, dict) else None
                if exp is None:
                    exp_str = 'no expiry'
                else:
                    secs_left = exp - now
                    if secs_left <= 0:
                        exp_str = 'EXPIRED'
                    elif secs_left < 60:
                        exp_str = f'expires in {int(secs_left)}s'
                    elif secs_left < 3600:
                        exp_str = f'expires in {int(secs_left / 60)}m'
                    else:
                        exp_str = f'expires in {int(secs_left / 3600)}h'
                print(f'  {key} = {json.dumps(value)}  [{exp_str}]')
            else:
                print(f'  {key} = {json.dumps(value)}')

    elif action == 'get':
        result = _request('GET', _url(args, f'/agents/{args.agent_id}/memory/{args.key}'), token=token)
        _pretty(result)

    elif action == 'set':
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value   # treat as plain string
        body: dict = {'value': value}
        if getattr(args, 'ttl', None) is not None:
            body['ttl_seconds'] = args.ttl
        result = _request('POST', _url(args, f'/agents/{args.agent_id}/memory'), token=token,
                          body={'key': args.key, **body})
        _pretty(result)

    elif action == 'delete':
        result = _request('DELETE', _url(args, f'/agents/{args.agent_id}/memory/{args.key}'), token=token)
        _pretty(result)

    elif action == 'prune':
        result = _request('POST', _url(args, f'/agents/{args.agent_id}/memory/prune'), token=token)
        print(f"Pruned {result.get('pruned', 0)} expired keys from agent '{args.agent_id}'.")

    elif action == 'clear':
        result = _request('DELETE', _url(args, f'/agents/{args.agent_id}/memory'), token=token)
        _pretty(result)

    elif action == 'export':
        result = _request('GET', _url(args, '/admin/memory/export'), token=token)
        if getattr(args, 'output', None):
            Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(f"Exported {result.get('agent_count', 0)} agents ({result.get('key_count', 0)} keys) to {args.output}")
        else:
            _pretty(result)

    elif action == 'import':
        data = json.loads(Path(args.file).read_text(encoding='utf-8'))
        merge = not getattr(args, 'replace', False)
        result = _request('POST', _url(args, '/admin/memory/import'), token=token,
                          body={'data': data, 'merge': merge})
        _pretty(result)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='gateway-ctl',
        description='Intelli Agent Gateway operator CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('--url', default=os.environ.get('GATEWAY_URL', 'http://localhost:8080'),
                   help='Gateway base URL (default: $GATEWAY_URL or http://localhost:8080)')
    p.add_argument('--token', default=None, help='Admin Bearer token (overrides cache/env)')

    sub = p.add_subparsers(dest='command', required=True)

    # ---- login ----
    login = sub.add_parser('login', help='Authenticate and cache admin token')
    login.add_argument('-u', '--username', required=True)
    login.add_argument('-p', '--password', required=True)
    login.set_defaults(func=cmd_login)

    # ---- kill-switch ----
    ks = sub.add_parser('kill-switch', help='Manage the emergency kill-switch')
    ks_sub = ks.add_subparsers(dest='ks_action', required=True)

    ks_on = ks_sub.add_parser('on', help='Activate the kill-switch')
    ks_on.add_argument('--reason', default='', help='Reason for activation')

    ks_sub.add_parser('off', help='Deactivate the kill-switch')
    ks_sub.add_parser('status', help='Show current kill-switch state')
    ks.set_defaults(func=cmd_kill_switch)

    # ---- permissions ----
    perm = sub.add_parser('permissions', help='Manage per-user tool permissions')
    perm_sub = perm.add_subparsers(dest='perm_action', required=True)

    perm_get = perm_sub.add_parser('get', help='Get tool allow-list for a user')
    perm_get.add_argument('username')

    perm_set = perm_sub.add_parser('set', help='Set tool allow-list for a user')
    perm_set.add_argument('username')
    perm_set.add_argument('tools', help='Comma-separated list of allowed tool names')

    perm_clear = perm_sub.add_parser('clear', help='Remove tool restriction (allow all tools)')
    perm_clear.add_argument('username')
    perm.set_defaults(func=cmd_permissions)

    # ---- audit ----
    audit = sub.add_parser('audit', help='View or export audit log')
    audit_sub = audit.add_subparsers(dest='audit_action', required=True)

    audit_tail = audit_sub.add_parser('tail', help='Stream recent audit entries to stdout')
    audit_tail.add_argument('--n', type=int, default=20, dest='n', help='Max entries (default 20)')
    audit_tail.add_argument('--actor',  default='', help='Filter by actor substring')
    audit_tail.add_argument('--action', default='', help='Filter by event/action substring')
    audit_tail.add_argument('--since',  default='', metavar='ISO8601',
                            help='Exclude entries before this datetime (ISO-8601)')
    audit_tail.add_argument('--until',  default='', metavar='ISO8601',
                            help='Exclude entries after this datetime (ISO-8601)')

    audit_csv = audit_sub.add_parser('export-csv', help='Download filtered audit log as CSV')
    audit_csv.add_argument('--output', '-o', default='audit.csv', metavar='FILE',
                           help='Output file path (default: audit.csv)')
    audit_csv.add_argument('--n', type=int, default=1000, dest='n', help='Max entries (default 1000)')
    audit_csv.add_argument('--actor',  default='', help='Filter by actor substring')
    audit_csv.add_argument('--action', default='', help='Filter by event/action substring')
    audit_csv.add_argument('--since',  default='', metavar='ISO8601')
    audit_csv.add_argument('--until',  default='', metavar='ISO8601')

    audit_follow = audit_sub.add_parser('follow',
                                         help='Poll audit log and print new entries (like tail -f)')
    audit_follow.add_argument('--interval', type=float, default=5.0, metavar='SECS',
                              help='Poll interval in seconds (default 5)')
    audit_follow.add_argument('--n', type=int, default=50, dest='n',
                              help='Max entries per fetch (default 50)')
    audit_follow.add_argument('--actor',  default='', help='Filter by actor substring')
    audit_follow.add_argument('--action', default='', help='Filter by event/action substring')

    audit.set_defaults(func=cmd_audit)

    # ---- key ----
    key = sub.add_parser('key', help='Manage provider API keys')
    key_sub = key.add_subparsers(dest='key_action', required=True)

    key_set = key_sub.add_parser('set', help='Store a provider API key')
    key_set.add_argument('provider')
    key_set.add_argument('key', help='API key value')
    key_set.add_argument('--ttl-days', type=int, default=None, dest='ttl_days',
                         help='Key TTL in days (0 = no expiry)')

    key_rot = key_sub.add_parser('rotate', help='Rotate a provider API key')
    key_rot.add_argument('provider')
    key_rot.add_argument('key', help='New API key value')
    key_rot.add_argument('--ttl-days', type=int, default=None, dest='ttl_days')

    key_stat = key_sub.add_parser('status', help='Check key existence and expiry')
    key_stat.add_argument('provider')

    key_exp = key_sub.add_parser('expiry', help='Show full TTL metadata for a key')
    key_exp.add_argument('provider')

    key_del = key_sub.add_parser('delete', help='Remove a stored key')
    key_del.add_argument('provider')
    key.set_defaults(func=cmd_key)

    # ---- providers ----
    prov = sub.add_parser('providers', help='List providers')
    prov_sub = prov.add_subparsers(dest='prov_action', required=True)
    prov_sub.add_parser('list', help='List all providers and their configuration status')
    prov_exp = prov_sub.add_parser('expiring', help='List keys expiring soon')
    prov_exp.add_argument('--within-days', type=float, default=7, dest='within_days')
    prov.set_defaults(func=cmd_providers)

    # ---- consent ----
    con = sub.add_parser('consent', help='Manage GDPR consent data')
    con_sub = con.add_subparsers(dest='consent_action', required=True)

    con_exp = con_sub.add_parser('export', help='Export all data for an actor (GDPR DSAR)')
    con_exp.add_argument('actor')

    con_era = con_sub.add_parser('erase', help='Erase all data for an actor (GDPR Art. 17)')
    con_era.add_argument('actor')
    con_era.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')

    con_tl = con_sub.add_parser('timeline', help='View recent consent timeline')
    con_tl.add_argument('--n', type=int, default=100)
    con_tl.add_argument('--origin', default='')
    con.set_defaults(func=cmd_consent)

    # ---- webhooks ----
    wh = sub.add_parser('webhooks', help='Manage approval event webhooks')
    wh_sub = wh.add_subparsers(dest='wh_action', required=True)

    wh_sub.add_parser('list', help='List all registered webhooks')

    wh_add = wh_sub.add_parser('add', help='Register a new webhook URL')
    wh_add.add_argument('url', help='HTTP/HTTPS callback URL')
    wh_add.add_argument('--events', default='',
                        help='Comma-separated events (default: all). '
                             'Options: approval.created, approval.approved, approval.rejected')
    wh_add.add_argument('--secret', default='', metavar='SECRET',
                        help='HMAC-SHA256 signing secret; sets X-Intelli-Signature-256 on each delivery')

    wh_del = wh_sub.add_parser('delete', help='Delete a webhook by ID')
    wh_del.add_argument('id', help='Webhook UUID')
    wh.set_defaults(func=cmd_webhooks)

    # ---- rate-limits ----
    rl = sub.add_parser('rate-limits', help='Manage runtime rate-limit configuration')
    rl_sub = rl.add_subparsers(dest='rl_action', required=True)

    rl_sub.add_parser('status', help='Show current config and active-client count')

    rl_set = rl_sub.add_parser('set', help='Update rate-limit settings at runtime')
    rl_set.add_argument('--max-requests', type=int, default=None, dest='max_requests')
    rl_set.add_argument('--window-seconds', type=float, default=None, dest='window_seconds')
    rl_set.add_argument('--burst', type=int, default=None)
    rl_set.add_argument('--enabled', type=lambda x: x.lower() not in ('0', 'false', 'no'),
                        default=None, metavar='true|false')
    rl_set.add_argument('--user-max-requests', type=int, default=None, dest='user_max_requests')
    rl_set.add_argument('--user-window-seconds', type=float, default=None,
                        dest='user_window_seconds')

    rl_rc = rl_sub.add_parser('reset-client', help='Reset sliding window for a client IP')
    rl_rc.add_argument('client', help='Client IP or key')

    rl_ru = rl_sub.add_parser('reset-user', help='Reset per-user window for a username')
    rl_ru.add_argument('username')
    rl.set_defaults(func=cmd_rate_limits)

    # ---- schedule ----
    sched = sub.add_parser('schedule', help='Manage scheduled tasks')
    sched_sub = sched.add_subparsers(dest='sched_action', required=True)

    sched_list = sched_sub.add_parser('list', help='List all scheduled tasks')
    sched_list.add_argument('--next', action='store_true', dest='next',
                             help='Sort by next_run_at and show countdown to next execution')
    sched_get = sched_sub.add_parser('get', help='Show details of a task')
    sched_get.add_argument('task_id', help='Task ID')

    sched_create = sched_sub.add_parser('create', help='Create a new scheduled task')
    sched_create.add_argument('name', help='Human-readable name')
    sched_create.add_argument('tool', help='Tool name to invoke')
    sched_create.add_argument('--args', default='{}', metavar='JSON',
                              help='Tool arguments as a JSON object (default: {})')
    sched_create.add_argument('--interval', type=int, default=3600, metavar='SECONDS',
                              help='Run every N seconds (default: 3600)')
    sched_create.add_argument('--disabled', action='store_true',
                              help='Create the task in disabled state')

    sched_del = sched_sub.add_parser('delete', help='Delete a task')
    sched_del.add_argument('task_id')

    sched_en = sched_sub.add_parser('enable', help='Enable a task')
    sched_en.add_argument('task_id')

    sched_dis = sched_sub.add_parser('disable', help='Disable a task')
    sched_dis.add_argument('task_id')

    sched_trig = sched_sub.add_parser('trigger', help='Force a task to run on the next tick')
    sched_trig.add_argument('task_id')

    sched_hist = sched_sub.add_parser('history', help='Show run history for a task')
    sched_hist.add_argument('task_id')
    sched_hist.add_argument('--n', type=int, default=None, metavar='N',
                            help='Limit output to the N most-recent records')
    sched.set_defaults(func=cmd_schedule)

    # ---- provider health ----
    ph = sub.add_parser('provider-health', help='Check provider key and adapter availability')
    ph_sub = ph.add_subparsers(dest='ph_action', required=True)

    ph_check = ph_sub.add_parser('check', help='Check a single provider')
    ph_check.add_argument('provider', choices=['openai', 'anthropic', 'openrouter', 'ollama'])

    ph_sub.add_parser('list', help='Poll all 4 providers and print a health table')

    ph_exp = ph_sub.add_parser('expiring', help='List providers with keys expiring soon')
    ph_exp.add_argument('--within-days', type=int, default=7, dest='within_days',
                        help='Warn if expiry is within this many days (default 7)')

    ph.set_defaults(func=cmd_provider_health)

    # ---- metrics ----
    met = sub.add_parser('metrics', help='View per-tool invocation counts and latency')
    met_sub = met.add_subparsers(dest='met_action', required=True)

    met_sub.add_parser('tools', help='Print all tools with call counts and latency')

    met_top = met_sub.add_parser('top', help='Print the top N most-called tools')
    met_top.add_argument('--n', type=int, default=5, metavar='N',
                         help='Number of tools to show (default: 5)')

    met.set_defaults(func=cmd_metrics)

    # ---- status ----
    stat = sub.add_parser('status', help='Print a gateway operational status summary')
    stat.set_defaults(func=cmd_status)

    # ---- memory ----
    mem = sub.add_parser('memory', help='Manage per-agent key-value memory')
    mem_sub = mem.add_subparsers(dest='mem_action', required=True)

    mem_sub.add_parser('agents', help='List all agents that have stored memory')

    mem_list = mem_sub.add_parser('list', help='List all keys for an agent')
    mem_list.add_argument('agent_id')
    mem_list.add_argument('--meta', action='store_true',
                         help='Also show per-key expiry information (requires one extra request per key)')

    mem_get = mem_sub.add_parser('get', help='Read a single memory key')
    mem_get.add_argument('agent_id')
    mem_get.add_argument('key')

    mem_set = mem_sub.add_parser('set', help='Write a value to a memory key')
    mem_set.add_argument('agent_id')
    mem_set.add_argument('key')
    mem_set.add_argument('value', help='Value (JSON or plain string)')
    mem_set.add_argument('--ttl', type=int, default=None, metavar='SECONDS',
                         help='Optional TTL in seconds')

    mem_del = mem_sub.add_parser('delete', help='Delete a single memory key')
    mem_del.add_argument('agent_id')
    mem_del.add_argument('key')

    mem_prune = mem_sub.add_parser('prune', help='Remove expired keys for an agent')
    mem_prune.add_argument('agent_id')

    mem_clear = mem_sub.add_parser('clear', help='Delete ALL memory for an agent')
    mem_clear.add_argument('agent_id')

    mem_exp = mem_sub.add_parser('export', help='Export all agent memory to JSON')
    mem_exp.add_argument('--output', metavar='FILE', default='',
                         help='Write to FILE instead of stdout')

    mem_imp = mem_sub.add_parser('import', help='Import agent memory from a JSON file')
    mem_imp.add_argument('file', metavar='FILE', help='Path to exported JSON file')
    mem_imp.add_argument('--replace', action='store_true',
                         help='Replace existing memory instead of merging')

    mem.set_defaults(func=cmd_memory)

    # ---- users ----
    usr = sub.add_parser('users', help='Manage gateway user accounts')
    usr_sub = usr.add_subparsers(dest='user_action', required=True)

    usr_sub.add_parser('list', help='List all user accounts')

    usr_cr = usr_sub.add_parser('create', help='Create a new user account')
    usr_cr.add_argument('username', help='Username for the new account')
    usr_cr.add_argument('password', help='Initial password')
    usr_cr.add_argument('--role', default='user', metavar='ROLE',
                        help='Role to assign (default: user)')

    usr_del = usr_sub.add_parser('delete', help='Delete a user account')
    usr_del.add_argument('username', help='Username to delete')

    usr_pw = usr_sub.add_parser('password', help='Change a user\'s password')
    usr_pw.add_argument('username', help='Target username')
    usr_pw.add_argument('new_password', help='New password')

    usr_perm = usr_sub.add_parser('permissions',
                                   help='View or set per-user tool allow-list')
    usr_perm_sub = usr_perm.add_subparsers(dest='user_perm_action', required=True)
    usr_perm_get = usr_perm_sub.add_parser('get', help='Show tool allow-list for a user')
    usr_perm_get.add_argument('username')
    usr_perm_set = usr_perm_sub.add_parser('set', help='Set tool allow-list (comma-separated)')
    usr_perm_set.add_argument('username')
    usr_perm_set.add_argument('tools', help='Comma-separated list of allowed tool names')
    usr_perm_clr = usr_perm_sub.add_parser('clear',
                                            help='Remove tool restriction (allow all tools)')
    usr_perm_clr.add_argument('username')

    usr.set_defaults(func=cmd_users)

    # ---- content-filter ----
    cf = sub.add_parser('content-filter', help='Manage runtime content-filter deny rules')
    cf_sub = cf.add_subparsers(dest='cf_action', required=True)

    cf_sub.add_parser('list', help='List all active deny rules with their index and mode')

    cf_add = cf_sub.add_parser('add', help='Add a new deny rule')
    cf_add.add_argument('pattern', help='Pattern string to deny')
    cf_add.add_argument('--mode', default='literal', choices=['literal', 'regex'],
                        help='Match mode: literal (default) or regex')
    cf_add.add_argument('--label', default='', metavar='LABEL',
                        help='Human-readable label for this rule (optional)')

    cf_del = cf_sub.add_parser('delete', help='Delete a deny rule by its index')
    cf_del.add_argument('index', type=int, metavar='INDEX',
                        help='Zero-based index from `content-filter list`')

    cf_sub.add_parser('reload', help='Reload rules from disk and environment variables')

    cf.set_defaults(func=cmd_content_filter)

    # ---- alerts ----
    alrt = sub.add_parser('alerts', help='Manage approval-queue depth alert configuration')
    alrt_sub = alrt.add_subparsers(dest='alert_action', required=True)

    alrt_sub.add_parser('status', help='Show current alert threshold')

    alrt_set = alrt_sub.add_parser('set', help='Set the alert threshold (0 = disable)')
    alrt_set.add_argument('threshold', type=int, metavar='N',
                          help='Fire gateway.alert when pending approvals reach this count; 0 disables')

    alrt.set_defaults(func=cmd_alerts)

    # ---- approvals ----
    appr = sub.add_parser('approvals',
                          help='Manage the approval queue and auto-reject timeout')
    appr_sub = appr.add_subparsers(dest='appr_action', required=True)

    appr_sub.add_parser('list', help='List all pending approval requests')

    appr_apv = appr_sub.add_parser('approve', help='Approve a pending request')
    appr_apv.add_argument('id', type=int, metavar='ID', help='Approval request ID')

    appr_rej = appr_sub.add_parser('reject', help='Reject a pending request')
    appr_rej.add_argument('id', type=int, metavar='ID', help='Approval request ID')

    appr_to = appr_sub.add_parser('timeout',
                                  help='Manage the auto-reject timeout configuration')
    appr_to_sub = appr_to.add_subparsers(dest='timeout_action', required=True)
    appr_to_sub.add_parser('get', help='Show current auto-reject timeout')
    appr_to_set = appr_to_sub.add_parser('set',
                                         help='Set the auto-reject timeout (0 = disable)')
    appr_to_set.add_argument('seconds', type=float, metavar='SECS',
                             help='Auto-reject pending approvals after this many seconds; 0 disables')

    appr.set_defaults(func=cmd_approvals)

    # ---- capabilities ----
    cap = sub.add_parser('capabilities', help='Browse tool capability manifests')
    cap_sub = cap.add_subparsers(dest='cap_action', required=True)

    cap_sub.add_parser('list', help='List all tools with their capability manifests')

    cap_show = cap_sub.add_parser('show', help='Show full manifest detail for a tool')
    cap_show.add_argument('tool', metavar='TOOL',
                          help='Tool id (e.g. file.write, system.exec)')

    cap.set_defaults(func=cmd_capabilities)

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
