"""REPORT (nano): turn the evidence into a comment a maintainer can paste into the issue.

The outcome is NOT decided here. VERIFY already set NEEDS_MAINTAINER if it could not vouch for a patch;
a run that reaches REPORT without an outcome has passed the gate and becomes CONFIRMED. The model only
writes prose. If it fails or returns junk the run keeps its outcome and gets a plain-code comment instead
(a run must never lose a verified result because of a formatting problem)."""
from __future__ import annotations

import json

from itr.agent.context import Context, StageError, clip
from itr.agent.states import State

REPORT_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "maxLength": 140}, "comment": {"type": "string", "maxLength": 4000}},
    "required": ["title", "comment"],
    "additionalProperties": False,
}


def tidy(text: str) -> str:
    """Models sometimes write a whole comment on one line with a literal `\\n` or PowerShell-style backtick-n
    where line breaks belong (seen with the nano model). Real markdown has line breaks."""
    if text.count("\n") < 3:
        text = text.replace("\\n", "\n").replace("`n", "\n")
    return text.replace("\u2702\ufe0f", "").strip()


def evidence(ctx: Context, outcome: str) -> dict:
    run, fx = ctx.run, ctx.run.fix or {}
    repro, verdict = run.repro or {}, run.verdict or {}
    data = {"outcome": outcome, "issue_title": run.issue.get("title", ""),
            "reproduction": {"script": clip(repro.get("script", ""), 2500),
                             "output": clip(repro.get("last_output", ""), 1500)},
            "reported_symptom": verdict.get("reported_symptom"), "observed_symptom": verdict.get("observed_symptom")}
    if fx.get("root_cause"):
        data["root_cause"] = fx["root_cause"]
    if outcome == "CONFIRMED":
        data.update({"patch": clip(fx.get("patch", ""), 3000), "test_path": fx.get("test_path"),
                     "test_code": clip(fx.get("regression_test", ""), 2000),
                     "checks": {"test_fails_before_patch": True, "test_passes_after_patch": True,
                                "reproduction_passes_after_patch": True,
                                "existing_tests_rerun": bool((fx.get("suite") or {}).get("ran"))}})
    return data


def plain_comment(data: dict) -> dict:
    """Deterministic fallback, no model involved."""
    intro = ("I reproduced this issue and drafted a fix with a regression test; the checks are below."
             if data["outcome"] == "CONFIRMED" else "I reproduced this issue but could not verify a patch.")
    lines = [intro, "", "### Reproduction", "```python",
             data["reproduction"]["script"], "```", "", "```", data["reproduction"]["output"], "```"]
    if data["outcome"] == "CONFIRMED":
        lines += ["", "### Proposed patch", "```diff", data.get("patch", ""), "```", "",
                  f"### Regression test (`{data.get('test_path')}`)", "```python", data.get("test_code", ""), "```"]
    else:
        lines += ["", "No patch could be verified automatically."]
    return {"title": f"Reproduced: {data['issue_title']}"[:90], "comment": "\n".join(lines)}


def report(ctx: Context) -> State:
    outcome = ctx.run.outcome or "CONFIRMED"
    data = evidence(ctx, outcome)
    try:
        messages = [{"role": "system", "content": ctx.prompt("report")},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]
        out = ctx.model_json("nano", messages, REPORT_SCHEMA, stage="report", retries=0).data
        out = {"title": out["title"].strip(), "comment": tidy(out["comment"])}
        generated = True
    except StageError:
        out, generated = plain_comment(data), False
    ctx.run.outcome = outcome                # type: ignore[assignment]
    ctx.artifact("report", {**out, "generated_by_model": generated})
    return State.DONE
