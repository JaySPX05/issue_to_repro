"""TRIAGE (nano): classify the issue and extract what a reproduction needs.

Transitions: kind == "bug" -> SETUP; anything else -> NEEDS_MAINTAINER (DONE)."""
from __future__ import annotations

import json

from itr.agent.context import Context, clip
from itr.agent.states import State

KINDS = ["bug", "feature", "question", "duplicate", "unclear"]

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"enum": KINDS},
        "claimed_version": {"type": ["string", "null"]},
        "steps": {"type": "array", "items": {"type": "string"}},
        "expected": {"type": "string"},
        "actual": {"type": "string"},
        "missing_info": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind", "claimed_version", "steps", "expected", "actual", "missing_info"],
    "additionalProperties": False,
}


def _comment_text(c) -> str:
    return c.get("body", "") if isinstance(c, dict) else str(c)


def triage(ctx: Context) -> State:
    issue = ctx.run.issue
    payload = {
        "title": issue.get("title", ""),
        "body": clip(issue.get("body", ""), 6000),
        "comments": [clip(_comment_text(c), 1500) for c in issue.get("comments", [])[:10]],
    }
    messages = [
        {"role": "system", "content": ctx.prompt("triage")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    resp = ctx.model_json("nano", messages, TRIAGE_SCHEMA, stage="triage")
    ctx.run.triage = resp.data
    ctx.artifact("triage", resp.data)

    if resp.data["kind"] == "bug":
        return State.SETUP
    ctx.run.outcome = "NEEDS_MAINTAINER"
    return State.DONE
