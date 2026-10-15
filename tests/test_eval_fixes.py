"""Fixes that came out of the first full eval run (D58): syntax-error line, budget grace, closest
candidate in the report, and optional dependencies offered to REPRO."""
from itr.agent.machine import run_machine
from itr.agent.patching import syntax_error_line
from itr.agent.budget import BudgetExceeded, init_budget
from itr.agent.stages.repro import optional_dependencies
from itr.agent.stages.report import compose, evidence
from itr.agent.states import State
from tests.fakes import BUG, FakeLLM, happy_stages, make_ctx

REAL_STDERR = '''Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1])
  File "/usr/lib/python3.12/ast.py", line 52, in parse
    return compile(source, filename, mode, flags,
  File "src/click/types.py", line 1022
    x = 1
IndentationError: unexpected indent
'''


def test_syntax_error_line_skips_the_wrappers_own_line_one():
    assert syntax_error_line(REAL_STDERR, "src/click/types.py") == 1022


def test_syntax_error_line_none_when_not_found():
    assert syntax_error_line("boom", "a.py") is None


def _reproduced(ctx):
    ctx.run.repro = {"classification": "failed", "script": "x", "last_output": "boom"}
    ctx.run.verdict = {"matches_issue": True}


def _budget_stages(raise_in):
    st = happy_stages()

    def boom(ctx):
        _reproduced(ctx)
        raise BudgetExceeded("cost budget exhausted ($0.158/$0.15)")
    st[raise_in] = boom
    return st


def test_budget_exhausted_after_reproduction_is_needs_maintainer_with_a_report():
    ctx, log = make_ctx(llm=FakeLLM({"nano": [BUG]}))
    run_machine(ctx, stages=_budget_stages(State.FIX))
    assert ctx.run.outcome == "NEEDS_MAINTAINER"
    assert log.stages_started()[-1] == "REPORT"
    assert "without a verified patch" in log.events[-1][1]["reason"]
    assert ctx.run.budget["grace_used"] and ctx.run.budget["max_cost_usd"] > 0.15


def test_budget_exhausted_before_reproduction_is_still_an_error():
    ctx, _ = make_ctx(llm=FakeLLM({"nano": [BUG]}))
    st = happy_stages()
    st[State.REPRO] = lambda c: (_ for _ in ()).throw(BudgetExceeded("time budget exhausted (900s)"))
    run_machine(ctx, stages=st)
    assert ctx.run.outcome == "ERROR"


def test_grace_is_given_only_once():
    ctx, _ = make_ctx(llm=FakeLLM({"nano": [BUG]}))
    st = _budget_stages(State.FIX)
    st[State.REPORT] = lambda c: (_ for _ in ()).throw(BudgetExceeded("still over"))
    run_machine(ctx, stages=st)
    assert ctx.run.outcome == "ERROR"


def test_report_shows_closest_candidate_clearly_marked_unverified():
    ctx, _ = make_ctx()
    ctx.run.outcome = "NEEDS_MAINTAINER"
    ctx.run.fix = {"history": [
        {"patch": "bad", "test_agrees": False, "failure": "x"},
        {"patch": "diff --git a b", "test_agrees": True, "test_path": "t/test_n.py", "test_code": "def test_n(): pass",
         "failure": "reproduction still fails on the patched code\nmore"}]}
    data = evidence(ctx, "NEEDS_MAINTAINER")
    text = compose(data, "t", "s")["comment"]
    assert "Closest candidate (NOT verified)" in text and "diff --git a b" in text
    assert "reproduction still fails on the patched code" in text and "more" not in text
    assert "bad" not in text


def test_report_has_no_candidate_section_when_no_attempt_agreed_with_its_test():
    ctx, _ = make_ctx()
    ctx.run.fix = {"history": [{"patch": "p", "test_agrees": False}]}
    assert "candidate" not in evidence(ctx, "NEEDS_MAINTAINER")


def test_optional_dependencies_lists_user_facing_extras_only():
    toml = '''[project.optional-dependencies]
widechars = ["wcwidth>=0.2"]
test = ["pytest"]
docs = ["sphinx"]
'''
    assert optional_dependencies(toml) == {"widechars": ["wcwidth"]}
    assert optional_dependencies("not toml {{") == {}
    assert optional_dependencies("") == {}
