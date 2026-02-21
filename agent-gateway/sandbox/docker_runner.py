"""Docker-based sandbox runner scaffold.

Executes the sandbox worker inside a Docker container when Docker is available,
providing stronger OS-level isolation than a bare subprocess.  Falls back to
the plain subprocess worker (sandbox.proxy) when Docker is unavailable.

Requirements (optional — only needed for Docker isolation):
  docker  (pip install docker)

Environment variables:
  SANDBOX_DOCKER_IMAGE    – Image to use, default "python:3.11-slim"
  SANDBOX_DOCKER_TIMEOUT  – Per-call timeout in seconds, default 5
  SANDBOX_DOCKER_MEMORY   – Memory limit, default "64m"
  SANDBOX_DOCKER_CPUS     – CPU quota (float), default "0.5"
  SANDBOX_DOCKER_PIDS     – Max PIDs per container, default 64
  SANDBOX_SECCOMP_PROFILE – Path to a custom seccomp JSON profile;
                            set to "unconfined" to disable seccomp entirely
                            (not recommended in production).  Defaults to
                            Docker's built-in default seccomp profile.

Security posture (hardened defaults):
  * All Linux capabilities dropped (--cap-drop ALL)
  * Privilege escalation blocked (no-new-privileges:true)
  * Default Docker seccomp profile applied (or custom profile via env)
  * Network fully disabled
  * Root filesystem read-only; /tmp via tmpfs (16 MiB)
  * Memory, CPU, and PID limits enforced
  * File-descriptor ulimit: 256 soft / 512 hard
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
from typing import Any, Dict, Optional

try:
    import docker  # type: ignore
    _HAS_DOCKER = True
except ImportError:
    docker = None  # type: ignore
    _HAS_DOCKER = False


class DockerSandboxError(Exception):
    pass


class DockerSandboxRunner:
    """Run whitelisted tool actions inside a Docker container.

    Falls back to the bundled subprocess worker when Docker is unavailable.
    """

    ALLOWED = {'noop', 'echo'}

    def __init__(self):
        self._image = os.environ.get('SANDBOX_DOCKER_IMAGE', 'python:3.11-slim')
        self._timeout = int(os.environ.get('SANDBOX_DOCKER_TIMEOUT', '5'))
        self._memory = os.environ.get('SANDBOX_DOCKER_MEMORY', '64m')
        self._cpus = float(os.environ.get('SANDBOX_DOCKER_CPUS', '0.5'))
        self._pids_limit = int(os.environ.get('SANDBOX_DOCKER_PIDS', '64'))
        self._seccomp_profile = os.environ.get('SANDBOX_SECCOMP_PROFILE', '')
        self._worker_path = os.path.join(os.path.dirname(__file__), 'worker.py')
        self._client: Optional[object] = None
        if _HAS_DOCKER:
            try:
                self._client = docker.from_env()  # type: ignore[union-attr]
            except Exception:
                pass

    def _run_docker(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute worker via docker run with resource limits and hardened security."""
        assert self._client is not None
        payload = json.dumps({'id': 'docker', 'action': action, 'params': params})
        # Read worker source to inject via stdin into the container
        with open(self._worker_path, 'r', encoding='utf-8') as f:
            worker_src = f.read()

        # Pass worker source and payload through stdin, separated by a null byte
        cmd = ['sh', '-c', f'echo \'{worker_src}\' > /tmp/worker.py && echo \'{payload}\' | python /tmp/worker.py']

        # Build security_opt list: always disable privilege escalation.
        # Optionally apply a custom seccomp profile; if unset Docker's default
        # seccomp profile is applied automatically.
        security_opt = ['no-new-privileges:true']
        if self._seccomp_profile:
            security_opt.append(f'seccomp={self._seccomp_profile}')

        # Ulimits: restrict open file descriptors to limit I/O resource abuse
        ulimits = []
        if _HAS_DOCKER:
            try:
                ulimits = [docker.types.Ulimit(name='nofile', soft=256, hard=512)]  # type: ignore[union-attr]
            except Exception:
                pass

        try:
            result = self._client.containers.run(  # type: ignore
                self._image,
                cmd,
                stdin_open=False,
                stdout=True,
                stderr=True,
                remove=True,
                mem_limit=self._memory,
                nano_cpus=int(self._cpus * 1e9),
                pids_limit=self._pids_limit,
                network_disabled=True,
                read_only=True,
                tmpfs={'/tmp': 'size=16m,mode=1777'},
                cap_drop=['ALL'],
                security_opt=security_opt,
                ulimits=ulimits,
                timeout=self._timeout,
            )
            out = result.decode('utf-8') if isinstance(result, bytes) else result
            data = json.loads(out)
            if 'error' in data:
                raise DockerSandboxError(data['error'])
            return data.get('result', data)
        except DockerSandboxError:
            raise
        except Exception as e:
            raise DockerSandboxError(f'Docker run failed: {e}')

    def _run_subprocess(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback: plain subprocess worker."""
        if not os.path.exists(self._worker_path):
            raise DockerSandboxError('worker not found')
        payload = json.dumps({'id': 'subprocess', 'action': action, 'params': params})
        proc = subprocess.Popen(
            [sys.executable, self._worker_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            out, err = proc.communicate(payload, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise DockerSandboxError('subprocess worker timeout')
        if proc.returncode != 0:
            raise DockerSandboxError(f'subprocess worker rc={proc.returncode}: {err.strip()}')
        data = json.loads(out)
        if 'error' in data:
            raise DockerSandboxError(data['error'])
        return data.get('result', data)

    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if action not in self.ALLOWED:
            raise DockerSandboxError(f'action not allowed: {action}')
        if self._client is not None:
            return self._run_docker(action, params)
        return self._run_subprocess(action, params)

    @property
    def backend(self) -> str:
        return 'docker' if self._client is not None else 'subprocess'
