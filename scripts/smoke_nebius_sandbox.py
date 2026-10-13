"""Run our REAL agent stages (SETUP then REPRO) inside the REAL Nebius Sandbox.

The model is replaced by a fixed "oracle" that returns a known-good script, so this tests the
sandbox adapter and the stages together, not the model. For two eval cases it checks that:
  - SETUP clones the pinned commit, builds a venv and installs the project in the sandbox;
  - REPRO's script FAILS on the buggy commit (click-2877: a BytesWarning; tabulate-399: a hang that the
    30 s timeout must catch, which exercises the adapter's timeout path), and PASSES on the fix;
  - snapshot()/restore() roll the sandbox back to the state SETUP saved.

    pip install "contree-sdk==0.3.6" pyyaml
    python scripts/smoke_nebius_sandbox.py                 # both cases, about 3 minutes
    python scripts/smoke_nebius_sandbox.py click-2877      # one case

Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID in .env. Free while Sandboxes are in beta."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from itr import config  # noqa: E402
from itr.agent.budget import init_budget  # noqa: E402
from itr.agent.paths import REPO_DIR, SCRIPT_DIR  # noqa: E402
from itr.agent.stages.repro import repro  # noqa: E402
from itr.agent.stages.setup import setup  # noqa: E402
from itr.agent.states import State  # noqa: E402
from itr.llm import LLMResponse  # noqa: E402
from itr.sandbox.nebius import NebiusSandbox  # noqa: E402
from tests.fakes import make_ctx  # noqa: E402

ORACLE = {
    "click-2877": dict(
        script="import click\nconvert = click.Path(allow_dash=True).convert\nfor value in ('-', '', b'-', b''):\n"
               "    convert(value, None, None)\nprint('ok')\n", flags=["-bb"], packages=[], expect="failed"),
    "tabulate-399": dict(
        script='from tabulate import _CustomTextWrap\nlines = _CustomTextWrap(width=1).wrap("\\ud55c\\uae00\\ud14c\\uc2a4\\ud2b8")\n'
               'print(lines)\nassert all(len(l) <= 1 for l in lines)\n', flags=[], packages=["wcwidth"], expect="hang"),
}


class Oracle:
    def __init__(self, case):
        self.o = ORACLE[case]

    def __call__(self, tier, messages, schema=None):
        sysmsg = messages[0]["content"]
        if sysmsg.startswith("You write a short Python script"):
            data = {"reasoning": "oracle", "script": self.o["script"], "python_flags": self.o["flags"],
                    "extra_packages": self.o["packages"], "expected_symptom": "oracle"}
        elif sysmsg.startswith("You fix the environment"):
            data = {"give_up": True, "reasoning": "oracle gives up", "commands": []}
        else:
            raise AssertionError(sysmsg[:40])
        return LLMResponse(data, 10, 10, 1, tier, "oracle")


def check(ok: bool, text: str) -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {text}")
    return ok


def run_case(case: dict) -> bool:
    cid = case["id"]
    print(f"\n=== {cid}")
    sb = NebiusSandbox()
    ctx, _ = make_ctx(issue={"url": case["url"], "repo": case["repo"], "title": "T", "body": "B", "comments": [],
                             "ref": case["ref"], "ref_pinned": True}, sandbox=sb, llm=Oracle(cid))
    init_budget(ctx.run, max_attempts={"repro": 1})
    ctx.run.triage = {"kind": "bug", "claimed_version": None, "steps": [], "expected": "", "actual": "", "missing_info": []}
    good = True
    sb.create()
    t0 = time.time()
    nxt = setup(ctx)
    good &= check(nxt is State.REPRO, f"SETUP -> {nxt.value} in {time.time() - t0:.0f}s   env={ {k: v for k, v in (ctx.run.env or {}).items() if k in ('ref_note', 'warnings', 'tests_collect_ok')} }")
    if nxt is not State.REPRO:
        print("   ", ctx.run.env)
        return False
    snapshot = ctx.run.env["snapshot"]
    t0 = time.time()
    nxt = repro(ctx)
    kind = ctx.run.repro["classification"]
    good &= check(kind == ORACLE[cid]["expect"] and nxt is State.VERDICT,
                  f"REPRO on the buggy commit -> {kind} ({time.time() - t0:.0f}s), expected {ORACLE[cid]['expect']}")
    print("    " + ctx.run.repro["last_output"].strip().replace("\n", "\n    ")[:300])
    sb.exec(f"cd {REPO_DIR} && git checkout -q --detach {case['fix_sha']}")
    init_budget(ctx.run, max_attempts={"repro": 1})
    ctx.run.repro = ctx.run.verdict = ctx.run.outcome = None
    repro(ctx)
    good &= check(ctx.run.repro["classification"] == "passed", f"same script on the fixed commit -> {ctx.run.repro['classification']}, expected passed")
    # roll back to the state SETUP saved: the repo is there, our repro script is not
    sb.restore(snapshot)
    still_there = sb.exec(f"test -d {REPO_DIR}/.git && echo repo-ok").stdout.strip()
    script_gone = sb.exec(f"test -e {SCRIPT_DIR}/repro_1.py && echo present || echo gone").stdout.strip()
    head = sb.exec(f"cd {REPO_DIR} && git rev-parse HEAD").stdout.strip()
    good &= check(still_there == "repo-ok" and script_gone == "gone" and head == case["ref"],
                  f"restore(): repo={still_there}, repro script={script_gone}, HEAD back on the buggy commit={head == case['ref']}")
    sb.destroy()
    return bool(good)


def main() -> int:
    try:
        import yaml
    except ImportError:
        raise SystemExit("pip install pyyaml")
    config.load_env(ROOT / ".env")
    for name in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID"):
        if not config.get(name):
            raise SystemExit(f"Set {name} in .env first.")
    cases = {c["id"]: c for c in yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]}
    wanted = sys.argv[1:] or list(ORACLE)
    results = {cid: run_case(cases[cid]) for cid in wanted}
    sb = NebiusSandbox()
    sb.create()
    print("\n=== adapter contract")
    t0 = time.time()
    r = sb.exec("sleep 60", timeout=5)
    ok = check(r.timed_out and r.exit_code == -1, f"timeout -> timed_out={r.timed_out} after {time.time() - t0:.0f}s")
    ok &= check(sb.exec("echo still-works").stdout.strip() == "still-works", "sandbox usable after a timeout")
    results["contract"] = bool(ok)
    print("\nSUMMARY ", {k: ("OK" if v else "FAIL") for k, v in results.items()})
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
