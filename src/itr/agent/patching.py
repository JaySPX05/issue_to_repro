"""Shared by FIX and VERIFY: validating and applying a model's proposed change, in plain code.

The model proposes edits as numbered line ranges ("replace lines 40-44 of this file with this text") over
a numbered view of the file that WE show it. It is not asked to retype existing code, because live runs
showed models invent text when quoting (D53), and it does not write diffs (wrong line numbers and context).
The rules below also stop a patch from "winning" by editing tests, test configuration or packaging
(decisions D49)."""
from __future__ import annotations

import re

from itr.agent.context import Context
from itr.agent.paths import PYTHON, REPO_DIR

MAX_EDITS = 5
MAX_REPLACE_CHARS = 6000
MAX_TEST_CHARS = 8000

SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
# Never editable by a patch: tests, their configuration, packaging, docs, CI.
PROTECTED = re.compile(
    r"(^|/)(tests?|testing|docs?|examples?|benchmarks?|\.github)/"
    r"|(^|/)(conftest|setup|noxfile)\.py$"
    r"|(^|/)(test_[^/]*|[^/]*_test)\.py$"
)


def _clean_relative(path: str) -> bool:
    return bool(path) and bool(SAFE_PATH.match(path)) and ".." not in path.split("/") and not path.startswith("/")


def source_path_error(path: str, tracked: set[str]) -> str | None:
    """A patch may edit only an existing, tracked, non-test Python source file."""
    if not _clean_relative(path) or not path.endswith(".py"):
        return f"{path!r}: not a relative .py path"
    if PROTECTED.search(path):
        return f"{path}: tests, conftest, setup and docs may not be edited by the patch"
    if path not in tracked:
        return f"{path}: not a file in the repository"
    return None


def test_path_error(path: str, tracked: set[str]) -> str | None:
    """The regression test must be a NEW file named test_*.py inside the repository."""
    if not _clean_relative(path) or not path.endswith(".py"):
        return f"test path {path!r}: not a relative .py path"
    if not path.rsplit("/", 1)[-1].startswith("test_"):
        return f"test path {path}: the file name must start with test_"
    if path in tracked:
        return f"test path {path}: that file already exists; choose a new file name"
    return None


def tracked_files(ctx: Context) -> set[str]:
    return set(ctx.sh(f"cd {REPO_DIR} && git ls-files").stdout.splitlines())


def check_edits(edits: list[dict], tracked: set[str]) -> list[str]:
    errors = []
    if not edits:
        errors.append("no edits were given")
    if len(edits) > MAX_EDITS:
        errors.append(f"too many edits ({len(edits)}; at most {MAX_EDITS})")
    for e in edits[:MAX_EDITS]:
        bad = source_path_error(e["path"], tracked)
        if bad:
            errors.append(bad)
        if e["start_line"] < 1 or e["end_line"] < e["start_line"]:
            errors.append(f"{e['path']}: bad line range {e['start_line']}-{e['end_line']} "
                          f"(1-based, end must not be before start)")
        if len(e["replace"]) > MAX_REPLACE_CHARS:
            errors.append(f"{e['path']}: replacement longer than {MAX_REPLACE_CHARS} characters")
    return errors


def apply_edits(ctx: Context, edits: list[dict]) -> list[str]:
    """Apply every edit or none. An edit replaces the inclusive line range start_line..end_line of the
    file AS IT WAS BEFORE ANY EDIT (all edits use the numbering the model was shown, so they never shift
    each other). `first_line` must equal the text of line start_line (ignoring indentation): a cheap check
    that the model's numbering is right. Returns error strings; empty means applied."""
    by_path: dict[str, list[dict]] = {}
    for e in edits:
        by_path.setdefault(e["path"], []).append(e)
    results: dict[str, str] = {}
    errors: list[str] = []
    for path, group in by_path.items():
        try:
            text = ctx.sandbox.read_file(f"{REPO_DIR}/{path}")
        except Exception as ex:
            errors.append(f"{path}: cannot read ({type(ex).__name__})")
            continue
        if "\ufffd" in text:
            errors.append(f"{path}: not valid UTF-8, refusing to rewrite it")
            continue
        crlf = "\r\n" in text
        lines = text.split("\n")                    # the same split the model's numbered view uses
        if lines and lines[-1] == "":
            lines.pop()
            trailing = True
        else:
            trailing = False
        group = sorted(group, key=lambda e: e["start_line"])
        ok = True
        for a, b in zip(group, group[1:]):
            if b["start_line"] <= a["end_line"]:
                errors.append(f"{path}: edits overlap (lines {a['start_line']}-{a['end_line']} and "
                              f"{b['start_line']}-{b['end_line']}); merge them into one edit")
                ok = False
        for e in group:
            if e["end_line"] > len(lines):
                errors.append(f"{path}: line {e['end_line']} does not exist (the file has {len(lines)} lines)")
                ok = False
            elif lines[e["start_line"] - 1].rstrip("\r").strip() != e["first_line"].strip():
                errors.append(f"{path}: line {e['start_line']} is {lines[e['start_line'] - 1].rstrip(chr(13))!r}, "
                              f"not {e['first_line'].strip()!r}; check the line numbers")
                ok = False
        if not ok:
            continue
        for e in reversed(group):                    # bottom-up keeps earlier numbers valid
            rep = e["replace"].replace("\r\n", "\n")
            new = [] if rep == "" else (rep[:-1] if rep.endswith("\n") else rep).split("\n")
            if crlf:
                new = [ln + "\r" for ln in new]
            lines[e["start_line"] - 1:e["end_line"]] = new
        out = "\n".join(lines) + ("\n" if trailing else "")
        if out == text:
            errors.append(f"{path}: the edits change nothing")
        results[path] = out
    if errors:
        return errors
    for path, out in results.items():
        ctx.sandbox.write_file(f"{REPO_DIR}/{path}", out)
    return []


SYNTAX_CHECK = ("import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1])")


def syntax_errors(ctx: Context, paths: list[str]) -> list[str]:
    """After a dry-run apply: does every edited file still parse? A wrong indentation in a replacement is
    the most common model mistake (seen live, D54), and this names the line instead of leaving VERIFY to
    report an opaque collection error. The message includes the lines around the problem."""
    errors = []
    for path in paths:
        res = ctx.sh(f'cd {REPO_DIR} && {PYTHON} -c "{SYNTAX_CHECK}" {path}')
        if res.exit_code == 0:
            continue
        err = (res.stderr or res.stdout).strip().splitlines()
        what = err[-1] if err else "SyntaxError"
        m = re.search(r"line (\d+)", res.stderr or "")
        context = ""
        if m:
            n = int(m.group(1))
            try:
                lines = ctx.sandbox.read_file(f"{REPO_DIR}/{path}").split("\n")
                context = " Lines around it:\n" + "\n".join(
                    f"{i:>5}| {lines[i - 1].rstrip(chr(13))}" for i in range(max(1, n - 3), min(len(lines), n + 2) + 1))
            except Exception:
                pass
        errors.append(f"your edit makes {path} invalid Python ({what}). Check the indentation of your replacement: "
                      f"it must line up with the surrounding code.{context}")
    return errors


def repo_diff(ctx: Context) -> str:
    return ctx.sh(f"cd {REPO_DIR} && git diff --no-color").stdout


def reset_repo(ctx: Context, remove: str | None = None) -> None:
    """Back to the checked-out commit: undo edits to tracked files and remove our one added test file.
    (No `git clean`: it could delete build metadata the editable install needs.)"""
    cmd = f"cd {REPO_DIR} && git checkout -q -- ."
    if remove:
        cmd += f" && rm -f {REPO_DIR}/{remove}"
    ctx.sh(cmd)
