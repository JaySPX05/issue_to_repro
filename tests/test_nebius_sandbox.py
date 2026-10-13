"""NebiusSandbox against a stand-in for the SDK, built to behave like the real one did in the probe."""
import datetime
import uuid

import pytest

from itr.sandbox.nebius import SandboxError, NebiusSandbox


class OperationTimedOutError(Exception):          # same class name as the SDK's, which is what the adapter checks
    pass


class Result:
    def __init__(self, truncated=False):
        self.truncated, self.elapsed_time, self.cost = truncated, datetime.timedelta(seconds=1), 0.0


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


CLOCK = Clock()


class Img:
    """Immutable-style image: run() returns a NEW image; a file system is a dict copied on each step."""

    def __init__(self, files=None, uid=None):
        self.files = dict(files or {})
        self.uuid = uid or uuid.uuid4()
        self._cmd = None
        self.exit_code, self.stdout, self.stderr, self.result = 0, "", "", Result()

    def run(self, shell, timeout, disposable):
        assert disposable is False, "every exec must keep its state"
        n = Img(self.files, self.uuid)
        n._cmd, n._timeout = shell, timeout
        return n

    def wait(self):
        out = Img(self.files)
        c = self._cmd
        if c.startswith("sleep"):
            raise OperationTimedOutError("Operation x has timed out")
        if c.startswith("write "):
            _, path, text = c.split(" ", 2)
            out.files[path] = text.encode()
        elif c.startswith("cat "):
            path = c[4:]
            if path in out.files:
                out.stdout = out.files[path].decode()
            else:
                out.exit_code, out.stderr = 1, f"cat: {path}: No such file"
        elif c == "fail":
            out.exit_code, out.stderr = 3, "boom"
        elif c.startswith("killed"):                         # the platform's own timer won the race
            CLOCK.t += float(c.split()[1])
            out.exit_code, out.stdout = -1, "partial output\n"
        elif c == "big":
            out.stdout, out.result = "x" * 10, Result(truncated=True)
        return out

    def apply_files(self, files):
        for k, v in files.items():
            assert isinstance(v, bytes), "SDK treats a str as a local path: content must be bytes"
        n = Img(self.files)
        n.files.update(files)
        return n

    def read(self, path):
        return self.files[path]


class Images:
    def __init__(self):
        self.store = {}

    def use(self, ref):
        if isinstance(ref, str) and ref in self.store:
            return self.store[ref]
        return Img()


class Client:
    def __init__(self):
        self.images = Images()


def make():
    CLOCK.t = 0.0
    c = Client()
    sb = NebiusSandbox(client=c, clock=CLOCK)
    sb.create()
    return sb, c


def test_state_carries_over_between_commands():
    sb, _ = make()
    sb.exec("write /work/a hello")
    assert sb.exec("cat /work/a").stdout == "hello"


def test_a_failing_command_is_a_result_not_an_error_and_keeps_going():
    sb, _ = make()
    r = sb.exec("fail")
    assert (r.exit_code, r.stderr, r.timed_out) == (3, "boom", False)
    assert sb.exec("write /work/b ok").exit_code == 0


def test_timeout_becomes_timed_out_and_discards_that_command():
    sb, _ = make()
    sb.exec("write /work/a before")
    r = sb.exec("sleep 90", timeout=5)
    assert r.timed_out is True and r.exit_code == -1 and "5s" in r.stderr
    assert sb.exec("cat /work/a").stdout == "before"          # the sandbox is still usable


def test_write_file_sends_bytes_and_read_file_returns_text():
    sb, _ = make()
    sb.write_file("/work/repro/s.py", "print('é')\n")
    assert sb.read_file("/work/repro/s.py") == "print('é')\n"
    assert sb.exec("cat /work/repro/s.py").stdout == "print('é')\n"


def test_truncated_output_is_flagged():
    sb, _ = make()
    assert "truncated" in sb.exec("big").stdout


def test_snapshot_and_restore_roll_back():
    sb, c = make()
    sb.exec("write /work/a one")
    snap = sb.snapshot()
    c.images.store[snap] = sb._current()                       # the platform keeps saved images by id
    sb.exec("write /work/a two")
    assert sb.exec("cat /work/a").stdout == "two"
    sb.restore(snap)
    assert sb.exec("cat /work/a").stdout == "one"


def test_infrastructure_errors_are_sandbox_errors_not_command_results():
    sb, _ = make()

    class Boom(Img):
        def run(self, shell, timeout, disposable):
            return self

        def wait(self):
            raise ConnectionError("network down")
    sb._image = Boom()
    with pytest.raises(SandboxError, match="ConnectionError"):
        sb.exec("anything")


def test_using_it_before_create_is_an_error_and_destroy_forgets_the_state():
    sb = NebiusSandbox(client=Client())
    with pytest.raises(SandboxError):
        sb.exec("x")
    sb.create()
    sb.destroy()
    with pytest.raises(SandboxError):
        sb.exec("x")


def test_default_image_has_git():
    assert NebiusSandbox(client=Client()).base_image == "python:3.12"


def test_a_command_killed_by_the_platform_at_the_limit_is_a_timeout_too():
    sb, _ = make()
    sb.exec("write /work/a before")
    r = sb.exec("killed 31", timeout=30)                     # returned normally, exit -1, after 31 s
    assert r.timed_out is True and r.exit_code == -1
    assert "partial output" in r.stdout and "timed out after 30s" in r.stderr
    assert sb.exec("cat /work/a").stdout == "before"


def test_a_quick_negative_exit_is_a_failure_not_a_timeout():
    sb, _ = make()
    r = sb.exec("killed 2", timeout=30)                      # for example an out-of-memory kill
    assert r.timed_out is False and r.exit_code == -1
