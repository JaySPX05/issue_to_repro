"""Tests for LocalDockerSandbox. The unit tests fake the docker CLI, so Docker is not needed.
Set ITR_DOCKER_TESTS=1 to also run the live test against a real Docker daemon."""
from __future__ import annotations

import os
import subprocess

import pytest

from itr.sandbox import local_docker
from itr.sandbox.local_docker import LocalDockerSandbox, SandboxError


class FakeDocker:
    """Records docker CLI calls and returns queued (returncode, stdout, stderr) results."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.results: list[tuple[int, str, str]] = []
        self.raise_timeout = False

    def __call__(self, args, input=None, timeout=None, **kw):
        self.calls.append({"args": args, "input": input, "timeout": timeout})
        if self.raise_timeout and args[1] == "exec":
            raise subprocess.TimeoutExpired(args, timeout, output="partial", stderr="")
        rc, out, err = self.results.pop(0) if self.results else (0, "", "")
        return subprocess.CompletedProcess(args, rc, out, err)


@pytest.fixture
def fake(monkeypatch):
    f = FakeDocker()
    monkeypatch.setattr(local_docker.subprocess, "run", f)
    monkeypatch.setattr(local_docker.config, "load_env", lambda *a, **k: [])
    for k in ("SANDBOX_IMAGE", "SANDBOX_MEMORY", "SANDBOX_CPUS", "SANDBOX_WORKDIR"):
        monkeypatch.delenv(k, raising=False)
    return f


def made(fake) -> LocalDockerSandbox:
    sb = LocalDockerSandbox()
    sb.create()
    fake.calls.clear()
    return sb


def test_create_runs_container_with_defaults(fake):
    sb = LocalDockerSandbox()
    sb.create()
    args = fake.calls[0]["args"]
    assert args[:3] == ["docker", "run", "-d"]
    assert "python:3.11" in args and "/work" in args and "--memory" in args
    assert sb.container and sb.container.startswith("itr-")


def test_create_uses_snapshot_image(fake):
    LocalDockerSandbox().create("sha256:abc")
    assert "sha256:abc" in fake.calls[0]["args"]


def test_create_failure_raises(fake):
    fake.results.append((125, "", "pull access denied"))
    with pytest.raises(SandboxError, match="pull access denied"):
        LocalDockerSandbox().create()


def test_exec_wraps_command_in_timeout(fake):
    sb = made(fake)
    fake.results.append((1, "out", "Traceback ... IndexError"))
    r = sb.exec("python repro.py", timeout=30)
    args = fake.calls[0]["args"]
    assert args[1] == "exec" and args[-3:] == ["sh", "-c", "python repro.py"]
    assert ["timeout", "-k", "5", "30"] == args[args.index("timeout"):args.index("timeout") + 4]
    assert (r.stdout, r.stderr, r.exit_code, r.timed_out) == ("out", "Traceback ... IndexError", 1, False)


def test_exec_reports_timeout_from_exit_code(fake):
    sb = made(fake)
    fake.results.append((124, "", ""))
    assert sb.exec("sleep 99", timeout=1).timed_out is True


def test_exec_reports_timeout_when_cli_hangs(fake):
    sb = made(fake)
    fake.raise_timeout = True
    r = sb.exec("sleep 99", timeout=1)
    assert r.timed_out and r.exit_code == 124 and r.stdout == "partial"


def test_write_file_pipes_content_and_resolves_relative_path(fake):
    sb = made(fake)
    sb.write_file("a b/repro.py", "print('hi')\n")
    call = fake.calls[0]
    assert call["input"] == "print('hi')\n"
    assert "'/work/a b/repro.py'" in call["args"][-1]


def test_read_file_missing_raises_file_not_found(fake):
    sb = made(fake)
    fake.results.append((1, "", "cat: /work/x: No such file or directory"))
    with pytest.raises(FileNotFoundError):
        sb.read_file("x")


def test_snapshot_returns_image_id(fake):
    sb = made(fake)
    fake.results.append((0, "sha256:deadbeef\n", ""))
    assert sb.snapshot() == "sha256:deadbeef"
    assert fake.calls[0]["args"][1] == "commit"


def test_destroy_is_idempotent(fake):
    sb = made(fake)
    sb.destroy()
    sb.destroy()
    assert [c["args"][1] for c in fake.calls] == ["rm"]
    with pytest.raises(SandboxError, match="create"):
        sb.exec("ls")


def test_missing_docker_cli_gives_clear_error(monkeypatch):
    monkeypatch.setattr(local_docker.config, "load_env", lambda *a, **k: [])
    with pytest.raises(SandboxError, match="Docker Desktop"):
        LocalDockerSandbox(docker_bin="definitely-not-docker-xyz").create()


@pytest.mark.skipif(os.environ.get("ITR_DOCKER_TESTS") != "1", reason="set ITR_DOCKER_TESTS=1 to run")
def test_live_docker_roundtrip():
    sb = LocalDockerSandbox()
    sb.create()
    try:
        sb.write_file("repro.py", "import sys; print('hi'); sys.exit(3)\n")
        r = sb.exec("python repro.py")
        assert r.stdout.strip() == "hi" and r.exit_code == 3
        assert sb.read_file("repro.py").startswith("import sys")
        assert sb.exec("sleep 10", timeout=1).timed_out
        snap = sb.snapshot()
        sb.create(snap)
        assert "hi" in sb.exec("python repro.py").stdout
    finally:
        sb.destroy()