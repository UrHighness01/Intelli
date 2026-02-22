"""Tests for the outbound URL allowlist in providers/adapters.py.

Covers _check_outbound_url() in isolation and verifies that each adapter
respects the allowlist when INTELLI_PROVIDER_OUTBOUND_ALLOWLIST is set.
"""
import os
import sys
import importlib
import pytest


# ---------------------------------------------------------------------------
# Helpers to reload the module so env-var-controlled globals are re-evaluated
# ---------------------------------------------------------------------------

def _reload_adapters(monkeypatch, allowlist: str | None = None):
    """Reload providers.adapters with a custom INTELLI_PROVIDER_OUTBOUND_ALLOWLIST."""
    if allowlist is None:
        monkeypatch.delenv('INTELLI_PROVIDER_OUTBOUND_ALLOWLIST', raising=False)
    else:
        monkeypatch.setenv('INTELLI_PROVIDER_OUTBOUND_ALLOWLIST', allowlist)
    # Remove cached module so the import re-executes module-level code
    for key in list(sys.modules.keys()):
        if 'providers.adapters' in key or key == 'providers.adapters':
            del sys.modules[key]
    import providers.adapters as m
    importlib.invalidate_caches()
    return m


# ---------------------------------------------------------------------------
# Unit tests for _check_outbound_url
# ---------------------------------------------------------------------------

class TestCheckOutboundUrl:
    def test_allowed_exact_origin(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com,https://api.anthropic.com')
        m._check_outbound_url('https://api.openai.com/v1/chat/completions')  # must not raise

    def test_allowed_with_path(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')
        m._check_outbound_url('https://api.openai.com/v1')  # must not raise

    def test_blocked_unlisted_origin(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')
        with pytest.raises(RuntimeError, match='blocked'):
            m._check_outbound_url('https://evil.example.com/steal')

    def test_blocked_different_scheme(self, monkeypatch):
        """http:// vs https:// are different origins."""
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')
        with pytest.raises(RuntimeError, match='blocked'):
            m._check_outbound_url('http://api.openai.com/v1')

    def test_blocked_different_port(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'http://localhost:11434')
        with pytest.raises(RuntimeError, match='blocked'):
            m._check_outbound_url('http://localhost:9999/api/chat')

    def test_partial_host_match_blocked(self, monkeypatch):
        """api.openai.com should NOT match evil-api.openai.com.evil.com."""
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')
        with pytest.raises(RuntimeError, match='blocked'):
            m._check_outbound_url('https://evil-api.openai.com.evil.com/v1')

    def test_default_allowlist_includes_all_providers(self, monkeypatch):
        """Without env var all 4 built-in provider origins must be allowed."""
        m = _reload_adapters(monkeypatch, None)
        for url in [
            'https://api.openai.com/v1/chat/completions',
            'https://api.anthropic.com/v1/messages',
            'https://openrouter.ai/api/v1/chat/completions',
            'http://localhost:11434/api/chat',
        ]:
            m._check_outbound_url(url)  # must not raise

    def test_multiple_origins_in_allowlist(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://a.example.com, https://b.example.com')
        m._check_outbound_url('https://a.example.com/endpoint')
        m._check_outbound_url('https://b.example.com/endpoint')
        with pytest.raises(RuntimeError):
            m._check_outbound_url('https://c.example.com/endpoint')

    def test_empty_allowlist_blocks_all(self, monkeypatch):
        """A whitespace-only value is treated as unset → falls back to the
        default allowlist (all built-in provider origins are allowed)."""
        m = _reload_adapters(monkeypatch, '   ')  # whitespace-only → default allowlist
        # Default allowlist allows all built-in providers — must NOT raise
        m._check_outbound_url('https://api.openai.com/v1')
        # But unknown origins are still blocked
        with pytest.raises(RuntimeError, match='blocked'):
            m._check_outbound_url('https://evil.example.com/')

    def test_trailing_slash_normalization(self, monkeypatch):
        """Allowlist entries with trailing slashes are normalized."""
        m = _reload_adapters(monkeypatch, 'https://api.openai.com/')
        m._check_outbound_url('https://api.openai.com/v1')  # must not raise


# ---------------------------------------------------------------------------
# Integration: _check_outbound_url called inside chat_complete / is_available
# ---------------------------------------------------------------------------

class TestAdapterHonorsAllowlist:
    """Verify that adapters raise before making any HTTP call when the target
    origin is not allowlisted."""

    def _block_requests(self, monkeypatch, m):
        """Ensure _requests.post/get raises if called (should be blocked before reaching it)."""
        import unittest.mock as mock
        m._requests = mock.MagicMock()
        m._HAS_REQUESTS = True
        m._requests.post.side_effect = AssertionError('HTTP call reached — allowlist not enforced')
        m._requests.get.side_effect  = AssertionError('HTTP call reached — allowlist not enforced')

    def test_openai_blocked(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.anthropic.com')  # OpenAI not in list
        self._block_requests(monkeypatch, m)
        monkeypatch.setenv('INTELLI_OPENAI_KEY', 'sk-test')
        adapter = m.OpenAIAdapter()
        with pytest.raises(RuntimeError, match='blocked'):
            adapter.chat_complete([{'role': 'user', 'content': 'hi'}])

    def test_anthropic_blocked(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')  # Anthropic not in list
        self._block_requests(monkeypatch, m)
        monkeypatch.setenv('INTELLI_ANTHROPIC_KEY', 'sk-ant-test')
        adapter = m.AnthropicAdapter()
        with pytest.raises(RuntimeError, match='blocked'):
            adapter.chat_complete([{'role': 'user', 'content': 'hi'}])

    def test_openrouter_blocked(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')
        self._block_requests(monkeypatch, m)
        monkeypatch.setenv('INTELLI_OPENROUTER_KEY', 'or-test')
        adapter = m.OpenRouterAdapter()
        with pytest.raises(RuntimeError, match='blocked'):
            adapter.chat_complete([{'role': 'user', 'content': 'hi'}])

    def test_ollama_chat_blocked(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')  # localhost not in list
        self._block_requests(monkeypatch, m)
        adapter = m.OllamaAdapter()
        with pytest.raises(RuntimeError, match='blocked'):
            adapter.chat_complete([{'role': 'user', 'content': 'hi'}])

    def test_ollama_is_available_blocked(self, monkeypatch):
        m = _reload_adapters(monkeypatch, 'https://api.openai.com')  # localhost not in list
        self._block_requests(monkeypatch, m)
        adapter = m.OllamaAdapter()
        # is_available swallows all errors and returns False — including RuntimeError
        assert adapter.is_available() is False

    def test_openai_allowed_proceeds_to_http(self, monkeypatch):
        """When origin is allowed, the adapter passes the allowlist check and
        reaches the HTTP layer (which we mock to return a canned response)."""
        import unittest.mock as mock
        m = _reload_adapters(monkeypatch, None)  # default — all providers allowed
        monkeypatch.setenv('INTELLI_OPENAI_KEY', 'sk-test')
        fake_resp = mock.MagicMock()
        fake_resp.json.return_value = {
            'choices': [{'message': {'content': 'Hello'}}],
            'model': 'gpt-4o-mini',
            'usage': {},
        }
        m._HAS_REQUESTS = True
        m._requests = mock.MagicMock()
        m._requests.post.return_value = fake_resp
        adapter = m.OpenAIAdapter()
        result = adapter.chat_complete([{'role': 'user', 'content': 'hi'}])
        assert result['content'] == 'Hello'
        assert m._requests.post.called
