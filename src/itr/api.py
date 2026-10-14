"""HTTP API: start runs, stream their events live (server-sent events), replay stored runs.

    POST /runs                 {"issue_url": "..."}  -> {"run_id": "..."}
    GET  /runs/{id}/events     SSE: every stored event, then live ones, then `event: end`
    GET  /runs/{id}            final state: run row + stages, artifacts, tokens, reason
    POST /runs/{id}/replay     ?speed=4  -> {"run_id": new id}; re-streams a stored run, no model calls
    GET  /runs                 recent runs (for the UI's history list)
    GET  /health               liveness check
    /                          the static UI in web/ (when the folder exists)

Each SSE message is `id: <seq>` + `data: <json>` where the JSON is the event
(run_id, seq, type, payload, ts) plus `cost_usd`, the run's running total so far.
A browser that reconnects sends Last-Event-ID and resumes after that seq.

Configuration (environment or .env, see .env.example):
    ITR_DB_PATH          default data/itr.db
    SANDBOX_BACKEND      docker (default) | nebius
    MAX_CONCURRENT_RUNS  default 2; further POST /runs get HTTP 429 (protects credits on a public demo)
    WEB_DIR              default <repo>/web

Run locally:  python -m itr.api            (http://127.0.0.1:8000)
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from itr import config
from itr.store import Store, call_cost

log = logging.getLogger("itr.api")

ISSUE_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/issues/\d+/?$")
POLL_S = 0.25            # how often the SSE stream checks the store for new events
PING_S = 15.0            # keep-alive comment so proxies do not close an idle stream
MAX_REPLAY_GAP_S = 1.5   # a replay never pauses longer than this between two events

RunFn = Callable[[str, str, Callable[[str, dict], None]], None]


class RunRequest(BaseModel):
    issue_url: str


def make_sandbox():
    """Build the sandbox chosen by SANDBOX_BACKEND."""
    backend = (config.get("SANDBOX_BACKEND", "docker") or "docker").lower()
    if backend == "docker":
        from itr.sandbox.local_docker import LocalDockerSandbox
        return LocalDockerSandbox()
    if backend == "nebius":
        from itr.sandbox.nebius import NebiusSandbox   # needs contree-sdk
        return NebiusSandbox()
    raise ValueError(f"SANDBOX_BACKEND must be docker or nebius, not {backend!r}")


def default_run_fn(run_id: str, issue_url: str, emit: Callable[[str, dict], None]) -> None:
    """Run the real agent: Context(real sandbox, real LLM, store emitter) -> run_machine."""
    from itr import llm
    from itr.agent.context import Context
    from itr.agent.machine import run_machine
    from itr.models import Run
    ctx = Context(run=Run(id=run_id, issue={"url": issue_url}), sandbox=make_sandbox(),
                  llm_call=llm.call, emit_fn=emit)
    run_machine(ctx)


def _sse(data: dict, seq: int | None = None, event: str | None = None) -> str:
    head = (f"id: {seq}\n" if seq is not None else "") + (f"event: {event}\n" if event else "")
    return f"{head}data: {json.dumps(data, default=str)}\n\n"


def create_app(store: Store | None = None, run_fn: RunFn | None = None,
               max_concurrent: int | None = None, web_dir: str | Path | None = None) -> FastAPI:
    """Build the app. Tests pass an in-memory Store and a fake run_fn; production uses defaults."""
    config.load_env()
    store = store or Store(config.get("ITR_DB_PATH", "data/itr.db"))
    run_fn = run_fn or default_run_fn
    limit = max_concurrent or int(config.get("MAX_CONCURRENT_RUNS", "2") or 2)
    slots = threading.BoundedSemaphore(limit)
    interrupted = store.mark_interrupted()
    if interrupted:
        log.warning("marked %d unfinished run(s) as ERROR after restart", len(interrupted))

    app = FastAPI(title="Issue-to-Repro", version="0.1.0")
    app.state.store = store

    # ---- background work ----------------------------------------------------------
    def execute(run_id: str, issue_url: str) -> None:
        try:
            run_fn(run_id, issue_url, store.emitter(run_id))
        except Exception as e:                                   # never leave a run hanging
            log.exception("run %s crashed outside the state machine", run_id)
            reason = f"{type(e).__name__}: {e}"
        else:
            reason = "run ended without an outcome"
        finally:
            slots.release()
        run = store.get_run(run_id)
        if run and run["state"] != "done":
            store.append(run_id, "outcome", {"outcome": "ERROR", "reason": reason})

    def replay(source_id: str, new_id: str, speed: float) -> None:
        prev_ts = None
        for ev in store.events(source_id):
            if speed > 0 and prev_ts is not None:
                time.sleep(min(max(ev.ts - prev_ts, 0) / speed, MAX_REPLAY_GAP_S))
            prev_ts = ev.ts
            store.append(new_id, ev.type, ev.payload)

    # ---- routes -----------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/runs")
    def start_run(req: RunRequest) -> dict:
        url = req.issue_url.strip()
        if not ISSUE_URL.match(url):
            raise HTTPException(422, "issue_url must look like https://github.com/OWNER/REPO/issues/123")
        if not slots.acquire(blocking=False):
            raise HTTPException(429, f"{limit} run(s) already in progress; try again shortly")
        run_id = store.create_run(url)
        threading.Thread(target=execute, args=(run_id, url), daemon=True, name=f"run-{run_id}").start()
        return {"run_id": run_id}

    @app.get("/runs")
    def list_runs(limit: int = 20) -> list[dict]:
        return store.list_runs(min(max(limit, 1), 200))

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        summary = store.summary(run_id)
        if summary is None:
            raise HTTPException(404, "run not found")
        return summary

    @app.post("/runs/{run_id}/replay")
    def replay_run(run_id: str, speed: float = 4.0) -> dict:
        src = store.get_run(run_id)
        if src is None:
            raise HTTPException(404, "run not found")
        if src["state"] != "done":
            raise HTTPException(409, "run is still in progress; replay it once it has finished")
        new_id = store.create_run(src["issue_url"])
        threading.Thread(target=replay, args=(run_id, new_id, speed), daemon=True).start()
        return {"run_id": new_id, "replay_of": run_id}

    @app.get("/runs/{run_id}/events")
    async def stream(run_id: str, request: Request) -> StreamingResponse:
        if store.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        try:
            last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)
        except ValueError:
            last = 0

        async def gen():
            seq, cost, idle = last, 0.0, 0.0
            for ev in store.events(run_id)[:last]:               # cost so far, when resuming
                if ev.type == "model_call":
                    cost += call_cost(ev.payload)
            while True:
                new = await asyncio.to_thread(store.events, run_id, seq)
                for ev in new:
                    seq = ev.seq
                    if ev.type == "model_call":
                        cost += call_cost(ev.payload)
                    yield _sse({**dataclasses.asdict(ev), "cost_usd": round(cost, 6)}, seq=ev.seq)
                    if ev.type == "outcome":
                        yield _sse({"run_id": run_id}, event="end")
                        return
                if new:
                    idle = 0.0
                    continue
                run = store.get_run(run_id)
                if run is None or run["state"] == "done":       # finished with no outcome event
                    yield _sse({"run_id": run_id}, event="end")
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(POLL_S)
                idle += POLL_S
                if idle >= PING_S:
                    idle = 0.0
                    yield ": ping\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- static UI (mounted last so the API routes win) -------------------------------
    web = Path(web_dir or config.get("WEB_DIR") or Path(__file__).resolve().parents[2] / "web")
    if web.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")

    return app


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
