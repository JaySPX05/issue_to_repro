"""SETUP: clone the repo at the right version and build a working test environment.

Heuristics first, model as fallback. Attempt 1 uses a fixed install plan built from the files in
the repo (no model call). Only if that fails does the `super` model read the error and propose
the next commands, until the attempt budget is spent.

Success = `pytest --collect-only` can run (exit 0, or 5 = no tests found). Then the sandbox is
checkpointed and the snapshot ID is stored in run.env["snapshot"] so REPRO, FIX and VERIFY can
all start from the same clean environment.

Transitions: ready -> REPRO.  Cannot clone / check out / install -> outcome SETUP_FAILED, DONE."""
from __future__ import annotations

import json
import re
import shlex

from itr.agent.budget import spend_attempt
from itr.agent.context import Context, clip
from itr.agent.paths import PIP, PYTHON, REPO_DIR, VENV_DIR
from itr.agent.states import State
from itr.sandbox.base import ExecResult

CLONE_TIMEOUT = 600
INSTALL_TIMEOUT = 900
SMOKE_OK_CODES = (0, 5)          # pytest: 0 = collected fine, 5 = no tests found (still a usable env)

REQ_FILES = [
    "requirements-dev.txt", "dev-requirements.txt", "requirements-test.txt", "test-requirements.txt",
    "requirements/dev.txt", "requirements/test.txt", "requirements/tests.txt", "requirements.txt",
]

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "give_up": {"type": "boolean"},
        "reasoning": {"type": "string"},
        "commands": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
    "required": ["give_up", "reasoning", "commands"],
    "additionalProperties": False,
}

CONFIG_PEEK = (
    f"cd {REPO_DIR} && for f in pyproject.toml setup.cfg tox.ini setup.py; do "
    "[ -f \"$f\" ] && { echo \"=== $f ===\"; head -c 3000 \"$f\"; echo; }; done; true"
)


# ---------------------------------------------------------------- pure helpers (easy to test)
TEST_NAMES = ["tests", "test", "testing", "dev"]      # preference order for group / extra names


def declared_test_deps(pyproject_text: str) -> tuple[str | None, str | None]:
    """Find where a project declares its test dependencies. Returns (dependency_group, extra), each
    the first match in TEST_NAMES or None. Reads [dependency-groups] (PEP 735, what modern projects
    use) and [project.optional-dependencies]."""
    groups: set[str] = set()
    extras: set[str] = set()
    try:
        import tomllib
        data = tomllib.loads(pyproject_text)
        groups = set(data.get("dependency-groups", {}))
        extras = set(data.get("project", {}).get("optional-dependencies", {}))
    except Exception:                                  # Python 3.10 (no tomllib) or odd TOML: scan by hand
        section = None
        for line in pyproject_text.splitlines():
            head = re.match(r"^\[([^\]]+)\]\s*$", line.strip())
            if head:
                section = head.group(1).strip()
                continue
            key = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*\[", line)
            if key and section == "dependency-groups":
                groups.add(key.group(1))
            elif key and section == "project.optional-dependencies":
                extras.add(key.group(1))
    pick = lambda found: next((n for n in TEST_NAMES if n in found), None)   # noqa: E731
    return pick(groups), pick(extras)


def plan_install(files: list[str], pyproject_text: str = "") -> list[str]:
    """Fixed install plan from the repo's files. No model involved."""
    present = set(files)
    group, extra = declared_test_deps(pyproject_text) if pyproject_text else (None, None)
    cmds = [f"python3 -m venv {VENV_DIR}", f"{PIP} install -q -U pip"]
    if present & {"pyproject.toml", "setup.py", "setup.cfg"}:
        target = f'".[{extra}]"' if extra else "."
        cmds.append(f"cd {REPO_DIR} && {PIP} install -q -e {target}")
    if group:
        cmds.append(f"cd {REPO_DIR} && {PIP} install -q --group {group}")
    for name in REQ_FILES:
        if name in present:
            cmds.append(f"cd {REPO_DIR} && {PIP} install -q -r {name}")
    cmds.append(f"{PIP} install -q pytest")
    return cmds


def numeric_version(text: str | None) -> str | None:
    m = re.search(r"\d+(?:\.\d+)+", text or "")
    return m.group(0) if m else None


def pick_tag(tags: list[str], version: str) -> str | None:
    available = set(tags)
    for candidate in (f"v{version}", version, f"V{version}", f"release-{version}", f"release/{version}"):
        if candidate in available:
            return candidate
    return None


# ---------------------------------------------------------------- steps
def clone_repo(ctx: Context) -> ExecResult | None:
    """Blobless clone (fast, still has full history for tags and SHAs). One plain retry for network
    flakes. Returns the failing result, or None on success."""
    url = f"https://github.com/{ctx.run.issue['repo']}.git"
    res = None
    for _ in range(2):
        res = ctx.sh(f"git clone --filter=blob:none {shlex.quote(url)} {REPO_DIR}", timeout=CLONE_TIMEOUT)
        if res.exit_code == 0:
            return None
        ctx.sh(f"rm -rf {REPO_DIR}")
    return res


def checkout_ref(ctx: Context) -> tuple[str | None, str | None, ExecResult | None]:
    """Pick the version to test. A pinned ref (eval cases set issue["ref_pinned"]) is used as-is.
    Otherwise, if the issue claims a version and a matching tag exists, use that tag; else stay on
    the default branch. Returns (ref, note, failing_result)."""
    issue = ctx.run.issue
    ref, note = issue.get("ref"), None
    if not issue.get("ref_pinned"):
        version = numeric_version((ctx.run.triage or {}).get("claimed_version"))
        if version:
            tags = ctx.sh(f"cd {REPO_DIR} && git tag --list").stdout.split()
            tag = pick_tag(tags, version)
            if tag:
                ref, note = tag, f"tag {tag} matches the claimed version {version}"
            else:
                note = f"no tag matches claimed version {version}; using {ref or 'the default branch'}"
    if ref:
        res = ctx.sh(f"cd {REPO_DIR} && git checkout -q --detach {shlex.quote(ref)}")
        if res.exit_code != 0:
            return ref, note, res
    return ref, note, None


def run_commands(ctx: Context, commands: list[str]) -> tuple[str, ExecResult] | None:
    """Run commands in order; stop at the first failure and return (command, result)."""
    for cmd in commands:
        res = ctx.sh(cmd, timeout=INSTALL_TIMEOUT)
        if res.exit_code != 0:
            return cmd, res
    return None


def smoke_check(ctx: Context) -> ExecResult:
    return ctx.sh(f"cd {REPO_DIR} && {PYTHON} -m pytest --collect-only -q -p no:cacheprovider", timeout=300)


def ask_for_plan(ctx: Context, files: list[str], history: list[dict]) -> dict:
    payload = {
        "repo": ctx.run.issue["repo"],
        "paths": {"repo": REPO_DIR, "venv": VENV_DIR},
        "files_at_repo_root": sorted(f for f in files if "/" not in f)[:80],
        "config_files": clip(ctx.sh(CONFIG_PEEK).stdout, 9000),
        "previous_attempts": history[-3:],
    }
    messages = [{"role": "system", "content": ctx.prompt("setup")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    return ctx.model_json("super", messages, PLAN_SCHEMA, stage="setup").data


def _fail(ctx: Context, reason: str, res: ExecResult | None = None, **extra) -> State:
    detail = clip((res.stderr or res.stdout) if res else "", 1500)
    ctx.run.env = {"error": reason, "detail": detail, **extra}
    ctx.run.outcome = "SETUP_FAILED"
    ctx.artifact("environment_failed", ctx.run.env)
    return State.DONE


def _attempts_left(ctx: Context) -> int:
    b = ctx.run.budget
    return b["max_attempts"].get("setup", 1) - b["attempts"].get("setup", 0)


# ---------------------------------------------------------------- the stage
def setup(ctx: Context) -> State:
    failure = clone_repo(ctx)
    if failure:
        return _fail(ctx, "git clone failed", failure)

    ref, ref_note, failure = checkout_ref(ctx)
    if failure:
        return _fail(ctx, f"could not check out {ref!r}", failure)
    if ref_note:
        ctx.artifact("ref_choice", {"ref": ref, "note": ref_note})

    files = ctx.sh(f"cd {REPO_DIR} && git ls-files").stdout.splitlines()
    pyproject = ctx.sh(f"cat {REPO_DIR}/pyproject.toml").stdout if "pyproject.toml" in files else ""
    commands = plan_install(files, pyproject)
    history: list[dict] = []
    last_res: ExecResult | None = None

    while spend_attempt(ctx.run, "setup"):
        failed = run_commands(ctx, commands)
        if failed is None:
            smoke = smoke_check(ctx)
            if smoke.exit_code in SMOKE_OK_CODES:
                py = ctx.sh(f"{PYTHON} --version")
                ctx.run.env = {
                    "repo_dir": REPO_DIR, "venv": VENV_DIR,
                    "python": (py.stdout or py.stderr).strip(),
                    "ref": ref, "ref_note": ref_note, "install_commands": commands,
                    "attempts": ctx.run.budget["attempts"]["setup"],
                    "snapshot": ctx.sandbox.snapshot(),
                }
                ctx.artifact("environment", ctx.run.env)
                return State.REPRO
            failed = ("pytest --collect-only", smoke)

        cmd, last_res = failed
        history.append({"commands": commands, "failed_command": cmd, "exit_code": last_res.exit_code,
                        "output_tail": ((last_res.stderr or "") + (last_res.stdout or ""))[-2500:]})
        if _attempts_left(ctx) <= 0:
            break
        plan = ask_for_plan(ctx, files, history)
        ctx.artifact("setup_plan", plan)
        if plan["give_up"] or not plan["commands"]:
            break
        commands = plan["commands"]

    return _fail(ctx, "could not build a working environment", last_res, attempts=len(history))
