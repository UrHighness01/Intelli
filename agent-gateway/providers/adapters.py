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
  openai          – OpenAI Chat Completions API         INTELLI_OPENAI_KEY or OPENAI_API_KEY
  anthropic        – Anthropic Messages API              INTELLI_ANTHROPIC_KEY or ANTHROPIC_API_KEY
  openrouter       – OpenRouter Chat Completions API     INTELLI_OPENROUTER_KEY
  github_copilot   – GitHub Copilot Chat API             INTELLI_GITHUB_COPILOT_TOKEN or GITHUB_COPILOT_TOKEN or GITHUB_TOKEN
  ollama           – Local Ollama REST API               no key required; OLLAMA_BASE_URL

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
import json as _json
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
        'https://api.githubcopilot.com',
        'https://api.individual.githubcopilot.com',
        'https://api.github.com',            # Copilot token exchange
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
# Provider settings store
# ---------------------------------------------------------------------------

class ProviderSettingsStore:
    """Persist per-provider settings (model_id, endpoint) in a JSON sidecar file."""
    _path = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'provider_settings.json'))

    @classmethod
    def get(cls, provider: str) -> dict:
        try:
            with open(cls._path, 'r', encoding='utf-8') as f:
                return _json.load(f).get(provider, {})
        except Exception:
            return {}

    @classmethod
    def set(cls, provider: str, settings: dict) -> None:
        try:
            with open(cls._path, 'r', encoding='utf-8') as f:
                data = _json.load(f)
        except Exception:
            data = {}
        data[provider] = {**data.get(provider, {}), **settings}
        with open(cls._path, 'w', encoding='utf-8') as f:
            _json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class BaseAdapter:
    provider = 'base'
    requires_key: bool = True  # set to False for local providers (e.g. Ollama)

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

    def _get_default_model(self) -> str:
        return ProviderSettingsStore.get('openai').get('model_id') or self.DEFAULT_MODEL

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = '',
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not model:
            model = self._get_default_model()
        self._check_requests()
        key = _resolve_key('openai', ['OPENAI_API_KEY'])
        if not key:
            raise RuntimeError('OpenAI API key not configured')
        _check_outbound_url(self.BASE_URL)
        # Strip Anthropic-only 'system' key — it's already in messages as role:system
        oai_kwargs = {k: v for k, v in kwargs.items() if k != 'system'}
        resp = _requests.post(
            f'{self.BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
                **oai_kwargs,
            },
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f'OpenAI API error {resp.status_code}: {resp.text[:500]}')
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

    def _get_default_model(self) -> str:
        return ProviderSettingsStore.get('anthropic').get('model_id') or self.DEFAULT_MODEL

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = '',
        temperature: float = 0.7,
        max_tokens: int = 1024,
        system: str = '',
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not model:
            model = self._get_default_model()
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

    def _get_default_model(self) -> str:
        return ProviderSettingsStore.get('openrouter').get('model_id') or self.DEFAULT_MODEL

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = '',
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not model:
            model = self._get_default_model()
        self._check_requests()
        key = _resolve_key('openrouter', [])
        if not key:
            raise RuntimeError('OpenRouter API key not configured')
        # Strip Anthropic-only 'system' key — it's already in messages as role:system
        oai_kwargs = {k: v for k, v in kwargs.items() if k != 'system'}
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
                **oai_kwargs,
            },
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f'OpenRouter API error {resp.status_code}: {resp.text[:500]}')
        data = resp.json()
        choice = data['choices'][0]['message']['content']
        return self._standard_reply(choice, data.get('model', model), data.get('usage'))


# ---------------------------------------------------------------------------
# Ollama adapter (local)
# ---------------------------------------------------------------------------

class OllamaAdapter(BaseAdapter):
    """Local Ollama REST API.

    No API key required; assumes Ollama is running at OLLAMA_BASE_URL
    (default: http://localhost:11434).  A custom endpoint and default model
    can be set through the admin provider settings store.
    """
    provider = 'ollama'
    requires_key = False
    DEFAULT_MODEL = 'llama3.1:8b'

    def _get_base_url(self) -> str:
        """Return the Ollama endpoint, preferring admin settings over env var."""
        endpoint = ProviderSettingsStore.get('ollama').get('endpoint', '').strip()
        if endpoint:
            base = endpoint.rstrip('/')
            # Dynamically whitelist admin-configured endpoint at runtime
            p = _urlparse.urlparse(base)
            origin = f'{p.scheme}://{p.netloc}'
            if origin not in _OUTBOUND_ALLOWLIST:
                _OUTBOUND_ALLOWLIST.append(origin)
            return base
        return os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')

    def _get_default_model(self) -> str:
        return ProviderSettingsStore.get('ollama').get('model_id') or self.DEFAULT_MODEL

    def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        if not _HAS_REQUESTS:
            return False
        try:
            base_url = self._get_base_url()
            _check_outbound_url(base_url)
            r = _requests.get(f'{base_url}/api/tags', timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = '',
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not model:
            model = self._get_default_model()
        self._check_requests()
        base_url = self._get_base_url()
        _check_outbound_url(base_url)
        resp = _requests.post(
            f'{base_url}/api/chat',
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
# GitHub Copilot adapter
# ---------------------------------------------------------------------------

class GitHubCopilotAdapter(BaseAdapter):
    """GitHub Copilot Chat Completions API (OpenAI-compatible).

    GitHub Copilot requires a two-step token exchange:
      1. User supplies a GitHub OAuth/PAT token (with `copilot` scope).
      2. We exchange it for a short-lived Copilot API token via
         GET https://api.github.com/copilot_internal/v2/token
      3. The Copilot API token encodes the real base URL in its payload
         (``proxy-ep=...``); we parse that out and use it.

    Environment variables (resolved in order):
      INTELLI_GITHUB_COPILOT_TOKEN, GITHUB_COPILOT_TOKEN, GITHUB_TOKEN
    """
    provider = 'github_copilot'
    DEFAULT_MODEL = 'gpt-4o'
    _TOKEN_EXCHANGE_URL = 'https://api.github.com/copilot_internal/v2/token'
    _DEFAULT_BASE_URL = 'https://api.individual.githubcopilot.com'

    # Available Copilot models (as of early 2026)
    KNOWN_MODELS = [
        'gpt-4o',
        'gpt-4o-mini',
        'gpt-4.1',
        'gpt-4.1-mini',
        'gpt-4.5',
        'gpt-5',
        'gpt-5-mini',
        'o1',
        'o1-mini',
        'o3',
        'o3-mini',
        'o4-mini',
        'claude-sonnet-4.5',
        'claude-sonnet-4.6',
    ]

    # In-memory Copilot token cache: { github_token_hash -> (copilot_token, base_url, expires_at_ms) }
    _cache: Dict[str, tuple] = {}

    def is_available(self) -> bool:
        return bool(_resolve_key('github_copilot', [
            'GITHUB_COPILOT_TOKEN', 'GITHUB_TOKEN',
        ]))

    def _get_default_model(self) -> str:
        return ProviderSettingsStore.get('github_copilot').get('model_id') or self.DEFAULT_MODEL

    @staticmethod
    def _parse_proxy_ep(copilot_token: str) -> Optional[str]:
        """Extract proxy-ep from the Copilot token and convert to api.* URL."""
        import re as _re
        m = _re.search(r'(?:^|;)\s*proxy-ep=([^;\s]+)', copilot_token, _re.IGNORECASE)
        if not m:
            return None
        ep = m.group(1).strip()
        # Convert proxy.* -> api.*
        host = ep.replace('https://', '').replace('http://', '')
        host = _re.sub(r'^proxy\.', 'api.', host, flags=_re.IGNORECASE)
        return f'https://{host}' if host else None

    def _resolve_copilot_token(self, github_token: str) -> tuple:
        """Exchange a GitHub token for a short-lived Copilot API token.

        Returns (copilot_token, base_url).  Caches result until 5 min before expiry.
        """
        import time as _time

        # Use the token directly as the cache key — it is already in process memory
        # as a string.  Hashing a credential (even with a strong algorithm) triggers
        # CodeQL's py/weak-sensitive-data-hashing rule; avoiding hashing is cleaner.
        key = github_token
        now_ms = int(_time.time() * 1000)

        if key in self._cache:
            cop_tok, base_url, expires_at = self._cache[key]
            if expires_at - now_ms > 5 * 60 * 1000:  # more than 5 min remaining
                return cop_tok, base_url

        # Exchange with GitHub
        self._check_requests()
        resp = _requests.get(
            self._TOKEN_EXCHANGE_URL,
            headers={
                'Accept': 'application/json',
                'Authorization': f'Bearer {github_token}',
                'Editor-Version': 'vscode/1.97.0',
                'Editor-Plugin-Version': 'copilot/1.249.0',
                'User-Agent': 'GitHubCopilotChat/0.24.0',
            },
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(
                f'GitHub Copilot token exchange failed: HTTP {resp.status_code} — '
                'Check that your GitHub token has the "copilot" scope.'
            )
        data = resp.json()
        cop_tok = data.get('token', '')
        if not cop_tok:
            raise RuntimeError('Copilot token exchange returned empty token')

        # expires_at is a unix timestamp (seconds)
        raw_exp = data.get('expires_at', 0)
        expires_at_ms = int(raw_exp) * 1000 if raw_exp < 10_000_000_000 else int(raw_exp)

        base_url = self._parse_proxy_ep(cop_tok) or self._DEFAULT_BASE_URL

        # Whitelist the base URL dynamically
        p = _urlparse.urlparse(base_url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in _OUTBOUND_ALLOWLIST:
            _OUTBOUND_ALLOWLIST.append(origin)

        self._cache[key] = (cop_tok, base_url, expires_at_ms)
        return cop_tok, base_url

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = '',
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not model:
            model = self._get_default_model()
        self._check_requests()
        github_token = _resolve_key('github_copilot', [
            'GITHUB_COPILOT_TOKEN', 'GITHUB_TOKEN',
        ])
        if not github_token:
            raise RuntimeError('GitHub Copilot token not configured')

        cop_tok, base_url = self._resolve_copilot_token(github_token)
        _check_outbound_url(base_url)

        # o1/o3/o4-family models don't accept a temperature parameter
        _o_series = model.startswith(('o1', 'o3', 'o4'))
        # Strip Anthropic-only 'system' key — it's already in messages as role:system
        oai_kwargs = {k: v for k, v in kwargs.items() if k != 'system'}

        # Normalise messages: Copilot does not support vision content arrays.
        # If any message has a list content, flatten to text-only.
        def _flatten_content(c: Any) -> str:
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = []
                for item in c:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        parts.append(item.get('text', ''))
                    elif isinstance(item, str):
                        parts.append(item)
                return '\n'.join(p for p in parts if p)
            return str(c) if c is not None else ''

        normalised_msgs = [
            {**m, 'content': _flatten_content(m.get('content'))}
            if isinstance(m.get('content'), list)
            else m
            for m in messages
        ]

        body: Dict[str, Any] = {
            'model': model,
            'messages': normalised_msgs,
            'max_tokens': max_tokens,
            **oai_kwargs,
        }
        if not _o_series:
            body['temperature'] = temperature

        resp = _requests.post(
            f'{base_url}/chat/completions',
            headers={
                'Authorization': f'Bearer {cop_tok}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                # Must look like a real Copilot-enabled editor
                'Copilot-Integration-Id': 'vscode-chat',
                'Editor-Version': 'vscode/1.97.0',
                'Editor-Plugin-Version': 'copilot-chat/0.24.0',
                'User-Agent': 'GitHubCopilotChat/0.24.0',
                'openai-intent': 'conversation-panel',
            },
            json=body,
            timeout=60,
        )
        if not resp.ok:
            import logging as _logging
            _log = _logging.getLogger('intelli.copilot')
            # Summarise each message: role + content type + length
            msg_summary = [
                {
                    'role': m.get('role'),
                    'content_type': type(m.get('content')).__name__,
                    'content_len': len(str(m.get('content', ''))),
                }
                for m in normalised_msgs
            ]
            _log.warning(
                'Copilot %s — model=%s body_keys=%s messages=%s resp=%s',
                resp.status_code, model, list(body.keys()), msg_summary, resp.text[:800],
            )
            raise RuntimeError(f'GitHub Copilot API error {resp.status_code}: {resp.text[:800]}')
        data = resp.json()
        if not data.get('choices'):
            raise RuntimeError(f'GitHub Copilot returned no choices: {str(data)[:400]}')
        choice = data['choices'][0]['message']['content']
        return self._standard_reply(choice, data.get('model', model), data.get('usage'))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTERS: Dict[str, BaseAdapter] = {
    'openai': OpenAIAdapter(),
    'anthropic': AnthropicAdapter(),
    'openrouter': OpenRouterAdapter(),
    'ollama': OllamaAdapter(),
    'github_copilot': GitHubCopilotAdapter(),
}


def get_adapter(provider: str) -> BaseAdapter:
    """Return a provider adapter by name.  Raises KeyError for unknown providers."""
    if provider not in _ADAPTERS:
        raise KeyError(f'Unknown provider: {provider!r}.  Available: {list(_ADAPTERS)}')
    return _ADAPTERS[provider]


def available_providers() -> List[str]:
    """Return the list of providers whose keys are currently configured."""
    return [name for name, adapter in _ADAPTERS.items() if adapter.is_available()]
