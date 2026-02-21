"""Tests for per-user rate limiting.

Verifies that rate_limit.check_user_rate_limit() tracks per-username quotas
independently of the per-IP sliding window.

Uses monkeypatch.setattr to configure module-level globals without reloading
the module — reloading would pollute shared state for other test files.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

_GW_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _GW_DIR not in sys.path:
    sys.path.insert(0, _GW_DIR)

import rate_limit


@pytest.fixture(autouse=True)
def isolated_user_rl(monkeypatch):
    """Apply a small per-user quota via setattr and reset state each test."""
    monkeypatch.setattr(rate_limit, '_ENABLED', True)
    monkeypatch.setattr(rate_limit, '_USER_MAX_REQUESTS', 3)
    monkeypatch.setattr(rate_limit, '_USER_WINDOW_SECONDS', 60.0)
    rate_limit.reset_all_users()
    yield
    rate_limit.reset_all_users()


def test_user_rate_limit_allows_within_quota():
    """3 calls for the same user should all succeed (quota is 3)."""
    for _ in range(3):
        rate_limit.check_user_rate_limit('alice')  # must not raise


def test_user_rate_limit_blocks_when_exceeded():
    """4th call for the same user must raise 429."""
    for _ in range(3):
        rate_limit.check_user_rate_limit('alice')
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.check_user_rate_limit('alice')
    assert exc_info.value.status_code == 429
    detail: dict = exc_info.value.detail  # type: ignore[assignment]
    assert detail['error'] == 'user_rate_limit_exceeded'
    assert detail['user'] == 'alice'


def test_user_rate_limit_independent_per_user():
    """Different users have independent quotas."""
    for _ in range(3):
        rate_limit.check_user_rate_limit('alice')

    # bob hasn't used any quota, so his 3 calls should all pass
    for _ in range(3):
        rate_limit.check_user_rate_limit('bob')

    # alice is still blocked
    with pytest.raises(HTTPException):
        rate_limit.check_user_rate_limit('alice')


def test_user_rate_limit_429_has_retry_after():
    """The 429 error detail must include retry_after_seconds."""
    for _ in range(3):
        rate_limit.check_user_rate_limit('carol')
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.check_user_rate_limit('carol')
    detail: dict = exc_info.value.detail  # type: ignore[assignment]
    assert 'retry_after_seconds' in detail
    assert detail['retry_after_seconds'] >= 1


def test_reset_user_clears_state():
    """reset_user() must clear the quota so the next call succeeds."""
    for _ in range(3):
        rate_limit.check_user_rate_limit('dave')
    with pytest.raises(HTTPException):
        rate_limit.check_user_rate_limit('dave')

    rate_limit.reset_user('dave')
    rate_limit.check_user_rate_limit('dave')  # must not raise


def test_reset_all_users_clears_all_state():
    """reset_all_users() must clear all user quotas."""
    for user in ('eve', 'frank', 'grace'):
        for _ in range(3):
            rate_limit.check_user_rate_limit(user)

    rate_limit.reset_all_users()

    for user in ('eve', 'frank', 'grace'):
        rate_limit.check_user_rate_limit(user)  # all should succeed now


def test_user_rate_limit_disabled(monkeypatch):
    """When rate limiting is disabled, user limits are skipped."""
    monkeypatch.setattr(rate_limit, '_ENABLED', False)
    monkeypatch.setattr(rate_limit, '_USER_MAX_REQUESTS', 1)

    # Even exceeding the limit doesn't raise when disabled
    for _ in range(10):
        rate_limit.check_user_rate_limit('henry')  # must not raise
