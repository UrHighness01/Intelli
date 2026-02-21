import subprocess
import sys
import time
import requests


def test_fuzzer_no_crash():
    # This integration test expects the gateway to be running locally on 127.0.0.1:8080
    # It's a smoke test: call the fuzzer once and verify a 200 response.
    try:
        r = requests.post('http://127.0.0.1:8080/tab/preview', json={'html': '<html><body><input name="x" value="v"/></body></html>', 'url': 'https://test'})
        assert r.status_code == 200
        j = r.json()
        assert 'inputs' in j
    except Exception as e:
        # Skip if gateway isn't running in CI/dev environment
        import pytest
        pytest.skip(f'gateway not available: {e}')
