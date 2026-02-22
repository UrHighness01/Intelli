"""Tests for POST /chat/complete?stream=true  SSE streaming (Item 11)."""
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient
import auth as _auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def client():
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def token(client):
    """Fresh admin token for each test."""
    _auth.USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    users = {}
    if _auth.USERS_PATH.exists():
        try:
            users = json.loads(_auth.USERS_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    users.pop('stream_tester', None)
    _auth.USERS_PATH.write_text(json.dumps(users), encoding='utf-8')
    _auth._TOKENS.clear()
    _auth.create_user('stream_tester', 'str3ampass!', ['admin'])
    r = client.post('/admin/login', json={'username': 'stream_tester', 'password': 'str3ampass!'})
    assert r.status_code == 200
    return r.json()['token']


def _mock_adapter(content='Hello world test response', provider='openai', model='gpt-4o-mini'):
    adapter = MagicMock()
    adapter.is_available.return_value = True
    adapter.chat_complete.return_value = {
        'content': content,
        'model': model,
        'provider': provider,
        'usage': {'prompt_tokens': 8, 'completion_tokens': 4},
    }
    return adapter


# ---------------------------------------------------------------------------
# Non-streaming (regression) — stream=false (default)
# ---------------------------------------------------------------------------

class TestChatCompleteNonStreaming:
    def test_returns_json_by_default(self, client, token):
        """stream=false returns Content-Type application/json as before."""
        adapter = _mock_adapter('Hello JSON world')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert r.status_code == 200
        assert 'application/json' in r.headers.get('content-type', '')
        body = r.json()
        assert 'content' in body

    def test_stream_false_explicit_returns_json(self, client, token):
        """stream=false explicit returns JSON unchanged."""
        adapter = _mock_adapter('Explicit false')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=false',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert r.status_code == 200
        assert 'application/json' in r.headers.get('content-type', '')

    def test_unauthenticated_returns_401(self, client):
        r = client.post(
            '/chat/complete',
            json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Streaming — stream=true
# ---------------------------------------------------------------------------

class TestChatCompleteStreaming:
    def test_returns_event_stream_content_type(self, client, token):
        """?stream=true must respond with text/event-stream."""
        adapter = _mock_adapter('streaming test')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert r.status_code == 200
        assert 'text/event-stream' in r.headers.get('content-type', '')

    def test_stream_contains_data_lines(self, client, token):
        """Response body must contain 'data: ' lines."""
        adapter = _mock_adapter('Hello world')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert r.status_code == 200
        text = r.text
        assert 'data: ' in text

    def test_stream_final_event_has_done_true(self, client, token):
        """Last SSE data event must have done=true."""
        adapter = _mock_adapter('Hello world from stream')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'test'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        done_events = [e for e in events if e.get('done') is True]
        assert len(done_events) >= 1, 'At least one event with done=true expected'

    def test_stream_final_event_has_full_content(self, client, token):
        """Final SSE event must carry the full content from the adapter."""
        full_content = 'This is the complete answer from the model'
        adapter = _mock_adapter(full_content)
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'q'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        events = _parse_sse(r.text)
        final = next((e for e in events if e.get('done')), None)
        assert final is not None
        assert final.get('content') == full_content

    def test_stream_token_events_have_done_false(self, client, token):
        """Intermediate token events must have done=false."""
        adapter = _mock_adapter('one two three')  # 3 tokens
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'go'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        events = _parse_sse(r.text)
        token_events = [e for e in events if not e.get('done')]
        # 'one two three' → 3 words → 3 token events
        assert len(token_events) >= 1
        for ev in token_events:
            assert ev.get('done') is False
            assert 'token' in ev

    def test_stream_reconstructed_content_matches_full(self, client, token):
        """Concatenating all token events must equal the final content."""
        full_content = 'alpha beta gamma delta'
        adapter = _mock_adapter(full_content)
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'q'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        events = _parse_sse(r.text)
        token_events = [e for e in events if not e.get('done')]
        final = next((e for e in events if e.get('done')), {})
        reconstructed = ''.join(e.get('token', '') for e in token_events)
        assert reconstructed == final.get('content', '')

    def test_stream_unauthenticated_returns_401(self, client):
        r = client.post(
            '/chat/complete?stream=true',
            json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
        )
        assert r.status_code == 401

    def test_stream_unknown_provider_returns_400(self, client, token):
        r = client.post(
            '/chat/complete?stream=true',
            json={'provider': 'nonexistent_provider_xyz',
                  'messages': [{'role': 'user', 'content': 'hi'}]},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 400

    def test_stream_provider_error_emits_error_event(self, client, token):
        """When the adapter raises, stream must emit an error SSE event."""
        adapter = MagicMock()
        adapter.is_available.return_value = True
        adapter.chat_complete.side_effect = RuntimeError('upstream failure')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'q'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert r.status_code == 200  # SSE always starts with 200
        events = _parse_sse(r.text)
        err_events = [e for e in events if 'error' in e]
        assert len(err_events) >= 1
        assert 'upstream failure' in err_events[0]['error']

    def test_stream_cache_headers_present(self, client, token):
        """SSE response must carry Cache-Control: no-cache."""
        adapter = _mock_adapter('hi there')
        with patch('app.get_adapter', return_value=adapter):
            r = client.post(
                '/chat/complete?stream=true',
                json={'provider': 'openai', 'messages': [{'role': 'user', 'content': 'hi'}]},
                headers={'Authorization': f'Bearer {token}'},
            )
        assert 'no-cache' in r.headers.get('cache-control', '')


# ---------------------------------------------------------------------------
# Helper — parse SSE text into list of dicts
# ---------------------------------------------------------------------------

def _parse_sse(text: str) -> list:
    """Parse 'data: {...}\\n\\n' SSE format into a list of JSON objects."""
    events = []
    for chunk in text.split('\n\n'):
        for line in chunk.splitlines():
            if line.startswith('data: '):
                payload = line[6:].strip()
                if payload and payload != '[DONE]':
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
    return events
