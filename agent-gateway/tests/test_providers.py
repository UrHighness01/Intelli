import os
import tempfile
from providers.provider_adapter import ProviderKeyStore, OpenAIAdapter
import os


def test_keystore_env_fallback(monkeypatch):
    # ensure env fallback works when keyring not set
    monkeypatch.delenv('INTELLI_OPENAI_KEY', raising=False)
    monkeypatch.setenv('INTELLI_OPENAI_KEY', 'env-key-xyz')
    assert ProviderKeyStore.get_key('openai') == 'env-key-xyz'


def test_openai_adapter_raises_when_no_key(monkeypatch):
    monkeypatch.delenv('INTELLI_OPENAI_KEY', raising=False)
    # Ensure keyring returns None by monkeypatching get_password
    import keyring

    orig = keyring.get_password

    def _none(service, username):
        return None

    monkeypatch.setattr(keyring, 'get_password', _none)
    adapter = OpenAIAdapter()
    try:
        adapter.call({'prompt': 'hi'})
        assert False, 'expected RuntimeError for missing key'
    except RuntimeError:
        pass
    finally:
        monkeypatch.setattr(keyring, 'get_password', orig)
    # also test when env provides key
    monkeypatch.setenv('INTELLI_OPENAI_KEY', 'env-key-123')
    adapter = OpenAIAdapter()
    out = adapter.call({'prompt': 'hi'})
    assert out['auth']['Authorization'].endswith('env-key-123')
