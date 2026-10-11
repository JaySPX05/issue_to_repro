"""Sandbox contract. Implementations (local Docker, Nebius) are owned by the platform teammate."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class Sandbox(Protocol):
    def create(self, image: str | None = None) -> None: ...
    def exec(self, cmd: str, timeout: int = 120) -> ExecResult: ...
    def write_file(self, path: str, content: str) -> None: ...
    def read_file(self, path: str) -> str: ...
    def snapshot(self) -> str: ...
    def destroy(self) -> None: ...
