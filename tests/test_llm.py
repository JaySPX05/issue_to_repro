"""Tests for itr.llm against a local fake Token Factory server (no API key or network needed)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from itr import llm


class FakeServer:
    """Serves /chat/completions; `replies` is a queue of (status, body) tuples."""

    def __init__(self) -> None:
        self.replies: list[tuple[int, dict | str]] = []
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "body": json.loads(self.rfile.read(n))})
                status, body = outer.replies.pop(0)
                payload = (json.dumps(body) if isinstance(body, dict) else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


def ok(content: str, model: str = "m-nano", finish: str = "stop") -> tuple[int, dict]:
    return 200, {"model": model,
                 "choices": [{"message": {"content": content, "reasoning_content": "thinking..."},
                              "finish_reason": finish}],
                 "usage": {"prompt_tokens": 11, "completion_tokens": 22}}


@pytest.fixture
def server(monkeypatch):
    s = FakeServer()
    monkeypatch.setattr(llm, "_env_loaded", True)
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    monkeypatch.setenv("NEBIUS_BASE_URL", s.url)
    monkeypatch.setenv("MODEL_NANO", "m-nano")
    monkeypatch.setenv("MODEL_SUPER", "m-super")
    monkeypatch.setenv("MODEL_ULTRA", "m-ultra")
    monkeypatch.setenv("LLM_RETRIES", "1")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    yield s
    s.httpd.shutdown()


SCHEMA = {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}


def test_text_call_routes_tier_and_reports_usage(server):
    server.replies.append(ok("pong", model="m-super"))
    r = llm.call("super", [{"role": "user", "content": "ping"}])
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["auth"] == "Bearer test-key"
    assert req["body"]["model"] == "m-super"
    assert "response_format" not in req["body"]
    assert (r.data, r.tokens_in, r.tokens_out, r.tier, r.model) == ("pong", 11, 22, "super", "m-super")
    assert r.latency_ms >= 0


def test_schema_call_uses_strict_json_schema_and_parses(server):
    server.replies.append(ok('```json\n{"kind": "bug"}\n```'))
    r = llm.call("nano", [{"role": "user", "content": "x"}], SCHEMA)
    rf = server.requests[0]["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == SCHEMA
    assert r.data == {"kind": "bug"}


def test_unparseable_schema_reply_returns_raw_text(server):
    server.replies.append(ok("", finish="length"))
    r = llm.call("nano", [{"role": "user", "content": "x"}], SCHEMA)
    assert r.data == ""          # Context.model_json will retry with the error fed back


def test_retries_on_429_then_succeeds(server):
    server.replies += [(429, {"error": "slow down"}), ok("pong")]
    assert llm.call("nano", [{"role": "user", "content": "x"}]).data == "pong"
    assert len(server.requests) == 2


def test_non_retryable_error_raises(server):
    server.replies.append((401, {"error": "bad key"}))
    with pytest.raises(llm.LLMError, match="HTTP 401"):
        llm.call("nano", [{"role": "user", "content": "x"}])
    assert len(server.requests) == 1


def test_max_tokens_default_and_override(server, monkeypatch):
    server.replies += [ok("a"), ok("b")]
    llm.call("ultra", [{"role": "user", "content": "x"}])
    monkeypatch.setenv("LLM_MAX_TOKENS_ULTRA", "999")
    llm.call("ultra", [{"role": "user", "content": "x"}])
    assert [q["body"]["max_tokens"] for q in server.requests] == [16000, 999]


def test_missing_model_config_raises(server, monkeypatch):
    monkeypatch.delenv("MODEL_NANO")
    with pytest.raises(llm.LLMError, match="MODEL_NANO"):
        llm.call("nano", [{"role": "user", "content": "x"}])


def test_works_through_agent_context(server):
    """End to end with the teammate's Context: schema validation + event emission."""
    from tests.fakes import make_ctx
    from itr.agent.budget import init_budget
    server.replies.append(ok('{"kind": "bug"}'))
    ctx, events = make_ctx(llm=llm.call)
    init_budget(ctx.run)
    r = ctx.model_json("nano", [{"role": "user", "content": "x"}], SCHEMA, stage="triage")
    assert r.data == {"kind": "bug"}
    calls = [p for t, p in events.events if t == "model_call"]
    assert calls and calls[0]["tier"] == "nano" and calls[0]["tokens_in"] == 11