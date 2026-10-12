import pytest

from itr.agent.budget import init_budget
from itr.agent.paths import PIP, REPO_DIR, SCRIPT_DIR
from itr.agent.stages.repro import (FLAGS, SCRIPT_TIMEOUT, classify, run_command, valid_packages, repro)
from itr.agent.states import State
from itr.sandbox.base import ExecResult
from tests.fakes import FakeLLM, FakeSandbox, make_ctx

OK = ExecResult("", "", 0)
TRACE = 'Traceback (most recent call last):\n  File "/work/repro/repro_1.py", line 3, in <module>\nAssertionError: boom'


class Sandbox(FakeSandbox):
    """First matching rule wins; a list is consumed in order (the last item repeats)."""

    def exec(self, cmd, timeout=120):
        self.commands.append(cmd)
        for needle, res in self.responses.items():
            if needle in cmd:
                return (res.pop(0) if len(res) > 1 else res[0]) if isinstance(res, list) else res
        return OK


def plan(script="from lib import f\nassert f() == 1\n", flags=None, packages=None, symptom="f() returns the wrong value"):
    return {"reasoning": "because", "script": script, "python_flags": flags or [],
            "extra_packages": packages or [], "expected_symptom": symptom}


def make(llm, sb=None, verdict=None, **budget):
    ctx, log = make_ctx(issue={"url": "u", "repo": "o/r", "title": "T", "body": "B", "comments": [], "ref": "main"},
                        llm=llm, sandbox=sb or Sandbox())
    init_budget(ctx.run, **budget)
    ctx.run.triage = {"kind": "bug", "steps": ["call f()"], "expected": "1", "actual": "2"}
    ctx.run.env = {"python": "Python 3.12", "ref": "main", "ref_note": None}
    ctx.run.verdict = verdict
    return ctx, log


def script_run(sb):
    return [c for c in sb.commands if "repro_" in c and "PYTHONPATH" in c]


# ---- pure helpers
def test_classify_the_four_kinds():
    p = f"{SCRIPT_DIR}/repro_1.py"
    assert classify(ExecResult("", "", 0), p) == "passed"
    assert classify(ExecResult("", TRACE, 1), p) == "failed"
    assert classify(ExecResult("", "", -9, timed_out=True), p) == "hang"
    syn = f'  File "{p}", line 2\n    x = (\nSyntaxError: unexpected EOF'
    assert classify(ExecResult("", syn, 1), p) == "script_error"


def test_a_syntax_error_inside_the_library_is_not_blamed_on_our_script():
    lib = '  File "/work/repo/lib/mod.py", line 9\n    async = 1\nSyntaxError: invalid syntax'
    assert classify(ExecResult("", lib, 1), f"{SCRIPT_DIR}/repro_1.py") == "failed"


def test_valid_packages_accepts_specs_and_refuses_shell_tricks():
    ok, bad = valid_packages(["wcwidth", "numpy>=1.20", "pkg[extra]", "x; rm -rf /", "a b", "$(whoami)", "", "-e ."])
    assert ok == ["wcwidth", "numpy>=1.20", "pkg[extra]"] and len(bad) == 5
    assert len(valid_packages(["a", "b", "c", "d", "e"])[0]) == 3


def test_run_command_puts_the_repo_first_and_only_uses_listed_flags():
    cmd = run_command("/work/repro/repro_1.py", ["-bb"])
    assert cmd.startswith(f"cd {REPO_DIR} && PYTHONPATH={REPO_DIR}:{REPO_DIR}/src ") and " -bb /work/repro/" in cmd
    assert "-I" not in FLAGS                     # -I would ignore PYTHONPATH and import the wrong copy


# ---- the stage
def test_a_failing_script_goes_to_verdict_with_everything_the_judge_needs():
    llm = FakeLLM({"super": [plan()]})
    ctx, log = make(llm, Sandbox({"repro_1.py": ExecResult("got 2\n", TRACE, 1)}))
    assert repro(ctx) is State.VERDICT
    r = ctx.run.repro
    assert r["classification"] == "failed" and r["attempts"] == 1 and "AssertionError" in r["last_output"]
    assert r["script"].startswith("from lib") and r["expected_symptom"] == "f() returns the wrong value"
    assert ctx.sandbox.files[f"{SCRIPT_DIR}/repro_1.py"] == r["script"]
    assert ctx.run.outcome is None and [t for t, _, _ in llm.calls] == ["super"]
    assert any(t == "artifact" and p["name"] == "repro_script" for t, p in log.events)


def test_a_hang_counts_as_reproducing():
    ctx, _ = make(FakeLLM({"super": [plan()]}), Sandbox({"repro_1.py": ExecResult("", "", -9, timed_out=True)}))
    assert repro(ctx) is State.VERDICT and ctx.run.repro["classification"] == "hang"
    assert "no result after" in ctx.run.repro["last_output"]


def test_the_script_is_run_with_the_timeout_and_the_requested_flags():
    seen = {}

    class Spy(Sandbox):
        def exec(self, cmd, timeout=120):
            if "repro_1.py" in cmd and "PYTHONPATH" in cmd:
                seen["timeout"], seen["cmd"] = timeout, cmd
                return ExecResult("", TRACE, 1)
            return super().exec(cmd, timeout)
    ctx, _ = make(FakeLLM({"super": [plan(flags=["-bb"])]}), Spy())
    repro(ctx)
    assert seen["timeout"] == SCRIPT_TIMEOUT and " -bb " in seen["cmd"] and ctx.run.repro["python_flags"] == ["-bb"]


def test_a_script_that_passes_is_retried_with_the_result_in_the_next_prompt():
    first, second = plan(script="print('fine')\n"), plan(script="assert 0\n")
    llm = FakeLLM({"super": [first, second]})
    sb = Sandbox({"repro_1.py": ExecResult("fine\n", "", 0), "repro_2.py": ExecResult("", TRACE, 1)})
    ctx, _ = make(llm, sb)
    assert repro(ctx) is State.VERDICT and ctx.run.repro["attempts"] == 2
    prompt2 = llm.calls[1][1][-1]["content"]
    assert '"result": "passed"' in prompt2 and "print('fine')" in prompt2
    assert len(script_run(sb)) == 2


def test_a_syntax_error_in_our_script_is_retried_not_judged():
    syn = f'  File "{SCRIPT_DIR}/repro_1.py", line 1\n    x = (\nSyntaxError: invalid'
    llm = FakeLLM({"super": [plan(script="x = (\n"), plan()]})
    sb = Sandbox({"repro_1.py": ExecResult("", syn, 1), "repro_2.py": ExecResult("", TRACE, 1)})
    ctx, _ = make(llm, sb)
    assert repro(ctx) is State.VERDICT and ctx.run.repro["history"][0]["classification"] == "script_error"


def test_running_out_of_attempts_means_not_reproduced():
    llm = FakeLLM({"super": [plan(script=f"print({i})\n") for i in range(3)]})
    ctx, _ = make(llm, Sandbox(), max_attempts={"repro": 3})          # every run exits 0
    assert repro(ctx) is State.DONE
    assert ctx.run.outcome == "NOT_REPRODUCED" and ctx.run.repro["attempts"] == 3
    assert ctx.run.repro["classification"] == "passed" and len(llm.calls) == 3


def test_empty_or_oversized_scripts_cost_an_attempt_but_do_not_crash():
    llm = FakeLLM({"super": [plan(script="  \n"), plan(script="x" * 9000), plan()]})
    ctx, _ = make(llm, Sandbox({"repro_3.py": ExecResult("", TRACE, 1)}), max_attempts={"repro": 3})
    assert repro(ctx) is State.VERDICT
    assert [h["classification"] for h in ctx.run.repro["history"]][:2] == ["script_error", "script_error"]
    assert ctx.sandbox.files.get(f"{SCRIPT_DIR}/repro_3.py") is not None


def test_extra_packages_are_installed_once_and_bad_specs_are_refused():
    llm = FakeLLM({"super": [plan(packages=["wcwidth", "x; rm -rf /"]), plan(packages=["wcwidth"])]})
    sb = Sandbox({"repro_1.py": ExecResult("fine", "", 0), "repro_2.py": ExecResult("", TRACE, 1)})
    ctx, _ = make(llm, sb)
    repro(ctx)
    installs = [c for c in sb.commands if f"{PIP} install" in c]
    assert installs == [f"{PIP} install -q wcwidth"]                   # once, and the shell trick never ran
    assert ctx.run.repro["installed"] == ["wcwidth"]
    assert "refused package spec" in llm.calls[1][1][-1]["content"]


def test_a_failed_package_install_is_reported_back_to_the_model():
    llm = FakeLLM({"super": [plan(packages=["nosuchpkg"]), plan()]})
    sb = Sandbox({"nosuchpkg": ExecResult("", "No matching distribution", 1), "repro_2.py": ExecResult("", TRACE, 1),
                  "repro_1.py": ExecResult("fine", "", 0)})
    ctx, _ = make(llm, sb)
    repro(ctx)
    assert "No matching distribution" in llm.calls[1][1][-1]["content"]


def test_a_script_that_edits_the_repo_gets_undone():
    sb = Sandbox({"repro_1.py": ExecResult("", TRACE, 1), "git status --porcelain": ExecResult(" M lib/core.py\n", "", 0)})
    ctx, log = make(FakeLLM({"super": [plan()]}), sb)
    repro(ctx)
    assert any("git checkout -q -- ." in c for c in sb.commands)
    assert any(t == "artifact" and p["name"] == "repo_modified_by_script" for t, p in log.events)


def test_a_clean_repo_is_left_alone():
    ctx, _ = make(FakeLLM({"super": [plan()]}), Sandbox({"repro_1.py": ExecResult("", TRACE, 1)}))
    repro(ctx)
    assert not any("git checkout -q -- ." in c for c in ctx.sandbox.commands)


def test_reentry_after_a_rejected_attempt_keeps_history_and_passes_on_the_judges_feedback():
    llm1 = FakeLLM({"super": [plan(script="import nothing_here\n")]})
    ctx, _ = make(llm1, Sandbox({"repro_1.py": ExecResult("", "ModuleNotFoundError: nothing_here", 1)}))
    assert repro(ctx) is State.VERDICT
    ctx.run.verdict = {"matches_issue": False, "reasoning": "ImportError of the script, not the reported crash",
                       "confidence": 0.9}
    llm2 = FakeLLM({"super": [plan(script="assert 0\n")]})
    ctx.llm_call = llm2
    ctx.sandbox.responses["repro_2.py"] = ExecResult("", TRACE, 1)
    assert repro(ctx) is State.VERDICT
    assert ctx.run.repro["attempts"] == 2 and ctx.sandbox.files[f"{SCRIPT_DIR}/repro_2.py"] == "assert 0\n"
    prompt = llm2.calls[0][1][-1]["content"]
    assert "ImportError of the script" in prompt and "import nothing_here" in prompt
    assert ctx.run.budget["attempts"]["repro"] == 2                      # the budget is shared across re-entries


def test_reentry_with_no_attempts_left_ends_as_not_reproduced():
    llm = FakeLLM({"super": [plan()]})
    ctx, _ = make(llm, Sandbox({"repro_1.py": ExecResult("", TRACE, 1)}), max_attempts={"repro": 1})
    assert repro(ctx) is State.VERDICT
    ctx.run.verdict = {"matches_issue": False, "reasoning": "x", "confidence": 0.5}
    assert repro(ctx) is State.DONE and ctx.run.outcome == "NOT_REPRODUCED"


def test_the_prompt_carries_the_issue_triage_and_source_files():
    llm = FakeLLM({"super": [plan()]})
    sb = Sandbox({"repro_1.py": ExecResult("", TRACE, 1), "git ls-files": ExecResult("lib/__init__.py\nlib/core.py\n", "", 0)})
    ctx, _ = make(llm, sb)
    ctx.run.issue["body"] = "calling f() with no args returns 2"
    repro(ctx)
    prompt = llm.calls[0][1][-1]["content"]
    assert "calling f() with no args returns 2" in prompt and "lib/core.py" in prompt and "call f()" in prompt
    assert llm.calls[0][1][0]["content"].startswith("You write a short Python script")
