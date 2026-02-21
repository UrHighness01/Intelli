"""Root conftest.py — mirrors the per-test conftest so pytest discovers the
agent-gateway package whether invoked from the repo root or a subdirectory.
"""
import sys, os

_GW_DIR = os.path.join(os.path.dirname(__file__), 'agent-gateway')
if _GW_DIR not in sys.path:
    sys.path.insert(0, _GW_DIR)

_WORKER = os.path.join(_GW_DIR, 'sandbox', 'worker.py')
if os.path.exists(_WORKER) and not os.environ.get('SANDBOX_WORKER_PATH'):
    os.environ['SANDBOX_WORKER_PATH'] = _WORKER
