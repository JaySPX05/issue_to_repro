"""VERDICT (ultra): did the script fail for the reason the issue describes?

REPRO only proves that a script FAILED. This stage judges whether it failed for the RIGHT reason.
It is a separate model call from REPRO and uses the strongest tier, because the cost of a wrong
"yes" is a confident false report on a maintainer's issue tracker (see decisions D5).

The model answers factual questions; CODE makes the decision (decisions D43):
  failure_matches_symptom  does the observed failure show the symptom the reporter described?
  would_pass_if_fixed      if the library behaved exactly as the reporter expects, would this script
                           exit 0? (False for a script that is wrong in itself, e.g. one that calls
                           something with a missing argument, or fails on a platform the bug is not on.)
  cannot_reproduce_in_sandbox  the bug depends on something this sandbox cannot provide (another OS,
                           hardware, a service). With confidence >= 0.8 the run stops at once as
                           NOT_REPRODUCED instead of burning attempts.
  confidence               0..1
The verdict is "matches" only if BOTH booleans are true and confidence >= CONFIDENCE_MIN. A model that
says "yes" while contradicting its own booleans is overruled.

matches -> FIX.   rejected and repro attempts remain -> REPRO (with the judge's feedback).
rejected and no attempts left -> outcome NOT_REPRODUCED, DONE.

run.verdict = {matches_issue, reasoning, confidence, failure_matches_symptom, would_pass_if_fixed,
               reported_symptom, observed_symptom, rejected_because}
`reasoning` is what REPRO sees as `judge_feedback` after a rejection."""
from __future__ import annotations

import json

from itr.agent.budget import attempts_left
from itr.agent.context import Context, clip
from itr.agent.states import State

CONFIDENCE_MIN = 0.6
GIVE_UP_CONFIDENCE = 0.8      # to stop early because the sandbox cannot show this bug (a stricter bar than accepting)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "reported_symptom": {"type": "string"},
        "observed_symptom": {"type": "string"},
        "failure_matches_symptom": {"type": "boolean"},
        "would_pass_if_fixed": {"type": "boolean"},
        "cannot_reproduce_in_sandbox": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "feedback_for_next_attempt": {"type": "string"},
    },
    "required": ["reported_symptom", "observed_symptom", "failure_matches_symptom", "would_pass_if_fixed",
                 "cannot_reproduce_in_sandbox", "confidence", "reasoning", "feedback_for_next_attempt"],
    "additionalProperties": False,
}


def build_payload(ctx: Context) -> dict:
    issue, repro, triage, env = ctx.run.issue, ctx.run.repro or {}, ctx.run.triage or {}, ctx.run.env or {}
    comments = [c["body"] if isinstance(c, dict) else str(c) for c in issue.get("comments", [])[:10]]
    platform = ctx.sh("uname -sr").stdout.strip() or "Linux"
    return {
        "issue": {"title": issue.get("title", ""), "body": clip(issue.get("body", ""), 6000),
                  "comments": [clip(c, 1500) for c in comments]},
        "triage": {"expected": triage.get("expected"), "actual": triage.get("actual")},
        "sandbox": {"platform": platform, "python": env.get("python"),
                    "checked_out": env.get("ref"), "ref_note": env.get("ref_note")},
        "repro_attempt": {
            "number": repro.get("attempts"),
            "script": repro.get("script"),
            "python_flags": repro.get("python_flags"),
            "extra_packages_installed": repro.get("installed"),
            "result": repro.get("classification"),            # failed | hang (killed after 30 s)
            "claimed_symptom": repro.get("expected_symptom"),
            "output": clip(repro.get("last_output", ""), 3500),
        },
    }


def decide(data: dict) -> tuple[bool, str | None]:
    """The rule. Returns (matches, why_rejected)."""
    if not data["failure_matches_symptom"]:
        return False, "the failure does not show the reported symptom"
    if not data["would_pass_if_fixed"]:
        return False, "the script would still fail even if the bug were fixed (the script is wrong, not the library)"
    if not (data["confidence"] >= CONFIDENCE_MIN):
        return False, f"judge confidence {data['confidence']} is below {CONFIDENCE_MIN}"
    return True, None


def verdict(ctx: Context) -> State:
    messages = [{"role": "system", "content": ctx.prompt("verdict")},
                {"role": "user", "content": json.dumps(build_payload(ctx), ensure_ascii=False)}]
    data = ctx.model_json("ultra", messages, VERDICT_SCHEMA, stage="verdict").data
    matches, why = decide(data)
    feedback = data["feedback_for_next_attempt"].strip() or data["reasoning"]
    ctx.run.verdict = {
        "matches_issue": matches,
        "reasoning": data["reasoning"] if matches else f"{why}. {feedback}",
        "confidence": data["confidence"],
        "failure_matches_symptom": data["failure_matches_symptom"],
        "would_pass_if_fixed": data["would_pass_if_fixed"],
        "reported_symptom": data["reported_symptom"],
        "observed_symptom": data["observed_symptom"],
        "rejected_because": why,
    }
    ctx.run.verdict["cannot_reproduce_in_sandbox"] = data["cannot_reproduce_in_sandbox"]
    ctx.artifact("verdict", ctx.run.verdict)
    if matches:
        return State.FIX
    if data["cannot_reproduce_in_sandbox"] and data["confidence"] >= GIVE_UP_CONFIDENCE:
        ctx.run.outcome = "NOT_REPRODUCED"        # e.g. a Windows-only bug on a Linux sandbox: more tries cannot help
        return State.DONE
    if attempts_left(ctx.run, "repro") > 0:
        return State.REPRO
    ctx.run.outcome = "NOT_REPRODUCED"
    return State.DONE
