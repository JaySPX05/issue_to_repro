"""TRIAGE (nano): classify the issue and extract what a reproduction needs.

Transitions: kind == "bug" -> SETUP; anything else -> NEEDS_MAINTAINER (DONE)."""
from __future__ import annotations

import json

from itr.agent.context import Context, clip
from itr.agent.states import State

KINDS = ["bug", "feature", "question", "duplicate", "unclear"]

# Property order matters: the model writes the evidence (steps, expected, actual) BEFORE it chooses `kind`,
# so the label is conditioned on what it just wrote. Live run on tabulate-241 (D61): kind came first and was
# "feature" while the same reply's `actual` said "returns an invalid markdown table", which is a bug.
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "items": {"type": "string"}},
        "expected": {"type": "string"},
        "actual": {"type": "string"},
        "claimed_version": {"type": ["string", "null"]},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "kind": {"enum": KINDS},
    },
    "required": ["steps", "expected", "actual", "claimed_version", "missing_info", "kind"],
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
