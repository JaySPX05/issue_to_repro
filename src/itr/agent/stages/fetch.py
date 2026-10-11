"""FETCH: load the GitHub issue (no sandbox work yet; cloning happens in SETUP so that
non-bug issues never cost any sandbox time)."""
from __future__ import annotations

from itr import github
from itr.agent.context import Context
from itr.agent.states import State


def fetch(ctx: Context) -> State:
    issue = ctx.run.issue
    if "title" not in issue:                     # eval harness may pre-populate the issue + ref
        issue.update(github.fetch_issue(issue["url"]))
    ctx.artifact("issue", {k: issue.get(k) for k in ("url", "repo", "title", "ref")})
    return State.TRIAGE
