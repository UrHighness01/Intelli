from typing import Optional, Dict
import os
import keyring


class ProviderKeyStore:
    """Simple keystore wrapper using the OS keyring with environment fallback.

    Service name is `intelli-agent-gateway` and keys are stored per-provider.
    """
    SERVICE = "intelli-agent-gateway"

    @classmethod
    def set_key(cls, provider: str, key: str):
        keyring.set_password(cls.SERVICE, provider, key)

    @classmethod
    def get_key(cls, provider: str) -> Optional[str]:
        # First try keyring
        try:
            val = keyring.get_password(cls.SERVICE, provider)
            if val:
                return val
        except Exception:
            pass
        # Fallback to env var like INTELLI_{PROVIDER}_KEY
        envname = f"INTELLI_{provider.upper()}_KEY"
        return os.environ.get(envname)


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
