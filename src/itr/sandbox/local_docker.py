"""LocalDockerSandbox: the `Sandbox` contract on top of the local `docker` CLI.

Used for development and as a fallback while Nebius Sandboxes are not enabled (D25).
One long-lived container per run; commands run with `docker exec`; `snapshot()` is
`docker commit`, so a checkpoint can be reused as the `image` of a later `create()`.

Configuration (environment or .env, see .env.example):
    SANDBOX_IMAGE     default python:3.11 (Debian-based; includes git and a compiler)
    SANDBOX_MEMORY    default 2g   (docker --memory)
    SANDBOX_CPUS      default 2    (docker --cpus)
    SANDBOX_WORKDIR   default /work (relative paths in write_file/read_file resolve here)

Standard library only. Requires Docker Desktop (Windows/macOS) or Docker Engine (Linux).
"""
from __future__ import annotations

import logging
import posixpath
import shlex
import subprocess
import uuid

from itr import config
from itr.sandbox.base import ExecResult

log = logging.getLogger("itr.sandbox.docker")

DEFAULT_IMAGE = "python:3.11"
TIMEOUT_EXIT_CODES = {124, 137}   # `timeout` sends TERM (124) or KILL (137)
GRACE_S = 15                      # extra time for the docker CLI itself before we give up


class SandboxError(Exception):
    """The docker CLI is missing, the daemon is down, or a sandbox operation failed."""


def _setting(name: str, default: str) -> str:
    config.load_env()
    return config.get(name, default) or default


class LocalDockerSandbox:
    """A throwaway Docker container that implements the `Sandbox` protocol."""

    def __init__(self, docker_bin: str = "docker") -> None:
        self.docker = docker_bin
        self.container: str | None = None
        self.workdir = _setting("SANDBOX_WORKDIR", "/work")
        self.snapshots: list[str] = []

    # ---- helpers -------------------------------------------------------
    def _docker(self, args: list[str], input_text: str | None = None,
                timeout: float | None = 120) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.docker, *args], input=input_text, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except FileNotFoundError as e:
            raise SandboxError("docker CLI not found: install Docker Desktop and make sure it is running") from e

    def _require(self) -> str:
        if not self.container:
            raise SandboxError("sandbox not created: call create() first")
        return self.container

    def _abs(self, path: str) -> str:
        return path if path.startswith("/") else posixpath.join(self.workdir, path)

    # ---- Sandbox protocol ----------------------------------------------
    def create(self, image: str | None = None) -> None:
        """Start a fresh container from `image` (default SANDBOX_IMAGE), or from a snapshot ID."""
        if self.container:
            self.destroy()
        image = image or _setting("SANDBOX_IMAGE", DEFAULT_IMAGE)
        name = f"itr-{uuid.uuid4().hex[:10]}"
        args = ["run", "-d", "--name", name, "--label", "itr=1",
                "--memory", _setting("SANDBOX_MEMORY", "2g"), "--cpus", _setting("SANDBOX_CPUS", "2"),
                "-w", self.workdir, "--entrypoint", "sh", image,
                "-c", f"mkdir -p {shlex.quote(self.workdir)} && exec sleep infinity"]
        res = self._docker(args, timeout=900)   # first run may pull the image
        if res.returncode != 0:
            raise SandboxError(f"docker run failed ({res.returncode}): {res.stderr.strip()[:500]}")
        self.container = name
        log.info("sandbox created %s from %s", name, image)

    def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        """Run `cmd` with `sh -c` inside the container, in the workdir. Kills it after `timeout` s."""
        c = self._require()
        args = ["exec", "-w", self.workdir, c, "timeout", "-k", "5", str(timeout), "sh", "-c", cmd]
        try:
            res = self._docker(args, timeout=timeout + GRACE_S)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            return ExecResult(out, err, 124, timed_out=True)
        timed_out = res.returncode in TIMEOUT_EXIT_CODES
        return ExecResult(res.stdout, res.stderr, res.returncode, timed_out=timed_out)

    def write_file(self, path: str, content: str) -> None:
        """Write `content` to `path` (relative paths go under the workdir), creating parent dirs."""
        c = self._require()
        p = self._abs(path)
        script = f"mkdir -p {shlex.quote(posixpath.dirname(p))} && cat > {shlex.quote(p)}"
        res = self._docker(["exec", "-i", c, "sh", "-c", script], input_text=content)
        if res.returncode != 0:
            raise SandboxError(f"write_file {p} failed: {res.stderr.strip()[:300]}")

    def read_file(self, path: str) -> str:
        """Return the text of `path`. Raises FileNotFoundError if it does not exist."""
        c = self._require()
        p = self._abs(path)
        res = self._docker(["exec", c, "cat", p])
        if res.returncode != 0:
            if "No such file" in res.stderr:
                raise FileNotFoundError(p)
            raise SandboxError(f"read_file {p} failed: {res.stderr.strip()[:300]}")
        return res.stdout

    def snapshot(self) -> str:
        """Commit the container's filesystem to an image and return its ID (usable in create())."""
        c = self._require()
        res = self._docker(["commit", c], timeout=600)
        if res.returncode != 0:
            raise SandboxError(f"docker commit failed: {res.stderr.strip()[:300]}")
        image_id = res.stdout.strip()
        self.snapshots.append(image_id)
        return image_id

    def destroy(self) -> None:
        """Remove the container. Safe to call twice. Snapshot images are kept for reuse."""
        if not self.container:
            return
        self._docker(["rm", "-f", self.container], timeout=60)
        log.info("sandbox destroyed %s", self.container)
        self.container = None


if __name__ == "__main__":
    # Live smoke test (needs Docker running): python -m itr.sandbox.local_docker
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sb = LocalDockerSandbox()
    sb.create()
    try:
        print(sb.exec("python -V && git --version && pwd"))
        sb.write_file("hello/repro.py", "print('hello from the sandbox')\n")
        print(sb.exec("python hello/repro.py"))
        print("read back:", sb.read_file("hello/repro.py").strip())
        print(sb.exec("sleep 10", timeout=2))
        print("snapshot:", sb.snapshot()[:19])
    finally:
        sb.destroy()