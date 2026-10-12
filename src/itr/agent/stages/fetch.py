"""FETCH: load the GitHub issue (no sandbox work yet; cloning happens in SETUP so that
non-bug issues never cost any sandbox time)."""
from __future__ import annotations

from itr import github
from itr.agent.context import Context
from itr.agent.states import State

# Fields the caller (the eval harness) may set up front. Fetched data must never overwrite them:
# `ref` is the commit just before a known fix, and `as_of` limits which comments are visible.
PRESET_KEYS = ("ref", "ref_pinned", "as_of")


def fetch(ctx: Context) -> State:
    issue = ctx.run.issue
    if "title" not in issue:                     # eval cases may pre-populate the whole issue
        preset = {k: issue[k] for k in PRESET_KEYS if k in issue}
        issue.update(github.fetch_issue(issue["url"], as_of=issue.get("as_of")))
        issue.update(preset)
    ctx.artifact("issue", {k: issue.get(k) for k in ("url", "repo", "title", "ref")})
    return State.TRIAGE
