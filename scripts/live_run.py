"""Run the WHOLE agent (FETCH ... REPORT) on an eval case: real GitHub, real Nemotron models, real Nebius sandbox.

This is the first end-to-end test of FIX and VERIFY with real models. One run costs roughly 5-15 cents.

    pip install "contree-sdk==0.3.6" pyyaml
    python scripts/live_run.py tabulate-180
    python scripts/live_run.py cachetools-405 --max-cost 0.30

Needs in .env: NEBIUS_API_KEY, NEBIUS_PROJECT_ID, MODEL_NANO/SUPER/ULTRA, GITHUB_TOKEN (recommended).
Requires the real LLM client (src/itr/llm.py from the main branch): `git merge origin/main` first.
Saves everything to live_runs/<case>.json (gitignored) so a result can be inspected without paying again."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from itr import config  # noqa: E402
from itr.agent.budget import init_budget  # noqa: E402
from itr.agent.context import Context  # noqa: E402
from itr.agent.machine import run_machine  # noqa: E402
from itr.models import Run  # noqa: E402

OUT = ROOT / "live_runs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", help="case id from eval/cases.yaml, e.g. tabulate-180")
    ap.add_argument("--max-cost", type=float, default=0.25, help="dollar cap for the run (default 0.25)")
    ap.add_argument("--sandbox", choices=["nebius", "local"], default="nebius")
    args = ap.parse_args()

    try:
        import yaml
    except ImportError:
        raise SystemExit("pip install pyyaml")
    config.load_env(ROOT / ".env")
    for name in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID", "MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA"):
        if not config.get(name):
            raise SystemExit(f"Set {name} in .env first.")
    from itr import llm
    cases = {c["id"]: c for c in yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]}
    if args.case not in cases:
        raise SystemExit(f"unknown case {args.case!r}; choose from {', '.join(cases)}")
    case = cases[args.case]

    from itr.sandbox.nebius import NebiusSandbox
    sandbox = NebiusSandbox()

    events: list[dict] = []
    t0 = time.time()

    def emit(kind: str, payload: dict) -> None:
        events.append({"t": round(time.time() - t0, 1), "type": kind, "payload": payload})
        if kind == "stage_started":
            print(f"[{time.time() - t0:5.0f}s] {payload['stage']}", flush=True)
        elif kind == "stage_finished" and payload.get("error"):
            print(f"        ERROR: {payload['error']}", flush=True)
        elif kind == "model_call":
            print(f"        model {payload['tier']:5} {payload['tokens_in']:>6} in {payload['tokens_out']:>6} out "
                  f"{payload['latency_ms'] / 1000:5.1f}s", flush=True)
        elif kind == "artifact" and payload["name"] in ("repro_result", "fix_plan", "verify_failed",
                                                        "verify_passed", "repro_gave_up"):
            c = payload["content"]
            short = {k: (str(v)[:160] if not isinstance(v, (int, float, bool, list)) else v)
                     for k, v in c.items() if k != "patch"}
            errs = short.pop("plan_errors", None)
            print(f"        {payload['name']}: {short}", flush=True)
            for err in errs or []:
                print(f"          plan error: {err[:300]}", flush=True)
            if c.get("patch"):
                print("          patch:\n" + "\n".join("            " + ln for ln in c["patch"].splitlines()[:30]), flush=True)

    run = Run(id=args.case, issue={"url": case["url"], "repo": case["repo"], "ref": case["ref"],
                                   "ref_pinned": True, "as_of": case["as_of"]})
    init_budget(run, max_cost_usd=args.max_cost)
    ctx = Context(run=run, sandbox=sandbox, llm_call=llm.call, emit_fn=emit)
    run_machine(ctx)

    b = run.budget
    print(f"\nOUTCOME {run.outcome}   expected {case['expected_outcome']}   "
          f"cost ${b['cost_usd']:.3f}   tokens {b['tokens_used']}   {time.time() - t0:.0f}s")
    fx = run.fix or {}
    if fx.get("patch"):
        print("\n--- patch\n" + fx["patch"])
        print("--- regression test (" + str(fx.get("test_path")) + ")\n" + fx.get("regression_test", ""))
        print(f"--- compare with the maintainers' real fix: {case['repo']} commit {case['fix_sha']}")
    report = [e["payload"]["content"] for e in events if e["type"] == "artifact" and e["payload"]["name"] == "report"]
    if report:
        print("\n--- drafted comment\n" + report[-1]["title"] + "\n\n" + report[-1]["comment"])
    OUT.mkdir(exist_ok=True)
    (OUT / f"{args.case}.json").write_text(json.dumps(
        {"case": case, "outcome": run.outcome, "budget": b, "triage": run.triage, "env": run.env,
         "repro": run.repro, "verdict": run.verdict, "fix": run.fix, "events": events},
        indent=1, default=str), encoding="utf-8")
    print(f"\nsaved live_runs/{args.case}.json")
    return 0 if run.outcome == case["expected_outcome"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
