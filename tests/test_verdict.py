import json

import pytest

from itr.agent.budget import init_budget, spend_attempt
from itr.agent.context import StageError
from itr.agent.stages.repro import build_payload as repro_payload
from itr.agent.stages.verdict import CONFIDENCE_MIN, decide, verdict
from itr.agent.states import State
from tests.fakes import BUG, FakeLLM, FakeSandbox, make_ctx
from itr.sandbox.base import ExecResult

GOOD = {"reported_symptom": "IndexError on empty input", "observed_symptom": "IndexError: list index out of range",
        "failure_matches_symptom": True, "would_pass_if_fixed": True, "cannot_reproduce_in_sandbox": False, "confidence": 0.9,
        "reasoning": "same exception from the library", "feedback_for_next_attempt": ""}


def make(reply, attempts_used=1, repro=None, sandbox=None, **budget):
    llm = FakeLLM({"ultra": [reply]})
    ctx, log = make_ctx(llm=llm, sandbox=sandbox)
    init_budget(ctx.run, **budget)
    for _ in range(attempts_used):
        spend_attempt(ctx.run, "repro")
    ctx.run.issue.update({"title": "Crash on empty list", "body": "foo([]) raises IndexError",
                          "comments": [{"body": "same here on 3.12", "author": "x", "created_at": "t"}]})
    ctx.run.triage = BUG
    ctx.run.env = {"python": "/work/venv/bin/python", "ref": "abc123", "ref_note": "pinned"}
    ctx.run.repro = repro or {"script": "import foo\nfoo.foo([])", "python_flags": ["-bb"], "installed": ["wcwidth"],
                              "classification": "failed", "attempts": attempts_used,
                              "expected_symptom": "IndexError", "last_output": "IndexError: list index out of range"}
    return ctx, log, llm


def test_a_matching_failure_goes_to_fix_and_is_recorded():
    ctx, log, llm = make(GOOD)
    assert verdict(ctx) is State.FIX
    v = ctx.run.verdict
    assert v["matches_issue"] is True and v["rejected_because"] is None and v["confidence"] == 0.9
    assert "verdict" in [p.get("name") for t, p in log.events if t == "artifact"]


def test_the_judge_is_the_ultra_tier_and_sees_issue_script_and_output():
    ctx, log, llm = make(GOOD)
    verdict(ctx)
    tier, messages, schema = llm.calls[0]
    assert tier == "ultra" and schema["required"]
    body = json.loads(messages[1]["content"])
    assert body["issue"]["title"] == "Crash on empty list" and body["issue"]["comments"] == ["same here on 3.12"]
    assert "foo.foo([])" in body["repro_attempt"]["script"]
    assert body["repro_attempt"]["python_flags"] == ["-bb"]
    assert body["repro_attempt"]["output"] == "IndexError: list index out of range"
    assert body["repro_attempt"]["result"] == "failed"
    assert "STRICT" in messages[0]["content"].upper() or "strict" in messages[0]["content"]


def test_sandbox_platform_is_sent_so_a_windows_only_bug_can_be_spotted():
    sb = FakeSandbox({"uname": ExecResult("Linux 6.1\n", "", 0)})
    ctx, log, llm = make(GOOD, sandbox=sb)
    verdict(ctx)
    assert json.loads(llm.calls[0][1][1]["content"])["sandbox"]["platform"] == "Linux 6.1"


def test_wrong_reason_with_attempts_left_goes_back_to_repro_with_feedback():
    bad = {**GOOD, "failure_matches_symptom": False, "observed_symptom": "AttributeError: _termui_impl",
           "feedback_for_next_attempt": "import click._termui_impl explicitly", "confidence": 0.9}
    ctx, log, llm = make(bad, attempts_used=1)
    assert verdict(ctx) is State.REPRO
    v = ctx.run.verdict
    assert v["matches_issue"] is False
    assert "import click._termui_impl explicitly" in v["reasoning"]
    assert ctx.run.outcome is None
    # and the next REPRO prompt carries it
    payload = repro_payload(ctx, [], "", 3)
    assert "import click._termui_impl explicitly" in payload["judge_feedback"]


def test_model_that_says_matches_but_admits_the_script_is_wrong_is_overruled():
    # the click-1925 pattern: the failure looks right, but the script would fail even after a fix
    contradiction = {**GOOD, "failure_matches_symptom": True, "would_pass_if_fixed": False, "confidence": 0.95,
                     "feedback_for_next_attempt": "assert is_bool_flag is False instead of invoking the command"}
    ctx, log, llm = make(contradiction)
    assert verdict(ctx) is State.REPRO
    assert ctx.run.verdict["matches_issue"] is False
    assert "still fail even if the bug were fixed" in ctx.run.verdict["rejected_because"]


def test_low_confidence_is_rejected_even_when_both_answers_are_yes():
    ctx, log, llm = make({**GOOD, "confidence": CONFIDENCE_MIN - 0.01})
    assert verdict(ctx) is State.REPRO
    assert "below" in ctx.run.verdict["rejected_because"]


def test_confidence_at_the_threshold_is_accepted():
    ctx, log, llm = make({**GOOD, "confidence": CONFIDENCE_MIN})
    assert verdict(ctx) is State.FIX


def test_nan_confidence_is_not_accepted():
    assert decide({**GOOD, "confidence": float("nan")})[0] is False


def test_rejection_with_no_attempts_left_ends_not_reproduced():
    bad = {**GOOD, "failure_matches_symptom": False}
    ctx, log, llm = make(bad, attempts_used=4, max_attempts={"repro": 4})
    assert verdict(ctx) is State.DONE
    assert ctx.run.outcome == "NOT_REPRODUCED"


def test_rejection_with_one_attempt_left_still_retries():
    bad = {**GOOD, "failure_matches_symptom": False}
    ctx, log, llm = make(bad, attempts_used=3, max_attempts={"repro": 4})
    assert verdict(ctx) is State.REPRO


def test_empty_feedback_falls_back_to_the_reasoning():
    bad = {**GOOD, "failure_matches_symptom": False, "feedback_for_next_attempt": "  ", "reasoning": "different error"}
    ctx, log, llm = make(bad)
    verdict(ctx)
    assert "different error" in ctx.run.verdict["reasoning"]


def test_hang_result_is_passed_to_the_judge():
    ctx, log, llm = make(GOOD, repro={"script": "x", "python_flags": [], "installed": [], "classification": "hang",
                                      "attempts": 1, "expected_symptom": "hang", "last_output": "timed out"})
    verdict(ctx)
    assert json.loads(llm.calls[0][1][1]["content"])["repro_attempt"]["result"] == "hang"


def test_an_off_schema_reply_is_retried_once_then_fails_the_stage():
    llm = FakeLLM({"ultra": [{"confidence": 1}, {"confidence": 1}]})
    ctx, log = make_ctx(llm=llm)
    init_budget(ctx.run)
    ctx.run.repro = {"script": "x", "classification": "failed", "last_output": ""}
    with pytest.raises(StageError):
        verdict(ctx)
    assert len(llm.calls) == 2


def test_long_output_is_clipped_for_the_judge():
    ctx, log, llm = make(GOOD, repro={"script": "x", "python_flags": [], "installed": [], "classification": "failed",
                                      "attempts": 1, "expected_symptom": "s", "last_output": "E" * 20000})
    verdict(ctx)
    sent = json.loads(llm.calls[0][1][1]["content"])["repro_attempt"]["output"]
    assert len(sent) < 4000


WINDOWS_ONLY = {**GOOD, "failure_matches_symptom": False, "cannot_reproduce_in_sandbox": True, "confidence": 0.9,
                "feedback_for_next_attempt": "needs Windows"}


def test_a_bug_the_sandbox_cannot_show_stops_the_run_without_more_attempts():
    ctx, log, llm = make(WINDOWS_ONLY, attempts_used=1, max_attempts={"repro": 4})
    assert verdict(ctx) is State.DONE
    assert ctx.run.outcome == "NOT_REPRODUCED"
    assert ctx.run.verdict["cannot_reproduce_in_sandbox"] is True


def test_giving_up_early_needs_high_confidence():
    ctx, log, llm = make({**WINDOWS_ONLY, "confidence": 0.7}, attempts_used=1, max_attempts={"repro": 4})
    assert verdict(ctx) is State.REPRO and ctx.run.outcome is None


def test_the_give_up_flag_cannot_override_an_accepted_repro():
    ctx, log, llm = make({**GOOD, "cannot_reproduce_in_sandbox": True})
    assert verdict(ctx) is State.FIX and ctx.run.outcome is None


def test_the_prompt_keeps_the_assertion_convention_and_forbids_asserting_the_bug():
    ctx, log, llm = make(GOOD)
    verdict(ctx)
    prompt = llm.calls[0][1][0]["content"]
    assert "Never advise asserting the buggy behaviour" in prompt
    assert "cannot_reproduce_in_sandbox" in prompt
