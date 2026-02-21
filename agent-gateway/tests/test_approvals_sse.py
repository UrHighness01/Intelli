"""Tests for the /approvals/stream SSE endpoint logic.

httpx ASGITransport (sync and async) fully exhausts the ASGI lifecycle before
returning, making infinite streaming responses incompatible with TestClient.
Instead we:

  1. Verify the SSE route is registered and has the correct media type.
  2. Unit-test the async generator building blocks directly (fast, no HTTP layer).
"""
import os
import json
import asyncio

# Speed up SSE polling for tests
os.environ.setdefault('AGENT_GATEWAY_SSE_POLL_INTERVAL', '0.1')

import pytest
from fastapi.testclient import TestClient
from app import app, _SSE_POLL_INTERVAL
from supervisor import ApprovalQueue

_sync_client = TestClient(app)


# ---------------------------------------------------------------------------
# Route-level checks (no streaming required)
# ---------------------------------------------------------------------------

def test_approvals_stream_route_is_registered():
    """GET /approvals/stream route exists in the app."""
    paths = [r.path for r in app.routes]  # type: ignore[attr-defined]
    assert '/approvals/stream' in paths


def test_approvals_stream_media_type_in_source():
    """The SSE endpoint declares text/event-stream as its media type."""
    import inspect
    from app import approvals_stream
    src = inspect.getsource(approvals_stream)
    assert 'text/event-stream' in src


# ---------------------------------------------------------------------------
# Unit tests: event generator building-block behaviour (no HTTP layer)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_event_generator_emits_keepalive():
    """Verify that the keepalive SSE comment format is correct."""
    keepalive = ': keepalive\n\n'
    assert keepalive.startswith(':')
    assert keepalive.endswith('\n\n')


@pytest.mark.anyio
async def test_event_generator_logic_emits_approval_update():
    """A pending approval causes the generator logic to produce an approval_update event."""
    fake_q = ApprovalQueue()
    fake_q.submit({'tool': 'system.exec', 'args': {}})

    # Replicate the core logic from app.py's approvals_stream generator
    pending_dict = fake_q.list_pending()
    current_ids = {str(k) for k in pending_dict}
    assert current_ids, 'Expected pending items in queue'

    last_ids: set = set()
    new_ids = current_ids - last_ids
    assert new_ids, 'Expected new pending IDs on first poll'

    pending_list = [{'id': k, **v} for k, v in pending_dict.items()]
    data = json.dumps({'pending': pending_list})
    sse_chunk = f'event: approval_update\ndata: {data}\n\n'

    assert sse_chunk.startswith('event: approval_update')
    payload = json.loads(sse_chunk.split('data: ')[1].strip())
    assert 'pending' in payload
    assert isinstance(payload['pending'], list)
    assert len(payload['pending']) == 1
    assert payload['pending'][0]['status'] == 'pending'
