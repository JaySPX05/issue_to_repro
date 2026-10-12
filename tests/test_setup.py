import pytest

from itr.agent.budget import init_budget
from itr.agent.paths import PIP, REPO_DIR
from itr.agent.stages.setup import (CONFIG_PEEK, SMOKE_OK_CODES, declared_test_deps, numeric_version,
                                    pick_tag, plan_install, setup)
from itr.agent.states import State
from itr.sandbox.base import ExecResult
from tests.fakes import FakeLLM, FakeSandbox, make_ctx

OK = ExecResult("", "", 0)
FILES = "pyproject.toml\nsrc/pkg/__init__.py\ntests/test_a.py\n"


class ScriptedSandbox(FakeSandbox):
    """First matching rule wins. A rule's value is a result, or a list consumed in order
    (the last item repeats), so a command can fail first and succeed later."""

    def exec(self, cmd, timeout=120):
        self.commands.append(cmd)
        for needle, res in self.responses.items():
            if needle in cmd:
                if isinstance(res, list):
                    return res.pop(0) if len(res) > 1 else res[0]
                return res
        return OK


def sandbox(**rules):
    base = {"git ls-files": ExecResult(FILES, "", 0), "--version": ExecResult("Python 3.12.1", "", 0)}
    return ScriptedSandbox({**rules, **base})


def make(sb, llm=None, triage=None, issue_extra=None, **budget):
    issue = {"url": "u", "repo": "o/r", "title": "T", "body": "B", "comments": [], "ref": "main",
             **(issue_extra or {})}
    ctx, log = make_ctx(issue=issue, llm=llm, sandbox=sb)
    init_budget(ctx.run, **budget)
    ctx.run.triage = triage
    return ctx, log


# ---- pure helpers
def test_plan_install_uses_what_the_repo_has():
    plan = plan_install(["pyproject.toml", "requirements-dev.txt", "requirements.txt", "README.md"])
    joined = "\n".join(plan)
    assert plan[0].startswith("python3 -m venv")
    assert "install -q -e ." in joined and "-r requirements-dev.txt" in joined
    assert "-r requirements.txt" in joined and plan[-1].endswith("install -q pytest")


def test_plan_install_without_packaging_files_still_installs_pytest():
    assert plan_install(["main.py"])[-1] == f"{PIP} install -q pytest"
    assert not any("-e ." in c for c in plan_install(["main.py"]))


@pytest.mark.parametrize("text,expected", [("1.2.0", "1.2.0"), ("v2.31.0 (dev)", "2.31.0"),
                                           ("latest", None), (None, None), ("python 3.11, lib 4.5", "3.11")])
def test_numeric_version(text, expected):
    assert numeric_version(text) == expected


def test_pick_tag_tries_common_spellings():
    assert pick_tag(["v1.2.0", "1.2.1"], "1.2.0") == "v1.2.0"
    assert pick_tag(["1.2.0"], "1.2.0") == "1.2.0"
    assert pick_tag(["v1.2.1"], "1.2.0") is None


MODERN = """
[project]
name = "x"
[project.optional-dependencies]
docs = ["sphinx"]
[dependency-groups]
dev = ["ruff", "tox"]
tests = ["freezegun", "pytest"]
typing = ["mypy"]
"""
EXTRA_ONLY = """
[project]
name = "x"
[project.optional-dependencies]
test = ["pytest"]
"""


def test_declared_test_deps_reads_groups_and_extras():
    assert declared_test_deps(MODERN) == ("tests", None)
    assert declared_test_deps(EXTRA_ONLY) == (None, "test")
    assert declared_test_deps("[project]\nname='x'\n") == (None, None)
    assert declared_test_deps("not toml at all [[[") == (None, None)


def test_declared_test_deps_hand_scan_fallback_matches_tomllib(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "tomllib", None)       # makes `import tomllib` fail, like Python 3.10
    assert declared_test_deps(MODERN) == ("tests", None)
    assert declared_test_deps(EXTRA_ONLY) == (None, "test")


def test_plan_installs_the_test_group_and_the_test_extra():
    plan = plan_install(["pyproject.toml"], MODERN)
    assert f"{PIP} install -q --group tests" in "\n".join(plan)
    extra_plan = plan_install(["pyproject.toml"], EXTRA_ONLY)
    assert '-e ".[test]"' in "\n".join(extra_plan)


def test_setup_reads_pyproject_so_the_first_attempt_installs_the_group():
    sb = sandbox(**{"cat /work/repo/pyproject.toml": ExecResult(MODERN, "", 0)})
    ctx, _ = make(sb)
    assert setup(ctx) is State.REPRO
    assert any("--group tests" in c for c in sb.commands) and ctx.run.env["attempts"] == 1


def test_config_peek_command_is_valid_shell():
    import subprocess
    cmd = CONFIG_PEEK.replace(REPO_DIR, "/tmp")
    assert subprocess.run(["bash", "-n", "-c", cmd]).returncode == 0


# ---- the stage
def test_happy_path_needs_no_model_and_saves_a_checkpoint():
    ctx, log = make(sandbox())
    assert setup(ctx) is State.REPRO
    env = ctx.run.env
    assert env["snapshot"] == "snap-1" and env["python"] == "Python 3.12.1" and env["attempts"] == 1
    assert ctx.run.outcome is None
    assert "model_call" not in log.types()
    assert any(c.startswith("git clone") for c in ctx.sandbox.commands)


def test_pytest_exit_5_no_tests_is_still_a_usable_environment():
    assert 5 in SMOKE_OK_CODES
    ctx, _ = make(sandbox(**{"--collect-only": ExecResult("no tests ran", "", 5)}))
    assert setup(ctx) is State.REPRO


def test_clone_failure_retries_once_then_fails_without_model_calls():
    llm = FakeLLM()
    ctx, log = make(sandbox(**{"git clone": ExecResult("", "fatal: repository not found", 128)}), llm=llm)
    assert setup(ctx) is State.DONE
    assert ctx.run.outcome == "SETUP_FAILED" and "clone" in ctx.run.env["error"]
    assert "repository not found" in ctx.run.env["detail"]
    assert sum(c.startswith("git clone") for c in ctx.sandbox.commands) == 2
    assert llm.calls == []


def test_claimed_version_checks_out_the_matching_tag():
    sb = sandbox(**{"git tag --list": ExecResult("v1.1.0\nv1.2.0\n", "", 0)})
    ctx, log = make(sb, triage={"claimed_version": "1.2.0"})
    setup(ctx)
    assert any("checkout" in c and "v1.2.0" in c for c in sb.commands)
    assert ctx.run.env["ref"] == "v1.2.0"


def test_no_matching_tag_falls_back_to_default_branch_with_a_note():
    sb = sandbox(**{"git tag --list": ExecResult("v9.9.9\n", "", 0)})
    ctx, _ = make(sb, triage={"claimed_version": "1.2.0"})
    setup(ctx)
    assert ctx.run.env["ref"] == "main" and "no tag matches" in ctx.run.env["ref_note"]


def test_pinned_ref_is_never_overridden_by_a_claimed_version():
    sb = sandbox(**{"git tag --list": ExecResult("v1.2.0\n", "", 0)})
    ctx, _ = make(sb, triage={"claimed_version": "1.2.0"}, issue_extra={"ref": "abc123", "ref_pinned": True})
    setup(ctx)
    assert not any("tag --list" in c for c in sb.commands)
    assert ctx.run.env["ref"] == "abc123"


def test_bad_checkout_fails_cleanly():
    ctx, _ = make(sandbox(**{"checkout": ExecResult("", "pathspec 'abc' did not match", 1)}),
                  issue_extra={"ref": "abc", "ref_pinned": True})
    assert setup(ctx) is State.DONE and ctx.run.outcome == "SETUP_FAILED"


def test_failed_install_asks_super_for_a_plan_then_succeeds():
    fix = {"give_up": False, "reasoning": "needs the test extra", "commands": [f"{PIP} install -q -e '.[test]'"]}
    llm = FakeLLM({"super": [fix]})
    sb = sandbox(**{"-e .": ExecResult("", "ERROR: build failed", 1)})
    sb.responses = {f"{PIP} install -q -e '.[test]'": OK, **sb.responses}    # the fix works
    ctx, log = make(sb, llm=llm)
    assert setup(ctx) is State.REPRO
    assert [t for t, _, _ in llm.calls] == ["super"] and ctx.run.env["attempts"] == 2
    payload = llm.calls[0][1][-1]["content"]
    assert "build failed" in payload and "o/r" in payload
    assert ctx.run.env["install_commands"] == fix["commands"]


def test_model_can_give_up():
    llm = FakeLLM({"super": [{"give_up": True, "reasoning": "needs a GPU", "commands": []}]})
    ctx, _ = make(sandbox(**{"-e .": ExecResult("", "CUDA required", 1)}), llm=llm)
    assert setup(ctx) is State.DONE
    assert ctx.run.outcome == "SETUP_FAILED" and "CUDA" in ctx.run.env["detail"]


def test_attempt_budget_caps_the_retry_loop():
    bad = {"give_up": False, "reasoning": "try", "commands": ["false"]}
    llm = FakeLLM({"super": [bad, bad, bad]})
    ctx, _ = make(sandbox(**{"-e .": ExecResult("", "boom", 1), "false": ExecResult("", "still boom", 1)}),
                  llm=llm, max_attempts={"setup": 2})
    assert setup(ctx) is State.DONE
    assert ctx.run.outcome == "SETUP_FAILED" and ctx.run.env["attempts"] == 2
    assert len(llm.calls) == 1                      # no model call after the last attempt is spent


def test_smoke_check_failure_goes_to_the_model_too():
    fix = {"give_up": False, "reasoning": "import error", "commands": [f"{PIP} install -q six"]}
    llm = FakeLLM({"super": [fix]})
    sb = sandbox(**{"--collect-only": [ExecResult("", "ImportError: no module six", 2), ExecResult("ok", "", 0)]})
    ctx, _ = make(sb, llm=llm)
    assert setup(ctx) is State.REPRO and len(llm.calls) == 1
    assert "ImportError" in llm.calls[0][1][-1]["content"]
