"""Shared by FIX and VERIFY: validating and applying a model's proposed change, in plain code.

The model proposes edits as exact find-and-replace pairs, not as a diff: models write diffs with wrong
line numbers and context, while "this exact text, once, becomes that text" is checked mechanically.
The rules below also stop a patch from "winning" by editing tests, test configuration or packaging
(decisions D49)."""
from __future__ import annotations

import re

from itr.agent.context import Context
from itr.agent.paths import REPO_DIR

MAX_EDITS = 5
MAX_SEARCH_CHARS = 4000
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
        if not e["search"].strip():
            errors.append(f"{e['path']}: empty search text")
        elif len(e["search"]) > MAX_SEARCH_CHARS:
            errors.append(f"{e['path']}: search text longer than {MAX_SEARCH_CHARS} characters")
        elif e["search"] == e["replace"]:
            errors.append(f"{e['path']}: replace is identical to search")
    return errors


def apply_edits(ctx: Context, edits: list[dict]) -> list[str]:
    """Apply every edit or none. Each search text must occur EXACTLY once in its file (at the moment it
    is applied, so a later edit can build on an earlier one). Returns error strings; empty means applied."""
    contents: dict[str, str] = {}
    errors: list[str] = []
    for e in edits:
        path = e["path"]
        try:
            text = contents[path] if path in contents else ctx.sandbox.read_file(f"{REPO_DIR}/{path}")
        except Exception as ex:
            errors.append(f"{path}: cannot read ({type(ex).__name__})")
            continue
        if "�" in text:
            errors.append(f"{path}: not valid UTF-8, refusing to rewrite it")
            continue
        search, replace = e["search"], e["replace"]
        if "\r\n" in text:                              # a Windows-line-ending file: match its style
            search = search.replace("\r\n", "\n").replace("\n", "\r\n")
            replace = replace.replace("\r\n", "\n").replace("\n", "\r\n")
        n = text.count(search)
        if n == 0:
            errors.append(f"{path}: the search text was not found (it must match the file exactly, "
                          f"including indentation): {search[:80]!r}")
        elif n > 1:
            errors.append(f"{path}: the search text matches {n} places; include more surrounding lines "
                          f"so it matches exactly one: {search[:80]!r}")
        else:
            contents[path] = text.replace(search, replace, 1)
    if errors:
        return errors
    for path, text in contents.items():
        ctx.sandbox.write_file(f"{REPO_DIR}/{path}", text)
    return []


def repo_diff(ctx: Context) -> str:
    return ctx.sh(f"cd {REPO_DIR} && git diff --no-color").stdout


def reset_repo(ctx: Context, remove: str | None = None) -> None:
    """Back to the checked-out commit: undo edits to tracked files and remove our one added test file.
    (No `git clean`: it could delete build metadata the editable install needs.)"""
    cmd = f"cd {REPO_DIR} && git checkout -q -- ."
    if remove:
        cmd += f" && rm -f {REPO_DIR}/{remove}"
    ctx.sh(cmd)
