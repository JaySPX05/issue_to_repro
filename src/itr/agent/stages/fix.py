"""FIX (super; ultra from the 3rd attempt): find the root cause, propose a minimal patch and a regression test.

Two model calls per attempt, each small and checkable:
  LOCATE  which source files matter (we show the model the file list, never the whole repo)
  PATCH   numbered line-range edits plus a new test file, shown only the relevant files (numbered)

FIX never trusts its own output. It validates the plan (paths, sizes, protected files), dry-applies the
edits to prove every line range is valid, stores the diff, resets the repo, and ALWAYS hands
over to VERIFY, which runs everything and decides (decisions D49-D51).

run.fix = {patch, regression_test, test_path, edits, root_cause, notes, plan_errors, history[],
           before_result, after_result}   (the last two are filled in by VERIFY)"""
from __future__ import annotations

import json
import re

from itr.agent.budget import attempts_used, spend_attempt
from itr.agent.context import Context, clip
from itr.agent.patching import (MAX_TEST_CHARS, PROTECTED, apply_edits, check_edits, repo_diff, reset_repo,
                                syntax_errors, test_path_error, tracked_files)
from itr.agent.paths import REPO_DIR
from itr.agent.states import State

ULTRA_FROM_ATTEMPT = 3         # attempts 1-2 use super; later ones escalate to ultra
MAX_FILES = 4
FILE_CHARS = 12000             # a file longer than this is shown as windows around the relevant lines
TOTAL_CHARS = 32000
WINDOW = 25                    # lines of context around a relevant line

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FILES},
        "search_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["reasoning", "files", "search_terms"],
    "additionalProperties": False,
}

EDIT_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"},
                   "end_line": {"type": "integer"}, "first_line": {"type": "string"}, "replace": {"type": "string"}},
    "required": ["path", "start_line", "end_line", "first_line", "replace"],
    "additionalProperties": False,
}

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "edits": {"type": "array", "items": EDIT_SCHEMA, "minItems": 1, "maxItems": 5},
        "test_path": {"type": "string"},
        "test_code": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["root_cause", "edits", "test_path", "test_code", "notes"],
    "additionalProperties": False,
}

TRACE_FILE = re.compile(r'File "' + re.escape(REPO_DIR) + r'/([^"]+\.py)", line (\d+)')


# ---------------------------------------------------------------- pure helpers
def traceback_locations(output: str) -> dict[str, list[int]]:
    """Files (relative to the repo) and line numbers named by a traceback."""
    found: dict[str, list[int]] = {}
    for path, line in TRACE_FILE.findall(output or ""):
        found.setdefault(path, []).append(int(line))
    return found


def render_file(text: str, lines_of_interest: list[int], terms: list[str], limit: int = FILE_CHARS) -> str:
    """A numbered view ("  12| code"). The whole file when it is short; otherwise only windows around the
    traceback lines and the lines that mention a search term, with the gaps marked. The numbers are the
    ones the model must use in its edits. Lines are split on "\\n" only, exactly as apply_edits does."""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()

    def fmt(i: int) -> str:
        return f"{i:>5}| {lines[i - 1]}"

    if len(text) <= limit:
        return "\n".join(fmt(i) for i in range(1, len(lines) + 1))
    keep: set[int] = set()
    for n in lines_of_interest:
        keep.update(range(max(1, n - WINDOW), min(len(lines), n + WINDOW) + 1))
    for term in terms:
        if not term:
            continue
        for i, ln in enumerate(lines, 1):
            if term in ln:
                keep.update(range(max(1, i - 3), min(len(lines), i + WINDOW) + 1))
                if len(keep) * 40 > limit * 3:
                    break
    if not keep:
        keep = set(range(1, min(len(lines), 120) + 1))
    out, prev = [], 0
    for i in sorted(keep):
        if prev != i - 1:
            out.append(f"      ... [lines {prev + 1}-{i - 1} omitted] ...")
        out.append(fmt(i))
        prev = i
    if prev < len(lines):
        out.append(f"      ... [lines {prev + 1}-{len(lines)} omitted] ...")
    return clip("\n".join(out), limit)


def source_candidates(tracked: set[str]) -> list[str]:
    return sorted(p for p in tracked if p.endswith(".py") and not PROTECTED.search(p))


def order_candidates(candidates: list[str], trace: dict[str, list[int]]) -> list[str]:
    """Files named by the traceback first, so the 200-file cap in the LOCATE prompt can never hide them."""
    return sorted(candidates, key=lambda p: (p not in trace, p))


def plan_errors(plan: dict, tracked: set[str]) -> list[str]:
    errors = check_edits(plan["edits"], tracked)
    bad = test_path_error(plan["test_path"], tracked)
    if bad:
        errors.append(bad)
    code = plan["test_code"]
    if not re.search(r"^\s*(async\s+)?def test_", code, re.M):
        errors.append("test_code defines no test function (def test_...)")
    if len(code) > MAX_TEST_CHARS:
        errors.append(f"test_code longer than {MAX_TEST_CHARS} characters")
    return errors


# ---------------------------------------------------------------- steps
def locate(ctx: Context, candidates: list[str], trace: dict[str, list[int]]) -> tuple[list[str], list[str]]:
    repro = ctx.run.repro or {}
    payload = {
        "title": ctx.run.issue.get("title", ""),
        "body": clip(ctx.run.issue.get("body", ""), 4000),
        "repro": {"script": clip(repro.get("script", ""), 2500), "output": clip(repro.get("last_output", ""), 2500)},
        "judge": {k: (ctx.run.verdict or {}).get(k) for k in ("reported_symptom", "observed_symptom")},
        "traceback_files": sorted(trace),
        "source_files": candidates[:200],
    }
    messages = [{"role": "system", "content": ctx.prompt("locate")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    plan = ctx.model_json("super", messages, LOCATE_SCHEMA, stage="fix").data
    ctx.artifact("fix_locate", plan)
    files = [f for f in plan["files"] if f in candidates]
    for f in sorted(trace):                       # a file the traceback names is always worth reading
        if f in candidates and f not in files:
            files.append(f)
    return files[:MAX_FILES], plan["search_terms"]


def read_files(ctx: Context, files: list[str], trace: dict[str, list[int]], terms: list[str]) -> dict[str, str]:
    shown, budget_left = {}, TOTAL_CHARS
    for path in files:
        try:
            text = ctx.sandbox.read_file(f"{REPO_DIR}/{path}")
        except Exception:
            continue
        rendered = render_file(text, trace.get(path, []), terms, limit=min(FILE_CHARS, budget_left))
        shown[path] = rendered
        budget_left -= len(rendered)
        if budget_left <= 500:
            break
    return shown


def test_style_example(ctx: Context, tracked: set[str]) -> dict:
    tests = sorted(p for p in tracked if re.search(r"(^|/)test_[^/]*\.py$", p))
    if not tests:
        return {"path": None, "text": "(the project has no tests; use plain pytest functions)"}
    path = min(tests, key=lambda p: (p.count("/"), len(p)))
    try:
        return {"path": path, "text": clip(ctx.sandbox.read_file(f"{REPO_DIR}/{path}"), 1800)}
    except Exception:
        return {"path": path, "text": ""}


def history_for_prompt(history: list[dict]) -> list[dict]:
    return [{"root_cause": h.get("root_cause", ""), "edits": [
                {"path": e["path"], "start_line": e["start_line"], "end_line": e["end_line"],
                 "replace": clip(e["replace"], 500)} for e in h.get("edits", [])],
             "test_path": h.get("test_path"), "test_code": clip(h.get("test_code", ""), 1500),
             "failure": h.get("failure", "")} for h in history[-3:]]


# ---------------------------------------------------------------- the stage
def fix(ctx: Context) -> State:
    previous = ctx.run.fix or {}
    history: list[dict] = list(previous.get("history", []))
    if not spend_attempt(ctx.run, "fix"):
        ctx.run.outcome = "NEEDS_MAINTAINER"
        ctx.run.fix = {**previous, "history": history, "plan_errors": ["no fix attempts left"]}
        return State.REPORT
    attempt = attempts_used(ctx.run, "fix")
    tier = "ultra" if attempt >= ULTRA_FROM_ATTEMPT else "super"

    tracked = tracked_files(ctx)
    repro = ctx.run.repro or {}
    trace = traceback_locations(repro.get("last_output", ""))
    candidates = order_candidates(source_candidates(tracked), trace)
    files, terms = locate(ctx, candidates, trace)
    shown = read_files(ctx, files, trace, terms)

    b = ctx.run.budget
    left = b["max_attempts"].get("fix", 1) - b["attempts"].get("fix", 0) + 1
    payload = {
        "title": ctx.run.issue.get("title", ""),
        "body": clip(ctx.run.issue.get("body", ""), 5000),
        "comments": [clip(c.get("body", "") if isinstance(c, dict) else str(c), 1200)
                     for c in ctx.run.issue.get("comments", [])[:6]],
        "triage": ctx.run.triage,
        "repro": {"script": clip(repro.get("script", ""), 3000), "python_flags": repro.get("python_flags", []),
                  "output": clip(repro.get("last_output", ""), 2500)},
        "judge": {k: (ctx.run.verdict or {}).get(k) for k in ("reported_symptom", "observed_symptom", "reasoning")},
        "files": shown,
        "test_style": test_style_example(ctx, tracked),
        "attempts_left_including_this_one": left,
        "previous_attempts": history_for_prompt(history),
    }
    messages = [{"role": "system", "content": ctx.prompt("fix")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    plan = ctx.model_json(tier, messages, FIX_SCHEMA, stage="fix").data

    errors = plan_errors(plan, tracked)
    patch = ""
    if not errors:                                # dry run: prove every edit applies, keep the diff, undo
        errors = apply_edits(ctx, plan["edits"])
        if not errors:
            errors = syntax_errors(ctx, sorted({e["path"] for e in plan["edits"]}))
        if not errors:
            patch = repo_diff(ctx)
            for h in history:
                if h.get("patch") == patch and h.get("test_code") == plan["test_code"]:
                    errors = [f"this patch AND test are identical to the ones in attempt {h['attempt']}, which failed "
                              f"({clip(h.get('failure', ''), 300)}). Do something different."]
                    patch = ""
                    break
        reset_repo(ctx)
    entry = {"attempt": attempt, "tier": tier, "root_cause": plan["root_cause"], "edits": plan["edits"],
             "test_path": plan["test_path"], "test_code": plan["test_code"], "notes": plan["notes"],
             "plan_errors": errors, "failure": "", "patch": patch}
    history.append(entry)
    ctx.run.fix = {"patch": patch, "regression_test": plan["test_code"], "test_path": plan["test_path"],
                   "edits": plan["edits"], "root_cause": plan["root_cause"], "notes": plan["notes"],
                   "plan_errors": errors, "history": history, "before_result": None, "after_result": None}
    ctx.artifact("fix_plan", {"attempt": attempt, "tier": tier, "root_cause": plan["root_cause"],
                              "patch": patch, "test_path": plan["test_path"], "plan_errors": errors})
    return State.VERIFY
