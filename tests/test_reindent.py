"""Indentation repair in FIX (D58): a wrong first-line indent is repaired, a wrong idea is not."""
import pytest

from itr.agent.budget import init_budget
from itr.agent.patching import indent_hint, reindent_variants
from itr.agent.paths import REPO_DIR
from itr.agent.stages.fix import fix
from itr.agent.states import State
from tests.fakes import FakeLLM, FakeSandbox, make_ctx
from tests.localbox import bash_available, make_repo
from tests.test_fix_verify import LOCATE, LIB, EXISTING_TEST, GOOD_TEST, edit, fix_plan, make

SRC = "class A:\n    def f(self, xs):\n        s = 0\n        for x in xs[1:]:\n            s += x\n        return s\n"


def sandbox_with(text):
    ctx, _ = make_ctx(sandbox=FakeSandbox())
    ctx.sandbox.files[f"{REPO_DIR}/m.py"] = text
    return ctx


def test_variants_align_first_line_and_shift_the_block():
    ctx = sandbox_with(SRC)
    e = edit(4, 5, "for x in xs[1:]:", "for x in xs:\n    s += x", path="m.py")      # 0 spaces, should be 8
    uniform, first_only = reindent_variants(ctx, [e])
    assert uniform[0]["replace"] == "        for x in xs:\n            s += x"
    assert first_only[0]["replace"] == "        for x in xs:\n    s += x"


def test_no_variants_when_indentation_already_matches():
    ctx = sandbox_with(SRC)
    e = edit(4, 4, "for x in xs[1:]:", "        for x in xs:", path="m.py")
    assert reindent_variants(ctx, [e]) == []


def test_block_that_cannot_move_left_is_not_shifted():
    ctx = sandbox_with(SRC)
    e = edit(4, 5, "for x in xs[1:]:", "                for x in xs:\n    s += x", path="m.py")   # needs -8 but line 2 has 4
    variants = reindent_variants(ctx, [e])
    assert all(v[0]["replace"] != "for x in xs:\n-" for v in variants)
    assert len(variants) == 1 and variants[0][0]["replace"].startswith("        for x in xs:")   # first-line-only


def test_indent_hint_names_both_indents():
    ctx = sandbox_with(SRC)
    hint = indent_hint(ctx, [edit(4, 4, "for x in xs[1:]:", "for x in xs:", path="m.py")])
    assert "indented 8 spaces" in hint and "indented 0" in hint


pytestmark_bash = pytest.mark.skipif(not bash_available(), reason="needs a POSIX bash (e.g. Git Bash)")


@pytestmark_bash
def test_fix_repairs_a_wrong_indent_and_marks_it(tmp_path):
    sb = make_repo(tmp_path, {"lib.py": LIB, "tests/test_existing.py": EXISTING_TEST, "tests/__init__.py": ""})
    bad = edit(3, 3, "for x in xs[1:]:", "for x in xs:")            # lib.py line 3 is indented 4; this is 0
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(edits=[bad])]}))
    assert fix(ctx) == State.VERIFY
    fx = ctx.run.fix
    assert fx["plan_errors"] == [] and "for x in xs:" in fx["patch"]
    assert fx["history"][-1]["auto_reindented"] is True
    assert "    for x in xs:" in fx["patch"]


@pytestmark_bash
def test_fix_does_not_touch_a_replacement_that_already_parses(tmp_path):
    sb = make_repo(tmp_path, {"lib.py": LIB, "tests/test_existing.py": EXISTING_TEST, "tests/__init__.py": ""})
    ok = edit(3, 3, "for x in xs[1:]:", "    for x in xs:")
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(edits=[ok])]}))
    assert fix(ctx) == State.VERIFY
    assert ctx.run.fix["history"][-1]["auto_reindented"] is False


@pytestmark_bash
def test_hopeless_indentation_still_reports_a_hint(tmp_path):
    sb = make_repo(tmp_path, {"lib.py": LIB, "tests/test_existing.py": EXISTING_TEST, "tests/__init__.py": ""})
    broken = edit(3, 3, "for x in xs[1:]:", "    for x in xs(:")        # a real syntax error, indentation is right
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(edits=[broken])]}))
    assert fix(ctx) == State.VERIFY
    assert ctx.run.fix["plan_errors"] and "invalid Python" in ctx.run.fix["plan_errors"][0]


# ---- patch size guard (D60)
from itr.agent.patching import MAX_CHANGED_LINES, changed_lines, size_error

DIFF = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-old\n+new\n same\n"


def test_changed_lines_ignores_headers_and_context():
    assert changed_lines(DIFF) == 2
    assert size_error(DIFF) is None


def test_size_error_names_count_and_limit():
    msg = size_error("\n".join("+x" for _ in range(MAX_CHANGED_LINES + 1)))
    assert str(MAX_CHANGED_LINES + 1) in msg and f"limit is {MAX_CHANGED_LINES}" in msg


@pytestmark_bash
def test_fix_refuses_a_rewrite_and_accepts_the_small_change(tmp_path):
    big = "def f(xs):\n" + "".join(f"    a{i} = {i}\n" for i in range(60)) + "    return 1\n"
    sb = make_repo(tmp_path, {"lib.py": big, "tests/test_existing.py": EXISTING_TEST, "tests/__init__.py": ""})
    rewrite = edit(2, 61, "a0 = 0", "\n".join(f"    b{i} = {i}" for i in range(60)))
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(edits=[rewrite])]}))
    assert fix(ctx) == State.VERIFY
    assert "limit is" in ctx.run.fix["plan_errors"][0] and ctx.run.fix["patch"] == ""
    small = edit(62, 62, "return 1", "    return 2")
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(edits=[small])]}))
    assert fix(ctx) == State.VERIFY and ctx.run.fix["plan_errors"] == []


# ---- relocating a misnumbered edit (D63)
from itr.agent.patching import relocate_edits

NUMBERED = "\n".join(f"line_{i:02d} = {i}" for i in range(1, 41)) + "\n"


def test_relocate_moves_an_off_by_a_few_edit_to_the_unique_quoted_line():
    ctx = sandbox_with(NUMBERED)
    e = edit(10, 11, "line_14 = 14", "x", path="m.py")                  # says 10, really 14; range keeps its length
    (moved,), notes = relocate_edits(ctx, [e])
    assert (moved["start_line"], moved["end_line"]) == (14, 15) and "moved an edit from line 10 to 14" in notes[0]


def test_relocate_leaves_correct_missing_ambiguous_and_short_quotes_alone():
    ctx = sandbox_with(NUMBERED + "line_14 = 14\n")                       # now line_14 = 14 appears twice (14 and 41)
    right = edit(5, 5, "line_05 = 5", "x", path="m.py")
    missing = edit(5, 5, "this text is not in the file", "x", path="m.py")
    twice = edit(12, 12, "line_14 = 14", "x", path="m.py")               # both 14 and 41 ... only 14 is within the window
    short = edit(5, 5, "x = 1", "x", path="m.py")
    out, notes = relocate_edits(ctx, [right, missing, short])
    assert out == [right, missing, short] and notes == []
    ctx2 = sandbox_with(NUMBERED.replace("line_20 = 20", "line_14 = 14"))   # genuinely twice inside the window
    (kept,), n2 = relocate_edits(ctx2, [edit(17, 17, "line_14 = 14", "x", path="m.py")])
    assert kept["start_line"] == 17 and n2 == []


def test_relocate_does_not_search_beyond_the_window():
    ctx = sandbox_with(NUMBERED)
    far = edit(1, 1, "line_38 = 38", "x", path="m.py")
    assert relocate_edits(ctx, [far])[1] == []


@pytestmark_bash
def test_fix_relocates_then_applies_and_records_it(tmp_path):
    sb = make_repo(tmp_path, {"lib.py": LIB, "tests/test_existing.py": EXISTING_TEST, "tests/__init__.py": ""})
    off = edit(2, 2, "for x in xs[1:]:", "    for x in xs:")             # the loop is on line 3, quoted correctly
    ctx, _ = make(sb, FakeLLM({"super": [LOCATE, fix_plan(edits=[off])]}))
    assert fix(ctx) == State.VERIFY
    fx = ctx.run.fix
    assert fx["plan_errors"] == [] and "+    for x in xs:" in fx["patch"]
    assert fx["history"][-1]["auto_relocated"] and "line 2 to 3" in fx["history"][-1]["auto_relocated"][0]
