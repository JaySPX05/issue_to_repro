"""VERIFY (NO model, plain code): the gate that makes a CONFIRMED result trustworthy.

A fix is accepted only if ALL of these hold, each decided by an exit code and never by a model:
  1. the new regression test FAILS on the original code   (so the test really detects the bug)
  2. the same test PASSES on the patched code             (so the patch really fixes it)
  3. the original reproduction script exits 0 when patched (the issue's own scenario is fixed)
  4. no test that passed before the patch fails after it   (checked when the project's suite runs)
Anything else is a failed attempt: back to FIX with the exact reason while attempts remain, otherwise
NEEDS_MAINTAINER (we reproduced the bug but cannot vouch for a patch).  See decisions D50-D51."""
from __future__ import annotations

import re

from itr.agent.budget import attempts_left
from itr.agent.context import Context, clip
from itr.agent.patching import apply_edits, reset_repo
from itr.agent.paths import PYTHON, REPO_DIR, SCRIPT_DIR
from itr.agent.stages.repro import SCRIPT_TIMEOUT, run_command
from itr.agent.states import State
from itr.sandbox.base import ExecResult

TEST_TIMEOUT = 90
SUITE_TIMEOUT = 150
FAILED_LINE = re.compile(r"^FAILED (\S+)", re.M)


# ---------------------------------------------------------------- pure helpers
def pytest_flags(repro_flags: list[str]) -> str:
    """Interpreter flags for the test run. Dropped: -W (pytest manages warnings itself) and -O/-OO (they
    strip `assert` statements, so a test run with them could never fail and VERIFY would be meaningless)."""
    keep = [f for f in repro_flags if not f.startswith("-W") and f not in ("-O", "-OO")]
    return (" " + " ".join(keep)) if keep else ""


def pytest_command(repro_flags: list[str], target: str = "", extra: str = "") -> str:
    return (f"cd {REPO_DIR} && PYTHONPATH={REPO_DIR}:{REPO_DIR}/src {PYTHON}{pytest_flags(repro_flags)} "
            f"-m pytest -q -p no:warnings -p no:cacheprovider --no-header {extra} {target}").replace("  ", " ").strip()


def failed_tests(output: str) -> set[str]:
    return set(FAILED_LINE.findall(output or ""))


def result_dict(res: ExecResult) -> dict:
    return {"exit_code": res.exit_code, "timed_out": res.timed_out,
            "output": clip((res.stdout or "") + (("\n" + res.stderr) if res.stderr else ""), 2500)}


def judge_before(res: ExecResult, repro_kind: str) -> str | None:
    """None when the test failed on the original code the way a bug-detecting test should; else why not."""
    if res.timed_out:
        return None if repro_kind == "hang" else (
            "the test hung on the original code, but the reproduction did not hang; make the test finish quickly")
    if res.exit_code == 1:
        return None
    if res.exit_code == 0:
        return ("test passed on the original code: the test does not detect the bug. Assert the correct "
                "behaviour on the exact input from the reproduction.")
    return (f"the test could not run on the original code (pytest exit {res.exit_code}): a syntax, import or "
            f"collection error. Output:\n{clip(res.stdout + res.stderr, 1200)}")


def judge_after(res: ExecResult) -> str | None:
    if res.exit_code == 0 and not res.timed_out:
        return None
    if res.timed_out:
        return "test timed out on the patched code: the fix does not cure the hang"
    return f"test failed on the patched code (pytest exit {res.exit_code}). Output:\n{clip(res.stdout + res.stderr, 1500)}"


def new_failures(baseline: set[str], patched: set[str], test_path: str) -> list[str]:
    return sorted(t for t in patched - baseline if not t.startswith(test_path))


# ---------------------------------------------------------------- the stage
def _fail(ctx: Context, reason: str) -> State:
    fx = ctx.run.fix
    fx["failure"] = reason
    if fx.get("history"):
        fx["history"][-1]["failure"] = reason
    ctx.artifact("verify_failed", {"reason": clip(reason, 1500)})
    if attempts_left(ctx.run, "fix") > 0:
        return State.FIX
    ctx.run.outcome = "NEEDS_MAINTAINER"
    return State.REPORT


def verify(ctx: Context) -> State:
    fx = ctx.run.fix or {}
    repro = ctx.run.repro or {}
    env = ctx.run.env or {}
    flags = repro.get("python_flags", [])
    kind = repro.get("classification", "failed")
    test_path = fx.get("test_path", "")
    ctx.run.fix = fx

    if fx.get("plan_errors"):
        return _fail(ctx, "the proposed change was rejected before running: " + "; ".join(fx["plan_errors"]))

    try:
        reset_repo(ctx, remove=test_path)
        suite_cmd = pytest_command(flags, extra="-rf")
        baseline = None
        if env.get("tests_collect_ok"):
            res = ctx.sh(suite_cmd, timeout=SUITE_TIMEOUT)
            baseline = None if res.timed_out else failed_tests(res.stdout)

        ctx.sandbox.write_file(f"{REPO_DIR}/{test_path}", fx["regression_test"])
        before = ctx.sh(pytest_command(flags, test_path), timeout=TEST_TIMEOUT)
        fx["before_result"] = result_dict(before)
        why = judge_before(before, kind)
        if why:
            return _fail(ctx, why)

        errors = apply_edits(ctx, fx["edits"])
        if errors:
            return _fail(ctx, "edit_errors when applying the patch: " + "; ".join(errors))
        after = ctx.sh(pytest_command(flags, test_path), timeout=TEST_TIMEOUT)
        fx["after_result"] = result_dict(after)
        why = judge_after(after)
        if why:
            return _fail(ctx, why)

        script_path = f"{SCRIPT_DIR}/repro_verify.py"
        ctx.sandbox.write_file(script_path, repro.get("script", ""))
        again = ctx.sh(run_command(script_path, flags), timeout=SCRIPT_TIMEOUT)
        fx["repro_after"] = result_dict(again)
        if again.timed_out or again.exit_code != 0:
            return _fail(ctx, "reproduction still fails on the patched code, so the reported problem is not "
                              "solved. Output:\n" + clip(again.stdout + again.stderr, 1200))

        fx["suite"] = {"ran": baseline is not None, "new_failures": []}
        if baseline is not None:
            res = ctx.sh(suite_cmd, timeout=SUITE_TIMEOUT)
            if not res.timed_out:
                bad = new_failures(baseline, failed_tests(res.stdout), test_path)
                fx["suite"]["new_failures"] = bad
                if bad:
                    return _fail(ctx, "breaks existing tests that passed before the patch: " + ", ".join(bad[:8]))
            else:
                fx["suite"]["ran"] = False
        fx["verified"] = True
        ctx.artifact("verify_passed", {"before": fx["before_result"]["exit_code"],
                                       "after": fx["after_result"]["exit_code"], "suite": fx["suite"]})
        return State.REPORT
    finally:
        reset_repo(ctx, remove=test_path)
