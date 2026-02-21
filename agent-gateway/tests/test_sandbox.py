from sandbox.proxy import SandboxProxy, SandboxError


def test_noop():
    p = SandboxProxy()
    res = p.execute('noop', {})
    assert res['status'] == 'ok'


def test_echo_serializable():
    p = SandboxProxy()
    res = p.execute('echo', {'a': 1, 'b': 'x'})
    assert res['status'] == 'ok' and res['echo']['a'] == 1


def test_disallowed_action():
    p = SandboxProxy()
    try:
        p.execute('run_shell', {'cmd': 'ls'})
        assert False, 'expected SandboxError'
    except SandboxError:
        pass
