"""Tests for the sliding-window rate limiter (rate_limit.py)."""
import importlib
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest
from starlette.datastructures import Headers


# ── helpers ────────────────────────────────────────────────────────────────

def _make_request(ip: str = '1.2.3.4') -> StarletteRequest:
    """Build a minimal Starlette Request with a given client IP."""
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/',
        'query_string': b'',
        'headers': [],
        'client': (ip, 9999),
    }
    return StarletteRequest(scope)


def _make_forwarded_request(forwarded_for: str) -> StarletteRequest:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/',
        'query_string': b'',
        'headers': [(b'x-forwarded-for', forwarded_for.encode())],
        'client': ('10.0.0.1', 9999),
    }
    return StarletteRequest(scope)


# ── import module fresh per-test to allow env-var reconfiguration ──────────

@pytest.fixture(autouse=True)
def fresh_rate_limit(monkeypatch):
    """Each test gets a clean rate_limit module with all state reset."""
    import rate_limit
    rate_limit.reset_all()
    yield rate_limit
    rate_limit.reset_all()


# ── client key extraction ──────────────────────────────────────────────────

def test_client_key_uses_ip(fresh_rate_limit):
    req = _make_request('5.6.7.8')
    assert fresh_rate_limit._client_key(req) == '5.6.7.8'


def test_client_key_prefers_x_forwarded_for(fresh_rate_limit):
    req = _make_forwarded_request('203.0.113.5, 10.0.0.1')
    assert fresh_rate_limit._client_key(req) == '203.0.113.5'


# ── basic allow / deny ────────────────────────────────────────────────────

def test_requests_within_limit_are_allowed(fresh_rate_limit, monkeypatch):
    monkeypatch.setattr(fresh_rate_limit, '_MAX_REQUESTS', 5)
    monkeypatch.setattr(fresh_rate_limit, '_BURST', 0)
    req = _make_request('10.0.0.2')
    for _ in range(5):
        fresh_rate_limit.check_rate_limit(req)  # must not raise


def test_requests_over_limit_raise_429(fresh_rate_limit, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(fresh_rate_limit, '_MAX_REQUESTS', 3)
    monkeypatch.setattr(fresh_rate_limit, '_BURST', 0)
    req = _make_request('10.0.0.3')
    for _ in range(3):
        fresh_rate_limit.check_rate_limit(req)
    with pytest.raises(HTTPException) as exc_info:
        fresh_rate_limit.check_rate_limit(req)
    assert exc_info.value.status_code == 429


def test_burst_allows_extra_requests(fresh_rate_limit, monkeypatch):
    monkeypatch.setattr(fresh_rate_limit, '_MAX_REQUESTS', 2)
    monkeypatch.setattr(fresh_rate_limit, '_BURST', 3)
    req = _make_request('10.0.0.4')
    # Up to 2 + 3 = 5 requests should be fine
    for _ in range(5):
        fresh_rate_limit.check_rate_limit(req)


def test_disabled_limiter_always_passes(fresh_rate_limit, monkeypatch):
    monkeypatch.setattr(fresh_rate_limit, '_ENABLED', False)
    monkeypatch.setattr(fresh_rate_limit, '_MAX_REQUESTS', 1)
    monkeypatch.setattr(fresh_rate_limit, '_BURST', 0)
    req = _make_request('10.0.0.5')
    for _ in range(20):
        fresh_rate_limit.check_rate_limit(req)  # must not raise


# ── separate clients are tracked independently ────────────────────────────

def test_different_clients_tracked_independently(fresh_rate_limit, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(fresh_rate_limit, '_MAX_REQUESTS', 2)
    monkeypatch.setattr(fresh_rate_limit, '_BURST', 0)
    req_a = _make_request('1.1.1.1')
    req_b = _make_request('2.2.2.2')
    fresh_rate_limit.check_rate_limit(req_a)
    fresh_rate_limit.check_rate_limit(req_a)
    fresh_rate_limit.check_rate_limit(req_b)  # separate bucket — should pass
    with pytest.raises(HTTPException):
        fresh_rate_limit.check_rate_limit(req_a)


# ── current_usage ─────────────────────────────────────────────────────────

def test_current_usage_returns_counts(fresh_rate_limit, monkeypatch):
    monkeypatch.setattr(fresh_rate_limit, '_MAX_REQUESTS', 10)
    monkeypatch.setattr(fresh_rate_limit, '_BURST', 0)
    req = _make_request('3.3.3.3')
    fresh_rate_limit.check_rate_limit(req)
    fresh_rate_limit.check_rate_limit(req)
    usage = fresh_rate_limit.current_usage(req)
    assert usage['requests_in_window'] == 2
    assert usage['remaining'] == 8


# ── 429 via HTTP layer ────────────────────────────────────────────────────

def test_tool_call_returns_429_when_rate_limited(monkeypatch, fresh_rate_limit):
    """Integration: /tools/call uses the rate_limiter dependency."""
    import rate_limit as rl
    monkeypatch.setattr(rl, '_MAX_REQUESTS', 1)
    monkeypatch.setattr(rl, '_BURST', 0)
    rl.reset_all()

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from app import app as _app
    client = TestClient(_app, raise_server_exceptions=False)

    payload = {'tool': 'echo', 'args': {'text': 'hi'}}
    r1 = client.post('/tools/call', json=payload)
    assert r1.status_code != 429  # first request passes

    # Exhaust the limit
    for _ in range(20):
        r = client.post('/tools/call', json=payload)
        if r.status_code == 429:
            assert 'Retry-After' in r.headers
            return

    pytest.fail('Expected a 429 response but none was returned')
