import pytest

from itr.agent.budget import init_budget
from itr.agent.patching import apply_edits, check_edits, source_path_error, test_path_error as _tpe
from itr.agent.paths import REPO_DIR
from itr.agent.stages.fix import fix, plan_errors, render_file, traceback_locations
from itr.agent.stages.report import report
from itr.agent.stages.verify import failed_tests, new_failures, pytest_command, verify
from itr.agent.states import State
from tests.fakes import FakeLLM, make_ctx
from tests.localbox import bash_available, make_repo

pytestmark = pytest.mark.skipif(not bash_available(), reason="needs a POSIX bash (e.g. Git Bash)")

LIB = "def total(xs):\n    s = 0\n    for x in xs[1:]:\n        s += x\n    return s\n"
SCRIPT = "from lib import total\nassert total([1, 2, 3]) == 6\n"
EXISTING_TEST = "from lib import total\n\ndef test_empty():\n    assert total([]) == 0\n"
GOOD_EDIT = {"path": "lib.py", "start_line": 3, "end_line": 3, "first_line": "for x in xs[1:]:", "replace": "    for x in xs:"}


def edit(start, end, first, replace, path="lib.py"):
    return {"path": path, "start_line": start, "end_line": end, "first_line": first, "replace": replace}
GOOD_TEST = "from lib import total\n\ndef test_total_includes_first_item():\n    assert total([1, 2, 3]) == 6\n"
LOCATE = {"reasoning": "the loop skips the first item", "files": ["lib.py"], "search_terms": ["total"]}


def fix_plan(edits=None, test_code=GOOD_TEST, test_path="tests/test_total.py"):
    return {"root_cause": "slice skips item 0", "edits": edits or [GOOD_EDIT], "test_path": test_path,
            "test_code": test_code, "notes": "n"}


@pytest.fixture
def env(tmp_path):
    sb = make_repo(tmp_path, {"lib.py": LIB, "tests/test_existing.py": EXISTING_TEST, "tests/__init__.py": ""})
    return sb


def make(sb, llm, tests_ok=False, **budget):
    ctx, log = make_ctx(llm=llm, sandbox=sb)
    init_budget(ctx.run, **budget)
    ctx.run.triage = {"kind": "bug"}
    ctx.run.env = {"python": "x", "tests_collect_ok": tests_ok}
    ctx.run.repro = {"script": SCRIPT, "python_flags": [], "classification": "failed",
                     "last_output": 'Traceback\n  File "/work/repo/lib.py", line 3, in total\nAssertionError'}
    ctx.run.verdict = {"matches_issue": True, "reasoning": "", "reported_symptom": "a", "observed_symptom": "b"}
    return ctx, log


def write_repro_script(sb):
    sb.write_file("/work/repro/repro_1.py", SCRIPT)


def run_fix_verify(ctx):
    assert fix(ctx) == State.VERIFY
    state = verify(ctx)
    if state == State.FIX and not (ctx.run.fix or {}).get("failure_expected"):
        print("VERIFY said:", ctx.run.fix.get("failure"))      # shown by pytest when a test fails
    return state


# ---- pure helpers
def test_source_paths_exclude_tests_config_and_unknown_files():
    tracked = {"lib.py", "pkg/mod.py", "tests/test_a.py", "conftest.py", "setup.py", "docs/conf.py", "README.md"}
    assert source_path_error("lib.py", tracked) is None
    assert source_path_error("pkg/mod.py", tracked) is None
    for bad in ["tests/test_a.py", "conftest.py", "setup.py", "docs/conf.py", "README.md", "../x.py",
                "/etc/passwd.py", "nope.py", "a b.py", "pkg/test_mod.py"]:
        assert source_path_error(bad, tracked | {"pkg/test_mod.py"}), bad


def test_test_path_must_be_new_and_named_test():
    tracked = {"tests/test_a.py"}
    assert _tpe("tests/test_new.py", tracked) is None
    assert _tpe("tests/test_a.py", tracked)
    assert _tpe("tests/new.py", tracked)
    assert _tpe("../test_x.py", tracked)
    assert _tpe("tests/test_x.txt", tracked)


def test_check_edits_limits():
    t = {"lib.py"}
    e = edit(1, 1, "a", "b")
    assert check_edits([e], t) == []
    assert check_edits([], t)
    assert check_edits([e] * 6, t)
    assert check_edits([{**e, "start_line": 0}], t)
    assert check_edits([{**e, "start_line": 5, "end_line": 4}], t)
    assert check_edits([{**e, "replace": "x" * 7000}], t)


def test_traceback_locations_and_file_windows():
    out = 'File "/work/repo/pkg/a.py", line 12, in f\n File "/usr/lib/x.py", line 3\n File "/work/repo/b.py", line 7'
    assert traceback_locations(out) == {"pkg/a.py": [12], "b.py": [7]}
    text = "\n".join(f"line{i}" for i in range(1, 3001))
    shown = render_file(text, [1500], [])
    assert " 1500| line1500" in shown and "    1| line1" not in shown and "omitted" in shown
    assert render_file("a\nb\n", [], []) == "    1| a\n    2| b"
    assert render_file("a\r\nb\r\n", [], []) == "    1| a\n    2| b"          # CRLF files look the same


def test_failed_test_parsing():
    out = "FAILED tests/a.py::t1 - assert 1\nFAILED tests/b.py::t2\n2 failed"
    assert failed_tests(out) == {"tests/a.py::t1", "tests/b.py::t2"}
    assert new_failures({"a::1"}, {"a::1", "b::2", "tests/test_new.py::x"}, "tests/test_new.py") == ["b::2"]


def test_pytest_command_drops_warning_flags_keeps_others():
    c = pytest_command(["-bb", "-W error"], "t.py")
    assert "-bb" in c and "-W" not in c and c.endswith("t.py")


def test_optimise_flags_are_never_used_for_the_test_run():
    c = pytest_command(["-O", "-OO", "-X dev"], "t.py")
    assert " -O" not in c and "-X dev" in c


def test_traceback_files_come_first_in_the_candidate_list():
    from itr.agent.stages.fix import order_candidates
    assert order_candidates(["a.py", "b.py", "z.py"], {"z.py": [1]}) == ["z.py", "a.py", "b.py"]


# ---- apply_edits
def test_apply_edits_is_all_or_nothing_and_explains_wrong_numbers(env):
    ctx, _ = make(env, FakeLLM())
    errors = apply_edits(ctx, [edit(3, 3, "for y in ys:", "    for x in xs:")])
    assert errors and "line 3 is" in errors[0] and "for x in xs[1:]:" in errors[0]       # shows the real line
    assert apply_edits(ctx, [edit(3, 99, "for x in xs[1:]:", "x")])                        # beyond the file
    errors = apply_edits(ctx, [GOOD_EDIT, edit(99, 99, "zzz", "q")])
    assert errors and env.read_file(f"{REPO_DIR}/lib.py") == LIB                          # nothing written


def test_apply_edits_replaces_inserts_and_deletes_using_original_numbers(env):
    ctx, _ = make(env, FakeLLM())
    # two edits in one file, both numbered against the ORIGINAL file; the first adds a line
    assert apply_edits(ctx, [edit(2, 2, "s = 0", "    s = 0\n    n = 0"),
                             edit(3, 4, "for x in xs[1:]:", "    for x in xs:\n        s += x")]) == []
    assert env.read_file(f"{REPO_DIR}/lib.py") == "def total(xs):\n    s = 0\n    n = 0\n    for x in xs:\n        s += x\n    return s\n"


def test_deleting_lines_with_an_empty_replacement(env):
    ctx, _ = make(env, FakeLLM())
    assert apply_edits(ctx, [edit(2, 2, "s = 0", "")]) == []
    assert env.read_file(f"{REPO_DIR}/lib.py") == "def total(xs):\n    for x in xs[1:]:\n        s += x\n    return s\n"


def test_overlapping_edits_and_no_op_edits_are_refused(env):
    ctx, _ = make(env, FakeLLM())
    assert "overlap" in apply_edits(ctx, [edit(2, 3, "s = 0", "x"), edit(3, 4, "for x in xs[1:]:", "y")])[0]
    assert "change nothing" in apply_edits(ctx, [edit(2, 2, "s = 0", "    s = 0")])[0]


def test_apply_edits_keeps_windows_line_endings(tmp_path):
    sb = make_repo(tmp_path, {"lib.py": LIB.replace("\n", "\r\n")})
    ctx, _ = make(sb, FakeLLM())
    assert apply_edits(ctx, [edit(3, 4, "for x in xs[1:]:", "    for x in xs:\n        s += x")]) == []
    text = sb.read_file(f"{REPO_DIR}/lib.py")
    assert "xs:\r\n" in text and "\n" not in text.replace("\r\n", "")


# ---- the whole gate against a real repo and real pytest
def test_good_fix_is_verified_and_repo_is_left_clean(env):
    write_repro_script(env)
    llm = FakeLLM({"super": [LOCATE, fix_plan()]})
    ctx, log = make(env, llm, tests_ok=True)
    assert run_fix_verify(ctx) == State.REPORT
    shown = [p["content"] for t, p in log.events if t == "artifact" and p["name"] == "verified_fix"][0]
    assert "xs[1:]" in shown["patch"] and shown["test_code"] == GOOD_TEST and shown["before"]["exit_code"] == 1
    fx = ctx.run.fix
    assert fx["verified"] and fx["before_result"]["exit_code"] == 1 and fx["after_result"]["exit_code"] == 0
    assert "xs[1:]" in fx["patch"] and fx["suite"] == {"ran": True, "new_failures": []}
    assert env.read_file(f"{REPO_DIR}/lib.py") == LIB                  # repo reset
    assert not (env.root / "repo/tests/test_total.py").exists()
    assert ctx.run.outcome is None                                      # REPORT turns this into CONFIRMED


def test_test_that_passes_on_original_code_is_rejected_and_retried(env):
    weak = "from lib import total\n\ndef test_weak():\n    assert total([]) == 0\n"
    llm = FakeLLM({"super": [LOCATE, fix_plan(test_code=weak), LOCATE, fix_plan()]})
    ctx, _ = make(env, llm)
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX
    assert "does not detect the bug" in ctx.run.fix["history"][-1]["failure"]
    assert run_fix_verify(ctx) == State.REPORT and ctx.run.fix["verified"]
    sent = llm.calls[-1][1][1]["content"]
    assert "does not detect the bug" in sent                           # the reason reaches the next prompt


def test_patch_that_does_not_fix_is_rejected(env):
    useless = edit(2, 2, "s = 0", "    s = 0  # nothing")
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(edits=[useless])]}))
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX
    assert "failed on the patched code" in ctx.run.fix["failure"]


def test_test_that_cannot_run_is_not_counted_as_a_detected_bug(env):
    broken = "from lib import total\n\ndef test_x(:\n    pass\n"
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(test_code=broken)]}))
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX
    assert "could not run" in ctx.run.fix["failure"]


def test_fix_that_breaks_an_existing_test_is_rejected(env):
    breaking = edit(5, 5, "return s", "    return s if xs else None")
    both = [GOOD_EDIT, breaking]
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(edits=both)]}), tests_ok=True)
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX
    assert "breaks existing tests" in ctx.run.fix["failure"] and "test_empty" in ctx.run.fix["failure"]


def test_repro_that_still_fails_after_patch_blocks_confirmation(env):
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan()]}))
    ctx.run.repro["script"] = "from lib import total\nassert total([5]) == 6\n"   # unrelated, still fails
    assert run_fix_verify(ctx) == State.FIX
    assert "reproduction still fails" in ctx.run.fix["failure"]


def test_plan_that_edits_protected_files_never_reaches_the_sandbox(env):
    cheat = edit(4, 4, "assert total([]) == 0", "    assert True", path="tests/test_existing.py")
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(edits=[cheat])]}))
    assert fix(ctx) == State.VERIFY
    assert ctx.run.fix["plan_errors"] and verify(ctx) == State.FIX
    assert env.read_file(f"{REPO_DIR}/tests/test_existing.py") == EXISTING_TEST


def test_wrong_line_numbers_are_a_plan_error_with_the_real_line_and_repo_unchanged(env):
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(edits=[edit(4, 4, "for x in xs[1:]:", "    for x in xs:")])]}))
    assert fix(ctx) == State.VERIFY
    assert "line 4 is" in ctx.run.fix["plan_errors"][0] and "s += x" in ctx.run.fix["plan_errors"][0]
    assert env.read_file(f"{REPO_DIR}/lib.py") == LIB


def test_out_of_attempts_gives_needs_maintainer(env):
    weak = "def test_weak():\n    assert True\n"
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(test_code=weak)]}), max_attempts={"fix": 1})
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.REPORT
    assert ctx.run.outcome == "NEEDS_MAINTAINER"


def test_escalates_to_ultra_on_third_attempt(env):
    weak = "def test_weak():\n    assert True\n"
    llm = FakeLLM({"super": [LOCATE, fix_plan(test_code=weak)] * 2 + [LOCATE], "ultra": [fix_plan()]})
    ctx, _ = make(env, llm)
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX and run_fix_verify(ctx) == State.FIX
    assert run_fix_verify(ctx) == State.REPORT
    assert [c[0] for c in llm.calls] == ["super", "super", "super", "super", "super", "ultra"]


def test_hang_repro_accepts_a_test_that_times_out_before_the_patch(env, monkeypatch):
    import importlib
    v = importlib.import_module("itr.agent.stages.verify")
    monkeypatch.setattr(v, "TEST_TIMEOUT", 2)
    hang_lib = "def total(xs):\n    while True:\n        pass\n"
    sb = make_repo(env.root.parent / "h", {"lib.py": hang_lib, "tests/__init__.py": ""})
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(
        edits=[edit(2, 3, "while True:", "    return sum(xs)")])]}))
    ctx.run.repro["classification"] = "hang"
    sb.write_file("/work/repro/repro_1.py", SCRIPT)
    assert run_fix_verify(ctx) == State.REPORT
    assert ctx.run.fix["before_result"]["timed_out"]


# ---- REPORT
def test_report_keeps_outcome_and_survives_model_failure():
    ctx, log = make_ctx(llm=FakeLLM({"nano": ["not json", "still not json"]}))
    init_budget(ctx.run)
    ctx.run.repro = {"script": "s", "last_output": "o"}
    ctx.run.fix = {"patch": "d", "regression_test": "t", "test_path": "tests/test_x.py"}
    assert report(ctx) == State.DONE
    assert ctx.run.outcome == "CONFIRMED"
    art = [p for t, p in log.events if t == "artifact" and p["name"] == "report"][0]["content"]
    assert art["generated_by_model"] is False and "### Proposed patch" in art["comment"]


def test_report_does_not_overwrite_needs_maintainer():
    ctx, log = make_ctx(llm=FakeLLM({"nano": [{"title": "T", "comment": "c"}]}))
    init_budget(ctx.run)
    ctx.run.outcome = "NEEDS_MAINTAINER"
    ctx.run.repro = {"script": "s", "last_output": "o"}
    report(ctx)
    assert ctx.run.outcome == "NEEDS_MAINTAINER"


def test_numbers_shown_to_the_model_match_the_numbers_used_to_edit(tmp_path):
    """A form feed or other odd line separator inside a file must not shift the numbering."""
    text = "def f():\n    a = '\x0c'\n    b = 1\n    return a\n"
    sb = make_repo(tmp_path, {"m.py": text})
    ctx, _ = make(sb, FakeLLM())
    view = render_file(text, [], [])
    assert "    3|     b = 1" in view
    assert apply_edits(ctx, [edit(3, 3, "b = 1", "    b = 2", path="m.py")]) == []
    assert sb.read_file(f"{REPO_DIR}/m.py") == text.replace("b = 1", "b = 2")


def test_report_tidy_repairs_one_line_comments():
    from itr.agent.stages.report import tidy
    assert tidy("A`n```python`nx=1`n```") == "A\n```python\nx=1\n```"
    assert tidy("A\\nB") == "A\nB"
    ok = "a\nb\nc\nd with `n inside"
    assert tidy(ok) == ok                       # real multi-line markdown is left alone


def test_an_edit_that_breaks_indentation_is_caught_before_verify_and_names_the_line(env):
    dedented = edit(3, 3, "for x in xs[1:]:", "for x in xs:")           # lost its 4-space indent
    ctx, _ = make(env, FakeLLM({"super": [LOCATE, fix_plan(edits=[dedented])]}))
    assert fix(ctx) == State.VERIFY
    err = ctx.run.fix["plan_errors"][0]
    assert "invalid Python" in err and "indentation" in err and "for x in xs:" in err
    assert env.read_file(f"{REPO_DIR}/lib.py") == LIB and ctx.run.fix["patch"] == ""


def test_a_repeated_patch_is_refused_with_the_earlier_failure(env):
    weak = "def test_weak():\n    assert True\n"                         # fails VERIFY the same way each time
    llm = FakeLLM({"super": [LOCATE, fix_plan(test_code=weak), LOCATE, fix_plan(test_code=weak)]})
    ctx, _ = make(env, llm)
    write_repro_script(env)
    assert run_fix_verify(ctx) == State.FIX
    assert fix(ctx) == State.VERIFY
    assert "identical to the ones in attempt 1" in ctx.run.fix["plan_errors"][0]


def test_plain_comment_does_not_repeat_the_title():
    from itr.agent.stages.report import plain_comment
    data = {"outcome": "CONFIRMED", "issue_title": "T", "reproduction": {"script": "s", "output": "o"},
            "patch": "d", "test_path": "t.py", "test_code": "c"}
    out = plain_comment(data)
    assert out["comment"].count("T") == 0 and out["title"].startswith("Reproduced")
