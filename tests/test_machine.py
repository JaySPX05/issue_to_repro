import pytest

from itr.agent.budget import BudgetExceeded, init_budget, spend_attempt
from itr.agent.machine import run_machine
from itr.agent.states import State
from tests.fakes import BUG, FakeLLM, FakeSandbox, happy_stages, make_ctx


def test_happy_path_runs_every_stage_in_order():
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG]}))
    run_machine(ctx, stages=happy_stages())
    assert ctx.run.outcome == "CONFIRMED"
    assert log.stages_started() == ["FETCH", "TRIAGE", "SETUP", "REPRO", "VERDICT", "FIX", "VERIFY", "REPORT"]
    assert log.types()[-1] == "outcome"
    assert ctx.sandbox.created and ctx.sandbox.destroyed


@pytest.mark.parametrize("kind", ["feature", "question", "duplicate", "unclear"])
def test_non_bug_ends_as_needs_maintainer_without_touching_sandbox(kind):
    triage = {**BUG, "kind": kind}
    ctx, log = make_ctx(llm=FakeLLM({"nano": [triage]}))
    run_machine(ctx, stages=happy_stages())
    assert ctx.run.outcome == "NEEDS_MAINTAINER"
    assert log.stages_started() == ["FETCH", "TRIAGE"]
    assert ctx.sandbox.commands == []


def test_model_calls_are_logged_and_budgeted_per_tier():
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG]}, tokens=50))
    run_machine(ctx, stages=happy_stages())
    assert ctx.run.budget["tokens_used"] == 100
    assert ctx.run.budget["tokens_by_tier"] == {"nano": 100}
    call = next(p for t, p in log.events if t == "model_call")
    assert call["tier"] == "nano" and call["stage"] == "triage"


def test_invalid_json_is_retried_with_feedback_then_succeeds():
    llm = FakeLLM({"nano": [{"kind": "bug"}, BUG]})        # first reply misses required keys
    ctx, _ = make_ctx(llm=llm)
    run_machine(ctx, stages=happy_stages())
    assert ctx.run.outcome == "CONFIRMED"
    assert len(llm.calls) == 2
    assert "schema violation" in llm.calls[1][1][-1]["content"]


def test_invalid_json_twice_ends_in_error_not_a_crash():
    llm = FakeLLM({"nano": [{"kind": "bug"}, {"kind": "nonsense"}]})
    ctx, log = make_ctx(llm=llm)
    run_machine(ctx, stages=happy_stages())
    assert ctx.run.outcome == "ERROR"
    assert ctx.sandbox.destroyed
    assert log.events[-1][0] == "outcome" and "TRIAGE" in log.events[-1][1]["reason"]


def test_json_string_reply_is_parsed():
    import json
    ctx, _ = make_ctx(llm=FakeLLM({"nano": [json.dumps(BUG)]}))
    run_machine(ctx, stages=happy_stages())
    assert ctx.run.outcome == "CONFIRMED"


def test_token_budget_exhaustion_ends_with_error():
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG, BUG]}, tokens=500))
    init_budget(ctx.run, max_tokens=800)                       # triage uses 1000
    stages_hit_budget = {State.FETCH: lambda c: State.TRIAGE,
                         State.TRIAGE: lambda c: (c.model("nano", [], None), c.model("nano", [], None))[0] and State.DONE}
    run_machine(ctx, stages=stages_hit_budget)
    assert ctx.run.outcome == "ERROR"
    assert "token budget" in log.events[-1][1]["reason"]


def test_stage_crash_ends_with_error_and_destroys_sandbox():
    def boom(ctx):
        raise RuntimeError("kaboom")
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG]}))
    run_machine(ctx, stages={**happy_stages(), State.SETUP: boom})
    assert ctx.run.outcome == "ERROR"
    assert ctx.sandbox.destroyed
    assert "kaboom" in log.events[-1][1]["reason"]


def test_run_that_ends_without_outcome_is_an_error():
    ctx, _ = make_ctx()
    run_machine(ctx, stages={State.FETCH: lambda c: State.DONE})
    assert ctx.run.outcome == "ERROR"


def test_step_limit_stops_infinite_loops():
    ctx, log = make_ctx()
    run_machine(ctx, stages={State.FETCH: lambda c: State.FETCH}, max_steps=5)
    assert ctx.run.outcome == "ERROR"
    assert log.events[-1][1]["reason"].startswith("step limit")


def test_verdict_can_branch_to_not_reproduced():

    def verdict_no(ctx):
        ctx.run.outcome = "NOT_REPRODUCED"
        return State.DONE
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG]}))
    run_machine(ctx, stages={**happy_stages(), State.VERDICT: verdict_no})
    assert ctx.run.outcome == "NOT_REPRODUCED"
    assert log.stages_started()[-1] == "VERDICT"


def test_attempt_budget_is_per_stage_and_returns_false_when_spent():
    ctx, _ = make_ctx()
    init_budget(ctx.run, max_attempts={"repro": 2})
    assert spend_attempt(ctx.run, "repro") and spend_attempt(ctx.run, "repro")
    assert not spend_attempt(ctx.run, "repro")
    assert spend_attempt(ctx.run, "fix")                       # other stages unaffected


def test_sandbox_commands_are_logged_with_clipped_output():
    from itr.sandbox.base import ExecResult
    sb = FakeSandbox({"echo": ExecResult("x" * 10000, "", 0)})
    ctx, log = make_ctx(sandbox=sb)
    init_budget(ctx.run)
    ctx.sh("echo hi")
    payload = next(p for t, p in log.events if t == "command_run")
    assert payload["exit_code"] == 0 and len(payload["stdout"]) < 4200


def test_cost_is_priced_per_tier_and_capped():
    from itr.agent.budget import check, init_budget, record_tokens
    ctx, log = make_ctx()
    init_budget(ctx.run, max_cost_usd=0.01, max_tokens=10**9)
    record_tokens(ctx.run, "ultra", 1_000_000, 0)            # $1.00 of input
    assert abs(ctx.run.budget["cost_usd"] - 1.0) < 1e-9
    record_tokens(ctx.run, "nano", 0, 1_000_000)             # $0.24 of output
    assert abs(ctx.run.budget["cost_usd"] - 1.24) < 1e-9
    import pytest
    with pytest.raises(BudgetExceeded, match="cost budget"):
        check(ctx.run)
