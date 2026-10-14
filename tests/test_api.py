"""Tests for itr.api with an in-memory store and fake runs (no Docker, no model, no network)."""
from __future__ import annotations

import json
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from itr import api  # noqa: E402
from itr.store import Store  # noqa: E402

URL = "https://github.com/pallets/click/issues/2877"


def fake_run(run_id, issue_url, emit):
    emit("stage_started", {"stage": "FETCH"})
    emit("artifact", {"name": "issue", "content": {"url": issue_url}})
    emit("stage_finished", {"stage": "FETCH", "next": "TRIAGE"})
    emit("stage_started", {"stage": "TRIAGE"})
    emit("model_call", {"stage": "triage", "tier": "nano", "tokens_in": 1000, "tokens_out": 500})
    emit("stage_finished", {"stage": "TRIAGE", "next": "DONE"})
    emit("outcome", {"outcome": "NEEDS_MAINTAINER"})


def make(run_fn=fake_run, **kw):
    store = Store(":memory:")
    return TestClient(api.create_app(store=store, run_fn=run_fn, web_dir="/nonexistent",
                                      seed_dir="/nonexistent", **kw)), store


def wait_done(store, run_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        run = store.get_run(run_id)
        if run and run["state"] == "done":
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def read_sse(client, path, headers=None):
    """Return (messages, ended) from an SSE stream: each message is the parsed data dict."""
    msgs, ended, event = [], False, None
    with client.stream("GET", path, headers=headers or {}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                if event == "end":
                    ended = True
                    break
                msgs.append(json.loads(line[6:]))
            elif line == "":
                event = None
    return msgs, ended


def test_health():
    client, _ = make()
    assert client.get("/health").json() == {"ok": True, "new_runs": True, "mode": "background"}


def test_post_runs_validates_url():
    client, _ = make()
    assert client.post("/runs", json={"issue_url": "https://example.com/x"}).status_code == 422
    assert client.post("/runs", json={}).status_code == 422


def test_full_run_then_final_state():
    client, store = make()
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    wait_done(store, rid)
    s = client.get(f"/runs/{rid}").json()
    assert s["outcome"] == "NEEDS_MAINTAINER" and s["state"] == "done"
    assert [x["stage"] for x in s["stages"]] == ["FETCH", "TRIAGE"]
    assert s["artifacts"]["issue"]["url"] == URL and s["tokens"] == {"nano": 1500}
    assert client.get("/runs/missing").status_code == 404
    assert client.get("/runs").json()[0]["id"] == rid


def test_sse_replays_stored_events_with_running_cost():
    client, store = make()
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    wait_done(store, rid)
    msgs, ended = read_sse(client, f"/runs/{rid}/events")
    assert ended and [m["seq"] for m in msgs] == list(range(1, 8))
    assert msgs[-1]["type"] == "outcome" and msgs[-1]["payload"]["outcome"] == "NEEDS_MAINTAINER"
    assert msgs[3]["cost_usd"] == 0 and msgs[4]["cost_usd"] == pytest.approx((1000 * 0.06 + 500 * 0.24) / 1e6)


def test_sse_streams_live_events_as_they_happen():
    gate = threading.Event()

    def slow_run(run_id, issue_url, emit):
        emit("stage_started", {"stage": "FETCH"})
        gate.wait(5)
        emit("outcome", {"outcome": "CONFIRMED"})

    client, _ = make(slow_run)
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    threading.Timer(0.5, gate.set).start()      # the outcome arrives while the client is listening
    msgs, ended = read_sse(client, f"/runs/{rid}/events")
    assert ended and [m["type"] for m in msgs] == ["stage_started", "outcome"]


def test_sse_resumes_after_last_event_id():
    client, store = make()
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    wait_done(store, rid)
    msgs, _ = read_sse(client, f"/runs/{rid}/events", headers={"Last-Event-ID": "5"})
    assert [m["seq"] for m in msgs] == [6, 7] and msgs[0]["cost_usd"] > 0
    assert client.get("/runs/missing/events").status_code == 404


def test_crashing_run_still_gets_an_outcome():
    def boom(run_id, issue_url, emit):
        emit("stage_started", {"stage": "FETCH"})
        raise RuntimeError("docker CLI not found")

    client, store = make(boom)
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    run = wait_done(store, rid)
    assert run["outcome"] == "ERROR"
    assert "docker CLI not found" in client.get(f"/runs/{rid}").json()["reason"]


def test_concurrency_limit_returns_429():
    gate = threading.Event()

    def blocked(run_id, issue_url, emit):
        gate.wait(5)
        emit("outcome", {"outcome": "ERROR"})

    client, store = make(blocked, max_concurrent=1)
    first = client.post("/runs", json={"issue_url": URL})
    assert first.status_code == 200
    assert client.post("/runs", json={"issue_url": URL}).status_code == 429
    gate.set()
    wait_done(store, first.json()["run_id"])
    assert client.post("/runs", json={"issue_url": URL}).status_code == 200


def test_replay_copies_events_without_running_the_agent():
    calls = []

    def counted(run_id, issue_url, emit):
        calls.append(run_id)
        fake_run(run_id, issue_url, emit)

    client, store = make(counted)
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    wait_done(store, rid)
    rep = client.post(f"/runs/{rid}/replay?speed=0").json()
    assert rep["replay_of"] == rid and rep["run_id"] != rid
    msgs, ended = read_sse(client, f"/runs/{rep['run_id']}/events")
    assert ended and [m["type"] for m in msgs] == [e.type for e in store.events(rid)]
    assert calls == [rid]                       # the agent ran once; the replay called no model
    assert client.post("/runs/missing/replay").status_code == 404


def test_replay_refuses_unfinished_run():
    gate = threading.Event()
    client, _ = make(lambda r, u, emit: gate.wait(5))
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    assert client.post(f"/runs/{rid}/replay").status_code == 409
    gate.set()


def test_real_state_machine_through_the_store():
    """The teammate's run_machine + Context, with fake stages, writing into our store."""
    from itr.agent.context import Context
    from itr.agent.machine import run_machine
    from itr.agent.states import State
    from itr.models import Run
    from tests.fakes import FakeLLM, FakeSandbox

    def triage(ctx):
        ctx.model("nano", [{"role": "user", "content": "x"}], stage="triage")
        ctx.artifact("triage", {"kind": "feature"})
        ctx.run.outcome = "NEEDS_MAINTAINER"
        return State.DONE

    def run_fn(run_id, issue_url, emit):
        ctx = Context(run=Run(id=run_id, issue={"url": issue_url}), sandbox=FakeSandbox(),
                      llm_call=FakeLLM({"nano": ["ok"]}), emit_fn=emit)
        run_machine(ctx, stages={State.FETCH: lambda c: State.TRIAGE, State.TRIAGE: triage})

    client, store = make(run_fn)
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    wait_done(store, rid)
    s = client.get(f"/runs/{rid}").json()
    assert s["outcome"] == "NEEDS_MAINTAINER" and s["artifacts"]["triage"] == {"kind": "feature"}
    assert [x["stage"] for x in s["stages"]] == ["FETCH", "TRIAGE"] and s["tokens"] == {"nano": 200}


def test_make_sandbox_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "bogus")
    with pytest.raises(ValueError, match="docker or nebius"):
        api.make_sandbox()


def test_replay_only_mode_refuses_new_runs():
    client, _ = make(allow_new_runs=False)
    r = client.post("/runs", json={"issue_url": URL})
    assert r.status_code == 403 and "replay-only" in r.json()["detail"]
    assert client.get("/health").json() == {"ok": True, "new_runs": False, "mode": "background"}


def test_bundled_runs_are_loaded_at_startup(tmp_path):
    src = Store(":memory:")
    rid = src.create_run(URL, run_id="demo-1")
    fake_run(rid, URL, src.emitter(rid))
    (tmp_path / "demo-1.json").write_text(json.dumps(src.export_run(rid)))
    (tmp_path / "broken.json").write_text("{not json")
    store = Store(":memory:")
    client = TestClient(api.create_app(store=store, run_fn=fake_run, web_dir="/nonexistent", seed_dir=tmp_path))
    s = client.get("/runs/demo-1").json()
    assert s["outcome"] == "NEEDS_MAINTAINER" and s["tokens"] == {"nano": 1500}
    msgs, ended = read_sse(client, "/runs/demo-1/events")
    assert ended and len(msgs) == 7
    api.create_app(store=store, run_fn=fake_run, web_dir="/nonexistent", seed_dir=tmp_path)
    assert len(store.events("demo-1")) == 7          # loading twice does not duplicate


# ---- single-request mode (serverless hosts such as Vercel) ---------------------------------

def read_sse_events(client, path):
    """Like read_sse but also returns named events: [(event_name or None, data), ...]."""
    out, event = [], None
    with client.stream("GET", path) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                out.append((event, json.loads(line[6:])))
                if event in ("end", "fail"):
                    break
            elif line == "":
                event = None
    return out


def test_live_run_starts_and_streams_in_one_request():
    client, store = make()
    msgs = read_sse_events(client, f"/runs/live?issue_url={URL}")
    assert msgs[-1][0] == "end"
    data = [d for e, d in msgs if e is None]
    assert [d["type"] for d in data][-1] == "outcome" and len(data) == 7
    assert store.get_run(data[0]["run_id"])["outcome"] == "NEEDS_MAINTAINER"


def test_live_run_reports_refusals_as_a_fail_event():
    client, _ = make()
    (event, data), = read_sse_events(client, "/runs/live?issue_url=https://example.com/x")
    assert event == "fail" and data["status"] == 422
    client2, _ = make(allow_new_runs=False)
    (event, data), = read_sse_events(client2, f"/runs/live?issue_url={URL}")
    assert event == "fail" and data["status"] == 403


def test_live_run_is_closed_at_the_platform_deadline(monkeypatch):
    monkeypatch.setenv("INLINE_DEADLINE_S", "0.5")
    gate = threading.Event()

    def stuck(run_id, issue_url, emit):
        emit("stage_started", {"stage": "SETUP"})
        gate.wait(5)

    client, store = make(stuck)
    msgs = read_sse_events(client, f"/runs/live?issue_url={URL}")
    gate.set()
    outcome = [d for e, d in msgs if e is None and d["type"] == "outcome"][0]
    assert outcome["payload"]["outcome"] == "ERROR" and "time limit" in outcome["payload"]["reason"]


def test_replay_stream_writes_nothing():
    client, store = make()
    rid = client.post("/runs", json={"issue_url": URL}).json()["run_id"]
    wait_done(store, rid)
    before = len(store.list_runs())
    msgs = read_sse_events(client, f"/runs/{rid}/replay-stream?speed=0")
    data = [d for e, d in msgs if e is None]
    assert [d["type"] for d in data] == [e.type for e in store.events(rid)]
    assert data[-1]["replay_of"] == rid and data[4]["cost_usd"] > 0
    assert len(store.list_runs()) == before
    assert client.get("/runs/missing/replay-stream").status_code == 404


def test_inline_mode_is_reported_and_sets_a_time_budget(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "inline")
    monkeypatch.delenv("RUN_MAX_SECONDS", raising=False)
    client, _ = make()
    assert client.get("/health").json()["mode"] == "inline"
    assert api.config.get("RUN_MAX_SECONDS") == "270"
