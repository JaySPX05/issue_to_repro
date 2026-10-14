"""Tests for itr.store (SQLite, standard library only)."""
from __future__ import annotations

import threading

import pytest

from itr.store import Store, call_cost


@pytest.fixture
def store():
    return Store(":memory:")


def test_create_and_get_run(store):
    rid = store.create_run("https://github.com/o/r/issues/1")
    run = store.get_run(rid)
    assert run["issue_url"].endswith("/issues/1")
    assert run["state"] == "queued" and run["outcome"] is None and run["cost"] == 0


def test_append_assigns_seq_per_run(store):
    a, b = store.create_run("u1"), store.create_run("u2")
    assert [store.append(a, "stage_started", {"stage": "FETCH"}).seq for _ in range(3)] == [1, 2, 3]
    assert store.append(b, "stage_started", {"stage": "FETCH"}).seq == 1


def test_events_after_seq_and_payload_roundtrip(store):
    rid = store.create_run("u")
    store.append(rid, "artifact", {"name": "repro_script", "content": "print('ä')"})
    store.append(rid, "command_run", {"cmd": "python repro.py", "exit_code": 1})
    evs = store.events(rid, after_seq=1)
    assert [e.type for e in evs] == ["command_run"]
    assert store.events(rid)[0].payload["content"] == "print('ä')"


def test_run_row_follows_events(store):
    rid = store.create_run("u")
    store.append(rid, "stage_started", {"stage": "REPRO"})
    assert store.get_run(rid)["state"] == "REPRO"
    store.append(rid, "model_call", {"tier": "super", "tokens_in": 1_000_000, "tokens_out": 0})
    store.append(rid, "outcome", {"outcome": "CONFIRMED"})
    run = store.get_run(rid)
    assert run["state"] == "done" and run["outcome"] == "CONFIRMED" and run["finished_at"]
    assert run["cost"] == pytest.approx(call_cost({"tier": "super", "tokens_in": 1_000_000}))


def test_summary_builds_stages_artifacts_tokens(store):
    rid = store.create_run("u")
    for t, p in [("stage_started", {"stage": "FETCH"}), ("artifact", {"name": "issue", "content": {"title": "x"}}),
                 ("stage_finished", {"stage": "FETCH", "next": "TRIAGE"}),
                 ("stage_started", {"stage": "TRIAGE"}), ("model_call", {"tier": "nano", "tokens_in": 10, "tokens_out": 5}),
                 ("stage_finished", {"stage": "TRIAGE", "error": "StageError: bad JSON"}),
                 ("outcome", {"outcome": "ERROR", "reason": "TRIAGE failed"})]:
        store.append(rid, t, p)
    s = store.summary(rid)
    assert s["stages"] == [{"stage": "FETCH", "status": "done"},
                           {"stage": "TRIAGE", "status": "failed", "error": "StageError: bad JSON"}]
    assert s["artifacts"] == {"issue": {"title": "x"}}
    assert s["tokens"] == {"nano": 15} and s["reason"] == "TRIAGE failed"
    assert store.summary("nope") is None


def test_mark_interrupted_closes_unfinished_runs(store):
    done, stuck = store.create_run("a"), store.create_run("b")
    store.append(done, "outcome", {"outcome": "CONFIRMED"})
    store.append(stuck, "stage_started", {"stage": "SETUP"})
    assert store.mark_interrupted() == [stuck]
    assert store.get_run(stuck)["outcome"] == "ERROR"
    assert store.get_run(done)["outcome"] == "CONFIRMED"


def test_concurrent_appends_keep_seq_unique(store):
    rid = store.create_run("u")
    emit = store.emitter(rid)
    threads = [threading.Thread(target=lambda: [emit("command_run", {"i": i}) for i in range(50)])
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e.seq for e in store.events(rid)] == list(range(1, 201))


def test_file_store_persists(tmp_path):
    path = tmp_path / "sub" / "itr.db"
    rid = Store(path).create_run("u")
    Store(path).append(rid, "outcome", {"outcome": "NOT_REPRODUCED"})
    assert Store(path).get_run(rid)["outcome"] == "NOT_REPRODUCED"
