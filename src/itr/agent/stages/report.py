"""REPORT (nano): the comment a maintainer can paste into the issue.

The outcome is NOT decided here. VERIFY already set NEEDS_MAINTAINER if it could not vouch for a patch;
a run that reaches REPORT without an outcome has passed the gate and becomes CONFIRMED.

The model never copies code (D55). Live runs showed the nano model retyping the verified diff and getting
it wrong, so a maintainer would have read a patch that is not the one that was tested. Code assembles the
whole comment, pasting the script, output, diff and test verbatim, and the model writes only two short
prose fields (a title and a summary). If the model fails or returns junk, a plain sentence replaces them:
a verified result is never lost to a formatting problem."""
from __future__ import annotations

import json
import re

from itr.agent.context import Context, StageError, clip
from itr.agent.states import State

REPORT_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "maxLength": 140}, "summary": {"type": "string", "maxLength": 700}},
    "required": ["title", "summary"],
    "additionalProperties": False,
}


def tidy(text: str) -> str:
    """Models sometimes write prose on one line with a literal `\\n` or PowerShell-style backtick-n where
    line breaks belong (seen with the nano model)."""
    if text.count("\n") < 1:
        text = text.replace("\\n", "\n").replace("`n", "\n")
    return text.replace("✂️", "").strip()


UNTRUE_CLAIM = re.compile(r"\b(merged|released|deployed|committed|pushed|shipped|has been applied|was applied)\b", re.I)


def drop_untrue_claims(summary: str, fallback: str) -> str:
    """The agent only PROPOSES a patch. A summary sentence that says it was merged, released, applied or
    committed is not true of anything we did, so that sentence is removed (seen on tabulate-399: "The fix has
    been merged")."""
    parts = re.split(r"(?<=[.!?])\s+", summary.strip())
    kept = [p for p in parts if not UNTRUE_CLAIM.search(p)]
    return " ".join(kept) if kept else fallback


def evidence(ctx: Context, outcome: str) -> dict:
    """Everything the comment needs, as plain data. The model sees only the short facts, not the code."""
    run, fx = ctx.run, ctx.run.fix or {}
    repro, verdict = run.repro or {}, run.verdict or {}
    data = {"outcome": outcome, "issue_title": run.issue.get("title", ""),
            "reported_symptom": verdict.get("reported_symptom"), "observed_symptom": verdict.get("observed_symptom"),
            "root_cause": fx.get("root_cause", ""),
            "reproduction": {"script": repro.get("script", ""), "output": repro.get("last_output", "")}}
    if outcome != "CONFIRMED":
        best = [h for h in fx.get("history", []) if h.get("test_agrees") and h.get("patch")]
        if best:
            h = best[-1]
            data["candidate"] = {"patch": h["patch"], "test_path": h.get("test_path"),
                                 "test_code": h.get("test_code", ""), "failure": h.get("failure", "")}
    if outcome == "CONFIRMED":
        data.update({"patch": fx.get("patch", ""), "test_path": fx.get("test_path"),
                     "test_code": fx.get("regression_test", ""),
                     "failing_before": fx.get("failing_before", []),
                     "suite_ran": bool((fx.get("suite") or {}).get("ran"))})
    return data


def compose(data: dict, title: str, summary: str) -> dict:
    """Assemble the markdown comment. Code, output and diff are inserted verbatim, never by a model."""
    confirmed = data["outcome"] == "CONFIRMED"
    lines = [summary.strip(), "", "### Reproduction", "```python", data["reproduction"]["script"].rstrip(), "```", "",
             "Output on the original code:", "```", clip(data["reproduction"]["output"].strip(), 1800), "```"]
    if data.get("root_cause"):
        lines += ["", "### Cause" if confirmed else "### Suspected cause (unverified)", data["root_cause"].strip()]
    if confirmed:
        lines += ["", "### Proposed patch", "```diff", data["patch"].rstrip(), "```",
                  "", f"### Regression test (`{data['test_path']}`)", "```python", data["test_code"].rstrip(), "```",
                  "", "### Checks run by the agent",
                  "- the new test fails on the original code" + (
                      f" ({', '.join(data['failing_before'][:4])})" if data.get("failing_before") else ""),
                  "- the new test passes on the patched code",
                  "- the reproduction script above exits 0 on the patched code",
                  "- the project's own tests were re-run with no new failures" if data.get("suite_ran")
                  else "- the project's own tests could not be re-run in the sandbox"]
    else:
        lines += ["", "No patch could be verified automatically."]
        cand = data.get("candidate")
        if cand:
            lines += ["", "### Closest candidate (NOT verified)",
                      "This patch passes its own new test, and that test fails on the original code, "
                      "but a later check failed, so treat it as a starting point only. Failed check: "
                      + clip(re.sub(r"\s*Output:?$", "", cand["failure"].strip().splitlines()[0]
                                    if cand["failure"].strip() else "unknown"), 300) + ".",
                      "```diff", cand["patch"].rstrip(), "```",
                      "", f"Its test (`{cand['test_path']}`):", "```python", cand["test_code"].rstrip(), "```"]
    return {"title": title.strip()[:140], "comment": "\n".join(lines)}


def fallback_prose(data: dict) -> tuple[str, str]:
    if data["outcome"] == "CONFIRMED":
        return (f"Reproduced: {data['issue_title']}"[:140],
                "I reproduced this issue and drafted a fix with a regression test; the checks are below.")
    return (f"Reproduced: {data['issue_title']}"[:140], "I reproduced this issue but could not verify a patch.")


def report(ctx: Context) -> State:
    outcome = ctx.run.outcome or "CONFIRMED"
    data = evidence(ctx, outcome)
    short = {k: data[k] for k in ("outcome", "issue_title", "reported_symptom", "observed_symptom", "root_cause")}
    try:
        messages = [{"role": "system", "content": ctx.prompt("report")},
                    {"role": "user", "content": json.dumps(short, ensure_ascii=False)}]
        prose = ctx.model_json("nano", messages, REPORT_SCHEMA, stage="report", retries=0).data
        title, summary, generated = tidy(prose["title"]), tidy(prose["summary"]), True
        summary = drop_untrue_claims(summary, fallback_prose(data)[1])
        if not title or not summary:
            raise StageError("empty prose")
    except StageError:
        (title, summary), generated = fallback_prose(data), False
    ctx.run.outcome = outcome                # type: ignore[assignment]
    ctx.artifact("report", {**compose(data, title, summary), "generated_by_model": generated})
    return State.DONE
