"""Concrete LLM provider adapters for the Agent Gateway.

Each adapter wraps a remote or local LLM API behind a unified interface:

    adapter.chat_complete(messages, model=..., **kwargs) -> dict

The returned dict always has the keys:
  content   – the assistant reply text (str)
  model     – model identifier used (str)
  usage     – token counts dict (may be empty for local providers)
  provider  – provider name (str)

API keys are resolved via the VaultKeyStore → ProviderKeyStore chain, or
directly from the environment variables listed per-adapter below.

Requires: ``requests`` (already in requirements.txt).

Supported providers
-------------------
  openai      – OpenAI Chat Completions API       INTELLI_OPENAI_KEY or OPENAI_API_KEY
  anthropic   – Anthropic Messages API            INTELLI_ANTHROPIC_KEY or ANTHROPIC_API_KEY
  openrouter  – OpenRouter Chat Completions API   INTELLI_OPENROUTER_KEY
  ollama      – Local Ollama REST API             no key required; OLLAMA_BASE_URL

Usage
-----
    from providers.adapters import get_adapter

    adapter = get_adapter('openai')
    if adapter.is_available():
        reply = adapter.chat_complete(
            messages=[{'role': 'user', 'content': 'Hello!'}],
            model='gpt-4o-mini',
        )
        print(reply['content'])
"""
from __future__ import annotations

import os
import urllib.parse as _urlparse
from typing import Any, Dict, List, Optional

_requests: Any = None
try:
    import requests as _requests  # type: ignore[assignment]
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

from providers.provider_adapter import ProviderKeyStore

try:
    from providers.vault_adapter import VaultKeyStore as _VaultKeyStore
    _vault: Optional[_VaultKeyStore] = _VaultKeyStore()
except Exception:
    _vault = None


# ---------------------------------------------------------------------------
# Outbound allowlist
# ---------------------------------------------------------------------------

def _build_default_allowlist() -> List[str]:
    """Derive the default allowed provider origins from env + known hosts."""
    raw_origins = [
        os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        'https://api.anthropic.com',
        'https://openrouter.ai',
        os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434'),
    ]
    result: List[str] = []
    for url in raw_origins:
        p = _urlparse.urlparse(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in result:
            result.append(origin)
    return result


_OUTBOUND_ALLOWLIST_RAW: str = os.environ.get('INTELLI_PROVIDER_OUTBOUND_ALLOWLIST', '')
_OUTBOUND_ALLOWLIST: List[str] = (
    [o.strip().rstrip('/') for o in _OUTBOUND_ALLOWLIST_RAW.split(',') if o.strip()]
    if _OUTBOUND_ALLOWLIST_RAW.strip()
    else _build_default_allowlist()
)


def _check_outbound_url(url: str) -> None:
    """Raise ``RuntimeError`` if *url*'s origin is not in the configured allowlist.

    Configure via comma-separated env var::

        INTELLI_PROVIDER_OUTBOUND_ALLOWLIST=https://api.openai.com,https://api.anthropic.com

    When the variable is unset every built-in provider origin is allowed.
    """
    p = _urlparse.urlparse(url)
    origin = f'{p.scheme}://{p.netloc}'
    if not any(origin == allowed or origin.startswith(allowed + '/') for allowed in _OUTBOUND_ALLOWLIST):
        raise RuntimeError(
            f'Outbound request to {origin!r} is blocked by INTELLI_PROVIDER_OUTBOUND_ALLOWLIST. '
            f'Allowed origins: {_OUTBOUND_ALLOWLIST}'
        )


# ---------------------------------------------------------------------------
# Key resolution helper
# ---------------------------------------------------------------------------

def _resolve_key(provider: str, env_aliases: List[str]) -> Optional[str]:
    """Try Vault → ProviderKeyStore → explicit env aliases in order."""
    if _vault is not None:
        try:
            val = _vault.get_key(provider)
            if val:
                return val
        except Exception:
            pass
    val = ProviderKeyStore.get_key(provider)
    if val:
        return val
    for alias in env_aliases:
        val = os.environ.get(alias)
        if val:
            return val
    return None


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class BaseAdapter:
    provider = 'base'

    def is_available(self) -> bool:
        raise NotImplementedError

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = '',
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def _standard_reply(self, content: str, model: str, usage: dict | None = None) -> Dict[str, Any]:
        return {
            'content': content,
            'model': model,
            'usage': usage or {},
            'provider': self.provider,
        }

    def _check_requests(self):
        if not _HAS_REQUESTS:
            raise RuntimeError('requests package is required for provider adapters')


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------

class OpenAIAdapter(BaseAdapter):
    """OpenAI Chat Completions API (v1).

    Environment variables (resolved in order):
      INTELLI_OPENAI_KEY, OPENAI_API_KEY
    """
    provider = 'openai'
    DEFAULT_MODEL = 'gpt-4o-mini'
    BASE_URL = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')

    def is_available(self) -> bool:
        return bool(_resolve_key('openai', ['OPENAI_API_KEY']))

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._check_requests()
        key = _resolve_key('openai', ['OPENAI_API_KEY'])
        if not key:
            raise RuntimeError('OpenAI API key not configured')
        _check_outbound_url(self.BASE_URL)
        resp = _requests.post(
            f'{self.BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
                **kwargs,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data['choices'][0]['message']['content']
        return self._standard_reply(choice, data.get('model', model), data.get('usage'))


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

class AnthropicAdapter(BaseAdapter):
    """Anthropic Claude Messages API.

    Environment variables (resolved in order):
      INTELLI_ANTHROPIC_KEY, ANTHROPIC_API_KEY
    """
    provider = 'anthropic'
    DEFAULT_MODEL = 'claude-3-haiku-20240307'
    BASE_URL = 'https://api.anthropic.com/v1'
    API_VERSION = '2023-06-01'

    def is_available(self) -> bool:
        return bool(_resolve_key('anthropic', ['ANTHROPIC_API_KEY']))

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system: str = '',
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._check_requests()
        key = _resolve_key('anthropic', ['ANTHROPIC_API_KEY'])
        if not key:
            raise RuntimeError('Anthropic API key not configured')

        # Anthropic requires system to be top-level, not in messages
        _check_outbound_url(self.BASE_URL)
        body: Dict[str, Any] = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if system:
            body['system'] = system

        resp = _requests.post(
            f'{self.BASE_URL}/messages',
            headers={
                'x-api-key': key,
                'anthropic-version': self.API_VERSION,
                'Content-Type': 'application/json',
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data['content'][0]['text'] if data.get('content') else ''
        usage = data.get('usage', {})
        return self._standard_reply(content, data.get('model', model), usage)


# ---------------------------------------------------------------------------
# OpenRouter adapter
# ---------------------------------------------------------------------------

class OpenRouterAdapter(BaseAdapter):
    """OpenRouter Chat Completions API (OpenAI-compatible).

    Routes to any model supported by OpenRouter (GPT, Claude, Mistral, etc.).
    Environment variables: INTELLI_OPENROUTER_KEY
    """
    provider = 'openrouter'
    DEFAULT_MODEL = 'mistralai/mistral-7b-instruct'
    BASE_URL = 'https://openrouter.ai/api/v1'

    def is_available(self) -> bool:
        return bool(_resolve_key('openrouter', []))

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._check_requests()
        key = _resolve_key('openrouter', [])
        if not key:
            raise RuntimeError('OpenRouter API key not configured')
        _check_outbound_url(self.BASE_URL)
        resp = _requests.post(
            f'{self.BASE_URL}/chat/completions',
            headers={
                'Authorization': f'Bearer {key}',
                'HTTP-Referer': 'https://github.com/UrHighness01/Intelli',
                'X-Title': 'Intelli Agent Gateway',
                'Content-Type': 'application/json',
            },
            json={
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
                **kwargs,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data['choices'][0]['message']['content']
        return self._standard_reply(choice, data.get('model', model), data.get('usage'))


# ---------------------------------------------------------------------------
# Ollama adapter (local)
# ---------------------------------------------------------------------------

class OllamaAdapter(BaseAdapter):
    """Local Ollama REST API.

    No API key required; assumes Ollama is running at OLLAMA_BASE_URL
    (default: http://localhost:11434).
    """
    provider = 'ollama'
    DEFAULT_MODEL = 'llama3'

    def __init__(self):
        self.base_url = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')

    def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        if not _HAS_REQUESTS:
            return False
        try:
            _check_outbound_url(self.base_url)
            r = _requests.get(f'{self.base_url}/api/tags', timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._check_requests()
        _check_outbound_url(self.base_url)
        resp = _requests.post(
            f'{self.base_url}/api/chat',
            json={
                'model': model,
                'messages': messages,
                'stream': False,
                'options': {'temperature': temperature, 'num_predict': max_tokens},
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get('message', {}).get('content', '')
        usage = {
            'prompt_tokens': data.get('prompt_eval_count', 0),
            'completion_tokens': data.get('eval_count', 0),
        }
        return self._standard_reply(content, model, usage)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTERS: Dict[str, BaseAdapter] = {
    'openai': OpenAIAdapter(),
    'anthropic': AnthropicAdapter(),
    'openrouter': OpenRouterAdapter(),
    'ollama': OllamaAdapter(),
}


def get_adapter(provider: str) -> BaseAdapter:
    """Return a provider adapter by name.  Raises KeyError for unknown providers."""
    if provider not in _ADAPTERS:
        raise KeyError(f'Unknown provider: {provider!r}.  Available: {list(_ADAPTERS)}')
    return _ADAPTERS[provider]


def available_providers() -> List[str]:
    """Return the list of providers whose keys are currently configured."""
    return [name for name, adapter in _ADAPTERS.items() if adapter.is_available()]
