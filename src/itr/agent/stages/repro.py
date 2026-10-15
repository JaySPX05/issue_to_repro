"""REPRO (super): write a minimal script that demonstrates the reported bug, run it, read the result.

The convention that makes this checkable: the script asserts the CORRECT behaviour, so it FAILS
(exception, assertion, or hang past the timeout) while the bug exists and exits 0 when it does not.
Running it is mechanical; judging whether it failed for the RIGHT reason is VERDICT's job, a
separate model call (see decisions D5).

Each attempt ends in one of four kinds, decided by code and not by a model:
  failed        non-zero exit             -> candidate reproduction, go to VERDICT
  hang          killed by the timeout     -> candidate reproduction, go to VERDICT
  passed        exit 0                    -> the bug did not show; feed that back and retry
  script_error  SyntaxError in our script -> our mistake, not evidence; feed back and retry

Re-entry: VERDICT may send the run back here when the failure was for the wrong reason. The history
of attempts and the verdict's feedback are then part of the next prompt. All attempts share the
`repro` budget. Out of attempts -> outcome NOT_REPRODUCED, DONE.

run.repro = {script, python_flags, run_command, last_output, attempts, history[], installed[],
             classification, expected_symptom}   (`history`, `installed`, `classification` extend the
             shared contract; `script`, `last_output`, `attempts` are the agreed fields.)"""
from __future__ import annotations

import json
import re
import shlex

from itr.agent.budget import spend_attempt
from itr.agent.context import Context, clip
from itr.agent.paths import PIP, PYTHON, REPO_DIR, SCRIPT_DIR
from itr.agent.states import State
from itr.sandbox.base import ExecResult

SCRIPT_TIMEOUT = 30           # seconds; a run that exceeds it is a hang, which counts as reproducing one
PIP_TIMEOUT = 300
MAX_PACKAGES = 3
MAX_SCRIPT_CHARS = 8000

# Only these interpreter flags are accepted: the value is spliced into a shell command, so it is an enum.
FLAGS = ["-b", "-bb", "-O", "-OO", "-X dev", "-X utf8", "-W error", "-W default"]
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}(\[[A-Za-z0-9,_-]+\])?([<>=!~]=?[A-Za-z0-9.*+!_-]+)?$")

REPRO_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "script": {"type": "string"},
        "python_flags": {"type": "array", "items": {"enum": FLAGS}, "maxItems": 3},
        "extra_packages": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_PACKAGES},
        "expected_symptom": {"type": "string"},
    },
    "required": ["reasoning", "script", "python_flags", "extra_packages", "expected_symptom"],
    "additionalProperties": False,
}

LAYOUT_CMD = (f"cd {REPO_DIR} && git ls-files '*.py' | grep -vE '(^|/)(tests?|docs?|examples?|benchmarks?)/' "
              "| head -60")


# ---------------------------------------------------------------- pure helpers (easy to test)
def unescape_newlines(script: str) -> str:
    """Some models return the whole script on one line with literal backslash-n sequences (seen with
    the real Super model on tabulate-180). A real script has line breaks; a one-liner full of `\\n`
    never does, so in that one case only, turn the literal sequences into real newlines."""
    if "\n" not in script and script.count("\\n") >= 2:
        return script.replace("\\n", "\n")
    return script


def classify(res: ExecResult, script_path: str) -> str:
    if res.timed_out:
        return "hang"
    if res.exit_code == 0:
        return "passed"
    err = (res.stderr or "").strip().splitlines()
    if err and err[-1].startswith(("SyntaxError", "IndentationError", "TabError")) and script_path in res.stderr:
        return "script_error"
    return "failed"


def format_output(res: ExecResult, kind: str) -> str:
    head = f"exit_code={res.exit_code}"
    if kind == "hang":
        head += f" (killed: no result after {SCRIPT_TIMEOUT}s, so the call never returned)"
    return f"{head}\n--- stdout ---\n{clip(res.stdout or '', 2000)}\n--- stderr ---\n{clip(res.stderr or '', 2500)}"


def valid_packages(names: list[str]) -> tuple[list[str], list[str]]:
    """Split model-suggested package specs into (acceptable, rejected). Names are spliced into a
    shell command, so anything that is not a plain requirement spec is refused."""
    ok, bad = [], []
    for n in names:
        (ok if PACKAGE_RE.match(n or "") else bad).append(n)
    return ok[:MAX_PACKAGES], bad


def run_command(script_path: str, flags: list[str]) -> str:
    """Run from the repo with the repo on the import path, so the script tests the checked-out code
    and not some other installed copy of the library."""
    flag_text = (" " + " ".join(flags)) if flags else ""
    return f"cd {REPO_DIR} && PYTHONPATH={REPO_DIR}:{REPO_DIR}/src {PYTHON}{flag_text} {script_path}"


def optional_dependencies(pyproject_text: str) -> dict[str, list[str]]:
    """The library's declared optional extras ({extra: [requirement names]}), minus the ones that only
    serve development. A bug that needs an optional package (tabulate-399: wide characters need wcwidth)
    never mentions it in the issue; the project's own metadata does (D58)."""
    try:
        import tomllib
        extras = tomllib.loads(pyproject_text).get("project", {}).get("optional-dependencies", {})
    except Exception:
        return {}
    skip = {"test", "tests", "testing", "dev", "docs", "doc", "lint", "typing", "all"}
    out: dict[str, list[str]] = {}
    for name, reqs in extras.items():
        if name.lower() in skip or not isinstance(reqs, list):
            continue
        names = [re.split(r"[<>=!~;\[ ]", str(r), maxsplit=1)[0] for r in reqs[:5]]
        out[name] = [n for n in names if n]
        if len(out) >= 8:
            break
    return out


# ---------------------------------------------------------------- steps
def build_payload(ctx: Context, history: list[dict], source_files: str, attempts_left: int,
                  optional: dict[str, list[str]] | None = None) -> dict:
    issue, env = ctx.run.issue, ctx.run.env or {}
    comments = [(c.get("body", "") if isinstance(c, dict) else str(c)) for c in issue.get("comments", [])[:10]]
    payload = {
        "title": issue.get("title", ""),
        "body": clip(issue.get("body", ""), 6000),
        "comments": [clip(c, 1500) for c in comments],
        "triage": ctx.run.triage,
        "python": env.get("python"),
        "checked_out": {"ref": env.get("ref"), "note": env.get("ref_note")},
        "source_files": source_files.strip().splitlines(),
        "optional_dependencies": optional or {},
        "attempts_left_including_this_one": attempts_left,
        "previous_attempts": [
            {"script": h["script"], "python_flags": h["python_flags"], "extra_packages": h["extra_packages"],
             "result": h["classification"], "output": clip(h["output"], 1800),
             "package_install_errors": h.get("package_install_errors", [])}
            for h in history[-3:]
        ],
    }
    verdict = ctx.run.verdict
    if verdict and verdict.get("matches_issue") is False:
        payload["judge_feedback"] = verdict.get("reasoning", "")
    return payload


def install_packages(ctx: Context, wanted: list[str], installed: list[str]) -> tuple[list[str], list[str]]:
    """Install extra packages (an optional dependency the bug needs). Returns (newly_installed, errors)."""
    ok, bad = valid_packages(wanted)
    errors = [f"refused package spec {b!r}" for b in bad]
    new: list[str] = []
    for pkg in ok:
        if pkg in installed:
            continue
        res = ctx.sh(f"{PIP} install -q {shlex.quote(pkg)}", timeout=PIP_TIMEOUT)
        if res.exit_code == 0:
            new.append(pkg)
        else:
            errors.append(f"pip install {pkg} failed: {clip(res.stderr or res.stdout, 300)}")
    return new, errors


def restore_tracked_files(ctx: Context) -> None:
    """A repro script must not edit the repository (VERIFY relies on pristine code). Scripts live
    outside the repo, but a careless one could still write into it, so undo any change to tracked files."""
    dirty = ctx.sandbox.exec(f"cd {REPO_DIR} && git status --porcelain --untracked-files=no").stdout.strip()
    if dirty:
        ctx.artifact("repo_modified_by_script", {"changes": dirty[:500]})
        ctx.sh(f"cd {REPO_DIR} && git checkout -q -- .")


# ---------------------------------------------------------------- the stage
def repro(ctx: Context) -> State:
    previous = ctx.run.repro or {}
    history: list[dict] = list(previous.get("history", []))
    installed: list[str] = list(previous.get("installed", []))
    source_files = ctx.sh(LAYOUT_CMD).stdout
    ctx.sh(f"mkdir -p {SCRIPT_DIR}")
    optional = optional_dependencies(ctx.sh(f"cat {REPO_DIR}/pyproject.toml 2>/dev/null").stdout)

    while spend_attempt(ctx.run, "repro"):
        b = ctx.run.budget
        left = b["max_attempts"].get("repro", 1) - b["attempts"].get("repro", 0) + 1
        payload = build_payload(ctx, history, source_files, left, optional)
        messages = [{"role": "system", "content": ctx.prompt("repro")},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        plan = ctx.model_json("super", messages, REPRO_SCHEMA, stage="repro").data

        script = unescape_newlines(plan["script"])
        if not script.strip() or len(script) > MAX_SCRIPT_CHARS:      # unusable: costs the attempt, not a crash
            history.append({"script": clip(script, 500), "python_flags": [], "extra_packages": [],
                            "classification": "script_error",
                            "output": "the script was empty or longer than the limit; write a short one"})
            continue
        flags = plan["python_flags"]
        new_pkgs, pkg_errors = install_packages(ctx, plan["extra_packages"], installed)
        installed += new_pkgs

        path = f"{SCRIPT_DIR}/repro_{len(history) + 1}.py"
        ctx.sandbox.write_file(path, script)
        cmd = run_command(path, flags)
        ctx.artifact("repro_script", {"path": path, "script": script, "python_flags": flags,
                                      "extra_packages": plan["extra_packages"],
                                      "expected_symptom": plan["expected_symptom"],
                                      "reasoning": plan["reasoning"]})
        res = ctx.sh(cmd, timeout=SCRIPT_TIMEOUT)
        restore_tracked_files(ctx)

        kind = classify(res, path)
        output = format_output(res, kind)
        history.append({"script": script, "python_flags": flags, "extra_packages": plan["extra_packages"],
                        "classification": kind, "output": output, "expected_symptom": plan["expected_symptom"],
                        "package_install_errors": pkg_errors})
        ctx.run.repro = {"script": script, "python_flags": flags, "run_command": cmd, "last_output": output,
                         "attempts": len(history), "history": history, "installed": installed,
                         "classification": kind, "expected_symptom": plan["expected_symptom"]}
        ctx.artifact("repro_result", {"classification": kind, "exit_code": res.exit_code, "output": output})
        if kind in ("failed", "hang"):
            return State.VERDICT

    last = history[-1]["classification"] if history else "never ran"
    ctx.run.repro = {**(ctx.run.repro or {"script": "", "last_output": "", "attempts": 0}),
                     "history": history, "installed": installed, "classification": last}
    ctx.run.outcome = "NOT_REPRODUCED"
    ctx.artifact("repro_gave_up", {"attempts": len(history), "last_result": last})
    return State.DONE
