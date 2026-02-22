import os
import tempfile
import pytest
from providers.provider_adapter import ProviderKeyStore, OpenAIAdapter


def test_keystore_env_fallback(monkeypatch):
    # ensure env fallback works when keyring not set
    monkeypatch.delenv('INTELLI_OPENAI_KEY', raising=False)
    monkeypatch.setenv('INTELLI_OPENAI_KEY', 'env-key-xyz')
    assert ProviderKeyStore.get_key('openai') == 'env-key-xyz'


def test_openai_adapter_raises_when_no_key(monkeypatch):
    monkeypatch.delenv('INTELLI_OPENAI_KEY', raising=False)
    keyring = pytest.importorskip('keyring', reason='keyring not installed; skipping')
    # Ensure keyring returns None by monkeypatching get_password
    monkeypatch.setattr(keyring, 'get_password', lambda service, username: None)
    # Block the file-backed fallback so users.json cannot supply a key either.
    # Using a plain function and swapping the classmethod descriptor works
    # because Python's descriptor protocol invokes it with the class as first arg.
    import providers.provider_adapter as _pa
    monkeypatch.setattr(_pa.ProviderKeyStore, '_read_fallback',
                        classmethod(lambda cls: {}))
    adapter = OpenAIAdapter()
    try:
        adapter.call({'prompt': 'hi'})
        assert False, 'expected RuntimeError for missing key'
    except RuntimeError:
        pass
    # monkeypatch restores everything automatically at teardown.
    # also test when env provides key (get_key env-var branch must still work)
    monkeypatch.setenv('INTELLI_OPENAI_KEY', 'env-key-123')
    adapter = OpenAIAdapter()
    out = adapter.call({'prompt': 'hi'})
    assert out['auth']['Authorization'].endswith('env-key-123')
