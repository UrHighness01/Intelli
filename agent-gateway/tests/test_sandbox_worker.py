import os
import sys
import json
import subprocess
import tempfile

from agent_gateway.sandbox.proxy import SandboxProxy


def get_worker_path():
    # worker next to module
    here = os.path.join(os.getcwd(), 'agent-gateway', 'sandbox', 'worker.py')
    return here


def test_worker_process_echo():
    worker = get_worker_path()
    assert os.path.exists(worker)

    payload = {"action": "echo", "params": {"msg": "hello"}}
    p = subprocess.Popen([sys.executable, worker], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(json.dumps(payload), timeout=3)
    assert p.returncode == 0
    data = json.loads(out)
    assert 'result' in data
    assert data['result']['echo']['msg'] == 'hello'


def test_proxy_uses_worker_via_env(monkeypatch):
    worker = get_worker_path()
    monkeypatch.setenv('SANDBOX_WORKER_PATH', worker)
    proxy = SandboxProxy()
    res = proxy.execute('echo', {'msg': 'from-proxy'})
    # proxy returns the worker's result dict or wrapped result
    if isinstance(res, dict) and 'echo' in res:
        assert res['echo']['msg'] == 'from-proxy'
    else:
        # if wrapped under 'result'
        assert res.get('result', {}).get('echo', {}).get('msg') == 'from-proxy'
