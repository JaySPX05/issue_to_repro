"""A search-term hit on a def/class shows the whole function; a patch that already failed on patched code is not retried."""
import pytest
from itr.agent.budget import init_budget
from tests.test_fix_verify import env  # noqa: F401  (fixture)

from itr.agent.stages.fix import block_end, render_file

FUNC = ["def long_word(self, chunks, width):", "    a = 1"] + [f"    step{i} = {i}" for i in range(60)] + \
       ["    return a", "", "def other():", "    pass"]


def make_text():
    pad = [f"x{i} = {i}" for i in range(500)]            # long enough that the file is shown as windows
    return "\n".join(pad + FUNC + pad) + "\n"


def test_block_end_covers_the_whole_function_and_stops_at_the_next_one():
    assert block_end(FUNC, 1) == len(FUNC) - 3            # up to "    return a"
    assert block_end(FUNC, 2) is None                      # not a def line


def test_block_end_is_capped_and_handles_multiline_signatures():
    long = ["def f(", "    a,", "    b,", "):"] + ["    x = 1"] * 200
    assert block_end(long, 1) == 1 + 80 or block_end(long, 1) <= 1 + 80
    assert block_end(long, 1) >= 5                         # the ") :" line did not end the block early


def test_term_on_a_def_line_shows_the_function_body_to_its_end():
    view = render_file(make_text(), [], ["long_word"], limit=100_000)
    assert "step59 = 59" in view and "return a" in view     # a 25-line window would have stopped near step22


def test_identical_patch_that_failed_on_patched_code_is_refused_even_with_a_new_test(env):
    from tests.test_fix_verify import LOCATE, fix_plan, make, run_fix_verify, write_repro_script
    from itr.agent.stages.fix import fix
    from itr.agent.states import State
    from tests.fakes import FakeLLM
    weak = "def test_weak():\n    assert True\n"                       # passes on the original, so VERIFY fails it
    other = "def test_weak_again():\n    assert 1 == 1\n"
    llm = FakeLLM({"super": [LOCATE, fix_plan(test_code=weak), LOCATE, fix_plan(test_code=other)]})
    ctx, _ = make(env, llm)
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX
    ctx.run.fix["history"][-1]["failure"] = "test timed out on the patched code: the fix does not cure the hang"
    assert fix(ctx) == State.VERIFY
    assert "exact patch was already tried in attempt 1" in ctx.run.fix["plan_errors"][0]


def test_summary_sentences_claiming_a_merge_are_removed():
    from itr.agent.stages.report import drop_untrue_claims
    text = "The call hangs. The agent verified a patch. The fix has been merged and guards the regression."
    assert drop_untrue_claims(text, "fallback") == "The call hangs. The agent verified a patch."
    assert drop_untrue_claims("The fix has been merged.", "fallback") == "fallback"


def test_an_empty_reply_is_retried_on_the_next_tier_up():
    from tests.fakes import FakeLLM, make_ctx
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    llm = FakeLLM({"super": ["", ""], "ultra": [{"x": 1}]})        # super runs out of room twice; ultra answers
    ctx, log = make_ctx(llm=llm)
    init_budget(ctx.run)
    resp = ctx.model_json("super", [{"role": "user", "content": "q"}], schema, stage="fix", retries=2)
    assert resp.data == {"x": 1}
    assert [c[0] for c in llm.calls] == ["super", "ultra"]          # ultra was asked as soon as super failed
    assert "EMPTY" in llm.calls[1][1][-1]["content"]
    assert any(t == "model_escalated" for t, _ in log.events)


def test_ultra_is_not_escalated_further_and_retries_still_run_out():
    import pytest as _pytest
    from itr.agent.context import StageError
    from tests.fakes import FakeLLM, make_ctx
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    ctx, _ = make_ctx(llm=FakeLLM({"ultra": ["", ""]}))
    init_budget(ctx.run)
    with _pytest.raises(StageError):
        ctx.model_json("ultra", [{"role": "user", "content": "q"}], schema, retries=1)
