"""SQLite store for runs and their events. The UI renders everything from the events table.

Schema (as agreed):
    runs   (id, issue_url, state, outcome, cost, created_at, finished_at)
    events (run_id, seq, type, payload JSON, ts)

`state` is the stage currently running ("FETCH", "SETUP", ...), "queued" before the first
stage, and "done" once an `outcome` event arrives. The store assigns `seq` (1, 2, 3 ... per run),
so the agent only ever calls `emit(type, payload)`.

Standard library only. One connection per Store guarded by a lock, so the agent thread and the
API's request handlers can share it safely.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator

from itr.models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    issue_url   TEXT NOT NULL,
    state       TEXT NOT NULL,
    outcome     TEXT,
    cost        REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    run_id  TEXT NOT NULL REFERENCES runs(id),
    seq     INTEGER NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts      REAL NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

# USD per 1M tokens (input, output). Read from the agent's budget table when it exists, so the
# price list lives in one place; the fallback only keeps the store usable on its own.
try:
    from itr.agent.budget import PRICES as _PRICES  # type: ignore[attr-defined]
except ImportError:                                   # pragma: no cover
    _PRICES = {}
FALLBACK_PRICES = {"nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (1.00, 3.00)}


def call_cost(payload: dict) -> float:
    """Dollar cost of one `model_call` event payload."""
    prices = _PRICES or FALLBACK_PRICES
    p_in, p_out = prices.get(payload.get("tier", ""), prices.get("ultra", (1.0, 3.0)))
    return (int(payload.get("tokens_in") or 0) * p_in + int(payload.get("tokens_out") or 0) * p_out) / 1e6


class Store:
    """Runs and events in one SQLite file. Use ":memory:" for tests."""

    def __init__(self, path: str | Path = "itr.db") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    # ---- runs ----------------------------------------------------------------
    def create_run(self, issue_url: str, run_id: str | None = None) -> str:
        """Insert a new run in state "queued" and return its id."""
        run_id = run_id or uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, issue_url, state, created_at) VALUES (?, ?, 'queued', ?)",
                (run_id, issue_url, time.time()))
        return run_id

    def get_run(self, run_id: str) -> dict | None:
        """The run row as a dict, or None."""
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        """Most recent runs first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def finish_run(self, run_id: str, outcome: str, cost: float | None = None) -> None:
        """Mark a run done. Used by the runner as a backstop when no `outcome` event arrived."""
        with self._lock:
            self._db.execute(
                "UPDATE runs SET state = 'done', outcome = ?, finished_at = ?, "
                "cost = COALESCE(?, cost) WHERE id = ?",
                (outcome, time.time(), cost, run_id))

    def mark_interrupted(self) -> list[str]:
        """On startup: runs left unfinished by a crash/restart get an ERROR outcome event."""
        with self._lock:
            ids = [r["id"] for r in self._db.execute(
                "SELECT id FROM runs WHERE state != 'done'").fetchall()]
        for rid in ids:
            self.append(rid, "outcome", {"outcome": "ERROR", "reason": "server restarted during the run"})
        return ids

    # ---- events --------------------------------------------------------------
    def append(self, run_id: str, type: str, payload: dict | None = None,
               ts: float | None = None) -> Event:
        """Store one event with the next seq for this run and update the run row to match."""
        payload = payload or {}
        ts = time.time() if ts is None else ts
        body = json.dumps(payload, default=str)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                seq = self._db.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?",
                    (run_id,)).fetchone()[0]
                self._db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                                 (run_id, seq, type, body, ts))
                if type == "stage_started":
                    self._db.execute("UPDATE runs SET state = ? WHERE id = ?",
                                     (str(payload.get("stage", "running")), run_id))
                elif type == "model_call":
                    self._db.execute("UPDATE runs SET cost = cost + ? WHERE id = ?",
                                     (call_cost(payload), run_id))
                elif type == "outcome":
                    self._db.execute(
                        "UPDATE runs SET state = 'done', outcome = ?, finished_at = ? WHERE id = ?",
                        (payload.get("outcome"), ts, run_id))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return Event(run_id=run_id, seq=seq, type=type, payload=payload, ts=ts)  # type: ignore[arg-type]

    def emitter(self, run_id: str) -> Callable[[str, dict], None]:
        """The `emit_fn` to hand to the agent's Context for this run."""
        def emit(type: str, payload: dict) -> None:
            self.append(run_id, type, payload)
        return emit

    def events(self, run_id: str, after_seq: int = 0) -> list[Event]:
        """Events for a run with seq > after_seq, in order."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, after_seq)).fetchall()
        return [Event(run_id=r["run_id"], seq=r["seq"], type=r["type"],
                      payload=json.loads(r["payload"]), ts=r["ts"]) for r in rows]

    def iter_events(self, run_id: str) -> Iterator[Event]:
        yield from self.events(run_id)

    # ---- export / import (bundled demo runs) ---------------------------------
    def export_run(self, run_id: str) -> dict:
        """The run row plus all its events, as plain JSON-able data."""
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return {"run": run, "events": [{"type": e.type, "payload": e.payload, "ts": e.ts}
                                       for e in self.events(run_id)]}

    def import_run(self, data: dict) -> str | None:
        """Load an exported run under its original id. Returns the id, or None if it already exists."""
        run = data["run"]
        if self.get_run(run["id"]) is not None:
            return None
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, issue_url, state, created_at) VALUES (?, ?, 'queued', ?)",
                (run["id"], run["issue_url"], run.get("created_at") or time.time()))
        for ev in data["events"]:
            self.append(run["id"], ev["type"], ev["payload"], ts=ev.get("ts"))
        if self.get_run(run["id"])["state"] != "done":           # exported mid-run: close it cleanly
            self.finish_run(run["id"], run.get("outcome") or "ERROR")
        return run["id"]

    def import_dir(self, folder: str | Path) -> list[str]:
        """Import every *.json export in `folder` that is not loaded yet. Bad files are skipped."""
        loaded: list[str] = []
        for path in sorted(Path(folder).glob("*.json")):
            try:
                rid = self.import_run(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if rid:
                loaded.append(rid)
        return loaded

    # ---- derived view --------------------------------------------------------
    def summary(self, run_id: str) -> dict | None:
        """Final state for GET /runs/{id}: the run row plus what the events say.

        stages     list of {stage, status, error?} in the order they ran
        artifacts  latest content per artifact name (issue, repro_script, diff, report, ...)
        tokens     totals per tier from model_call events
        reason     why the run ended, from the outcome event
        """
        run = self.get_run(run_id)
        if run is None:
            return None
        stages: list[dict] = []
        artifacts: dict = {}
        tokens: dict[str, int] = {}
        reason = None
        for ev in self.events(run_id):
            p = ev.payload
            if ev.type == "stage_started":
                stages.append({"stage": p.get("stage"), "status": "running"})
            elif ev.type == "stage_finished" and stages:
                stages[-1]["status"] = "failed" if p.get("error") else "done"
                if p.get("error"):
                    stages[-1]["error"] = p["error"]
            elif ev.type == "artifact" and p.get("name"):
                artifacts[p["name"]] = p.get("content")
            elif ev.type == "model_call":
                t = p.get("tier", "?")
                tokens[t] = tokens.get(t, 0) + int(p.get("tokens_in") or 0) + int(p.get("tokens_out") or 0)
            elif ev.type == "outcome":
                reason = p.get("reason")
        return {**run, "stages": stages, "artifacts": artifacts, "tokens": tokens, "reason": reason}
