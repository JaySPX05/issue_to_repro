"""In-memory stand-ins so agent code is testable without Docker, Nebius, or network."""
from __future__ import annotations

from itr.agent.context import Context
from itr.llm import LLMResponse
from itr.models import Run
from itr.sandbox.base import ExecResult


class FakeSandbox:
    """Scripted sandbox. `responses` maps a substring of the command to an ExecResult."""

    def __init__(self, responses: dict[str, ExecResult] | None = None):
        self.responses = responses or {}
        self.commands: list[str] = []
        self.files: dict[str, str] = {}
        self.created = False
        self.destroyed = False

    def create(self, image=None):
        self.created = True

    def exec(self, cmd, timeout=120):
        self.commands.append(cmd)
        for needle, res in self.responses.items():
            if needle in cmd:
                return res
        return ExecResult("", "", 0)

    def write_file(self, path, content):
        self.files[path] = content

    def read_file(self, path):
        return self.files[path]

    def snapshot(self):
        return "snap-1"

    def destroy(self):
        self.destroyed = True


class FakeLLM:
    """Queue of scripted replies per tier. A reply is a dict (parsed JSON) or a str."""

    def __init__(self, replies: dict[str, list] | None = None, tokens: int = 100):
        self.replies = {k: list(v) for k, v in (replies or {}).items()}
        self.calls: list[tuple[str, list[dict], dict | None]] = []
        self.tokens = tokens

    def __call__(self, tier, messages, schema=None):
        self.calls.append((tier, messages, schema))
        queue = self.replies.get(tier, [])
        if not queue:
            raise AssertionError(f"FakeLLM has no scripted reply left for tier {tier!r}")
        return LLMResponse(queue.pop(0), self.tokens, self.tokens, 5, tier, f"fake-{tier}")


class EventLog:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_type, payload):
        self.events.append((event_type, payload))

    def types(self):
        return [t for t, _ in self.events]

    def stages_started(self):
        return [p["stage"] for t, p in self.events if t == "stage_started"]


def make_ctx(issue=None, llm=None, sandbox=None):
    issue = issue if issue is not None else {
        "url": "https://github.com/o/r/issues/1", "repo": "o/r", "number": 1,
        "title": "Crash on empty list", "body": "foo([]) raises IndexError", "comments": [], "ref": "main",
    }
    log = EventLog()
    ctx = Context(run=Run(id="r1", issue=issue), sandbox=sandbox or FakeSandbox(),
                  llm_call=llm or FakeLLM(), emit_fn=log)
    return ctx, log


BUG = {"kind": "bug", "claimed_version": "1.2.0", "steps": ["call foo([])"],
       "expected": "returns None", "actual": "IndexError", "missing_info": []}
