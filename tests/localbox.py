"""A sandbox that really runs commands in a temp directory, for testing FIX/VERIFY against a real git
repo and real pytest. The agent's fixed paths (/work/...) are mapped into the temp directory."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from itr.agent.paths import PYTHON
from itr.sandbox.base import ExecResult


class LocalSandbox:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _map(self, text: str) -> str:
        # forward slashes everywhere: bash treats backslashes in a path as escapes (matters on Windows)
        out = text.replace(PYTHON, Path(sys.executable).as_posix()).replace("/work", self.root.as_posix())
        if os.name == "nt":      # a native Windows Python wants ';' between PYTHONPATH entries, not ':'
            out = re.sub(r"PYTHONPATH=(\S+)", lambda m: "PYTHONPATH='" + os.pathsep.join(
                re.split(r":(?=[A-Za-z]:/)", m.group(1))) + "'", out)
        return out

    def create(self, image=None): ...
    def destroy(self): ...
    def snapshot(self): return "local"

    def exec(self, cmd, timeout=120):
        try:
            p = subprocess.run([shutil.which("bash"), "-c", self._map(cmd)], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return ExecResult("", "timed out", -1, timed_out=True)
        root = self.root.as_posix()
        return ExecResult(p.stdout.replace(root, "/work"), p.stderr.replace(root, "/work"), p.returncode)

    def write_file(self, path, content):
        f = Path(self._map(path))
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8", newline="") as fh:      # exact bytes: no CRLF translation
            fh.write(content)

    def read_file(self, path):
        with open(self._map(path), encoding="utf-8", newline="") as fh:
            return fh.read()


def bash_available() -> bool:
    """The local sandbox needs a POSIX bash (Git Bash is fine). Windows' WSL launcher in System32 is not."""
    b = shutil.which("bash")
    return bool(b) and "system32" not in b.lower()


def make_repo(root: Path, files: dict[str, str]) -> LocalSandbox:
    repo = Path(root) / "repo"
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    sh = lambda c: subprocess.run(c, shell=True, cwd=repo, check=True, capture_output=True)
    sh("git init -q && git config core.autocrlf false && git add -A && git -c user.email=a@b -c user.name=t commit -qm init")
    (Path(root) / "repro").mkdir(exist_ok=True)
    return LocalSandbox(root)
