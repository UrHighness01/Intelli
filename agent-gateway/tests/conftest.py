"""conftest.py — Add agent-gateway to sys.path so tests can import modules
directly (e.g. `from app import app`) whether run from the repo root or the
agent-gateway directory.  Also sets SANDBOX_WORKER_PATH to the bundled worker
so sandbox tests work without needing the env var manually set.
"""
import sys
import os

import pytest

# Ensure the agent-gateway folder is importable as a package root.
_GW_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _GW_DIR not in sys.path:
    sys.path.insert(0, _GW_DIR)

# Ensure the repo root is importable so `agent_gateway.*` works.
_REPO_ROOT = os.path.normpath(os.path.join(_GW_DIR, '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Set bundled worker path for sandbox tests when not already set.
_WORKER = os.path.join(_GW_DIR, 'sandbox', 'worker.py')
if os.path.exists(_WORKER) and not os.environ.get('SANDBOX_WORKER_PATH'):
    os.environ['SANDBOX_WORKER_PATH'] = _WORKER

# Allow all tool capabilities during tests.  Individual capability tests
# inject explicit frozensets via CapabilityVerifier(allowed=...) so they
# are unaffected.  Other tests (approvals, supervisor, etc.) should not be
# blocked by a restrictive default.
os.environ['AGENT_GATEWAY_ALLOWED_CAPS'] = 'ALL'


@pytest.fixture(autouse=True)
def _reset_ip_rate_limiter():
    """Reset the per-IP rate-limit window before every test.

    This prevents individual tests (especially high-volume fuzzer tests) from
    exhausting the shared in-memory sliding window and causing 429 errors in
    subsequently-run tests that need to authenticate or make API calls.
    Test-isolation for the rate limiter state mirrors the isolation databases
    or file fixtures provide for persistent data.
    """
    import rate_limit
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()
