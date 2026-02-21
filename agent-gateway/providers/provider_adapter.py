from typing import Optional, Dict
import os
import json

# keyring is optional in test/dev environments
try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:
    keyring = None
    _HAS_KEYRING = False


class ProviderKeyStore:
    """Simple keystore wrapper using the OS keyring with environment fallback.

    Service name is `intelli-agent-gateway` and keys are stored per-provider.
    """
    SERVICE = "intelli-agent-gateway"

    @classmethod
    def set_key(cls, provider: str, key: str):
        if _HAS_KEYRING:
            try:
                keyring.set_password(cls.SERVICE, provider, key)
                return
            except Exception:
                pass
        # fallback to file storage (only for dev/tests)
        users = cls._read_fallback()
        users[provider] = key
        cls._write_fallback(users)

    @classmethod
    def get_key(cls, provider: str) -> Optional[str]:
        # First try keyring
        if _HAS_KEYRING:
            try:
                val = keyring.get_password(cls.SERVICE, provider)
                if val:
                    return val
            except Exception:
                pass
        # Fallback to env var like INTELLI_{PROVIDER}_KEY
        envname = f"INTELLI_{provider.upper()}_KEY"
        val = os.environ.get(envname)
        if val:
            return val
        # final fallback to file-backed store
        users = cls._read_fallback()
        return users.get(provider)

    @classmethod
    def _read_fallback(cls) -> Dict[str, str]:
        path = os.path.join(os.path.dirname(__file__), '..', 'users.json')
        path = os.path.normpath(path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def _write_fallback(cls, data: Dict[str, str]):
        path = os.path.join(os.path.dirname(__file__), '..', 'users.json')
        path = os.path.normpath(path)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass


class BaseProviderAdapter:
    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    def get_key(self) -> Optional[str]:
        return ProviderKeyStore.get_key(self.provider_name)

    def call(self, payload: Dict) -> Dict:
        raise NotImplementedError()


class OpenAIAdapter(BaseProviderAdapter):
    """Minimal OpenAI adapter scaffold that returns a request-ready dict.

    Note: This is a scaffold; it does not perform network calls in the prototype.
    """
    def __init__(self):
        super().__init__('openai')

    def call(self, payload: Dict) -> Dict:
        key = self.get_key()
        if not key:
            raise RuntimeError('API key missing for OpenAI')
        # Return a stubbed request descriptor
        return {
            'provider': 'openai',
            'auth': {'Authorization': f'Bearer {key}'},
            'payload': payload,
        }
