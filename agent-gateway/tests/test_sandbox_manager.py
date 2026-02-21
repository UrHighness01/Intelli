import os
from agent_gateway.sandbox.manager import WorkerManager


def test_worker_manager_health():
    mgr = WorkerManager()
    # by default the bundled worker exists in the repo
    assert os.path.exists(mgr.worker_path)
    ok = mgr.check_health()
    assert ok is True
