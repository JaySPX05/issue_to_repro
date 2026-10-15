import http.client
import urllib.error

import pytest

from itr import github
from itr.retry import RetrySandbox, is_transient, retry_call
from itr.agent.machine import run_machine
from itr.agent.states import State
from tests.fakes import BUG, FakeLLM, FakeSandbox, happy_stages, make_ctx

NOSLEEP = lambda s: None


@pytest.mark.parametrize("exc", [http.client.RemoteDisconnected("Remote end closed connection without response"),
                                 TimeoutError("read timed out"), ConnectionResetError("Connection reset by peer"),
                                 RuntimeError("SandboxError: ApiTimeoutError(timeout_type='read')"),
                                 urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)])
def test_transient_errors_are_recognised(exc):
    assert is_transient(exc)


@pytest.mark.parametrize("exc", [urllib.error.HTTPError("u", 404, "Not Found", {}, None), FileNotFoundError("no file"),
                                 ValueError("bad schema"), RuntimeError("permission denied")])
def test_real_errors_are_not_retried(exc):
    assert not is_transient(exc)


def test_retry_succeeds_after_blips_and_counts_attempts():
    calls, seen = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("timed out")
        return "ok"
    assert retry_call(flaky, sleep=NOSLEEP, on_retry=lambda n, e: seen.append(n)) == "ok"
    assert len(calls) == 3 and seen == [1, 2]


def test_retry_gives_up_and_reraises_after_the_last_attempt():
    calls = []

    def always():
        calls.append(1)
        raise TimeoutError("timed out")
    with pytest.raises(TimeoutError):
        retry_call(always, sleep=NOSLEEP)
    assert len(calls) == 3


def test_non_transient_error_is_raised_immediately():
    calls = []

    def bad():
        calls.append(1)
        raise ValueError("bad")
    with pytest.raises(ValueError):
        retry_call(bad, sleep=NOSLEEP)
    assert len(calls) == 1


class Flaky(FakeSandbox):
    def __init__(self, fail_times):
        super().__init__()
        self.fail_times = fail_times

    def exec(self, cmd, timeout=120):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("SandboxError: ApiTimeoutError: read timeout")
        return super().exec(cmd, timeout)


def test_sandbox_wrapper_retries_and_passes_attributes_through():
    inner = Flaky(2)
    events = []
    sb = RetrySandbox(inner, on_retry=lambda c, n, e: events.append((c, n)), sleep=NOSLEEP)
    assert sb.exec("echo hi").exit_code == 0
    assert events == [("exec", 1), ("exec", 2)]
    sb.write_file("/a", "x")
    assert sb.files["/a"] == "x" and sb.read_file("/a") == "x"       # attribute + method passthrough


def test_machine_survives_a_sandbox_blip_in_a_stage(monkeypatch):
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG]}), sandbox=Flaky(1))
    st = happy_stages()

    def setup(c):
        c.sh("echo x")                                    # first call fails once, then succeeds
        c.run.env = {}
        return State.REPRO
    st[State.SETUP] = setup
    monkeypatch.setattr("itr.retry.time.sleep", NOSLEEP)
    run_machine(ctx, stages=st)
    assert ctx.run.outcome == "CONFIRMED"
    assert "sandbox_retry" in log.types()


def test_github_get_retries_a_dropped_connection(monkeypatch):
    calls = []

    def fake_once(url, token=None):
        calls.append(1)
        if len(calls) < 3:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return {"ok": True}
    monkeypatch.setattr(github, "_http_get_once", fake_once)
    monkeypatch.setattr("itr.retry.time.sleep", NOSLEEP)
    assert github._http_get("https://api.github.com/x") == {"ok": True} and len(calls) == 3
