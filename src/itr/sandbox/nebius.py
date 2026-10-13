"""Sandbox on Nebius Token Factory Sandboxes (ConTree), built from what the real probe showed
(decisions D45, D46, D47).

How it maps onto the platform:
  A sandbox is a CHAIN OF IMAGES. Every command runs in a fresh microVM started from the current image
  and returns a NEW image holding the result, so file changes carry over to the next command. A saved
  state is just an image id, so `snapshot()` and `restore()` are cheap.

  exec        run(shell=cmd, disposable=False), keep the returned image. A non-zero exit is a normal result.
              A timeout is reported in two ways (OperationTimedOutError, or a negative exit code after running
              for the full limit); both become ExecResult(timed_out=True) and keep the PREVIOUS image (a command
              that times out is treated as if it changed nothing).
  write_file  apply_files({path: content.encode()}); content MUST be bytes, a str would be read as a local path.
  read_file   image.read(path)
  snapshot    the current image id            restore(id)  images.use(id)
  destroy     forget the image (the platform keeps saved images; nothing to kill, every run is a fresh VM)

Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID in the environment (the SDK reads them). Our own keys are never
passed into the sandbox. The default image is `python:3.12`, which already contains git (the slim image does not)."""
from __future__ import annotations

import time

from itr.sandbox.base import ExecResult

DEFAULT_IMAGE = "python:3.12"
KILLED_EXIT_CODES = (-1, -9, 124, 137)       # how a command killed at the time limit can be reported
TRUNCATION_NOTE = "\n[output truncated by the sandbox]"


class SandboxError(RuntimeError):
    """The sandbox itself failed (not the command inside it)."""


class NebiusSandbox:
    def __init__(self, image: str = DEFAULT_IMAGE, client=None, clock=time.monotonic):
        self.base_image = image
        self._client = client
        self._clock = clock
        self._image = None

    # -- helpers -----------------------------------------------------------------
    def _api(self):
        if self._client is None:
            from contree_sdk import ContreeSync            # imported late so the rest of the repo needs no SDK
            self._client = ContreeSync()
        return self._client

    def _current(self):
        if self._image is None:
            raise SandboxError("sandbox not created (call create() first)")
        return self._image

    # -- the Sandbox contract ----------------------------------------------------
    def create(self, image: str | None = None) -> None:
        self._image = self._api().images.use(image or self.base_image)

    def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        current = self._current()
        started = self._clock()
        try:
            out = current.run(shell=cmd, timeout=timeout, disposable=False).wait()
        except Exception as e:
            if type(e).__name__ == "OperationTimedOutError":
                return ExecResult("", f"command timed out after {timeout}s", -1, timed_out=True)
            raise SandboxError(f"{type(e).__name__}: {e}") from e
        stdout, stderr = out.stdout or "", out.stderr or ""
        # A timeout can arrive in TWO ways (found by the live smoke test, decisions D48): the client's timer
        # raises OperationTimedOutError, or the platform's own timer kills the command and returns a normal
        # result with a negative exit code. Both timers use the same limit, so which one wins is a race.
        # A killed command that ran for at least the limit is a timeout; a quick negative exit (for example
        # an out-of-memory kill) is just a failure.
        if out.exit_code in KILLED_EXIT_CODES and self._clock() - started >= timeout:
            note = f"command timed out after {timeout}s"
            return ExecResult(stdout, (stderr + "\n" if stderr else "") + note, -1, timed_out=True)
        self._image = out                                   # the next command starts from this state
        if getattr(out.result, "truncated", False):
            stdout += TRUNCATION_NOTE
        return ExecResult(stdout, stderr, out.exit_code)

    def write_file(self, path: str, content: str) -> None:
        try:
            self._image = self._current().apply_files({path: content.encode("utf-8")})
        except Exception as e:
            raise SandboxError(f"write_file({path}): {type(e).__name__}: {e}") from e

    def read_file(self, path: str) -> str:
        try:
            return self._current().read(path).decode("utf-8", errors="replace")
        except Exception as e:
            raise SandboxError(f"read_file({path}): {type(e).__name__}: {e}") from e

    def snapshot(self) -> str:
        uuid = self._current().uuid
        if uuid is None:
            raise SandboxError("the current state has no image id yet (run a command first)")
        return str(uuid)

    def destroy(self) -> None:
        self._image = None

    # -- addition proposed to the contract (needs the teammate's agreement) -------
    def restore(self, snapshot_id: str) -> None:
        """Go back to a state returned by snapshot(). Everything done since is discarded."""
        self._image = self._api().images.use(snapshot_id)
