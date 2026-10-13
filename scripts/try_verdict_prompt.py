"""Measure the VERDICT judge against labelled scripts, using the real Ultra model.

`eval/verdict_cases.json` holds 11 repro attempts that were really run on the buggy commits of the
eval cases (5 correct ones, 6 that fail for the wrong reason: import typos, a missing argument, a
script that fails before AND after the fix, a Windows-only bug on Linux, a self-inflicted timeout).
Each has a label: "accept" (the judge should say it reproduces the issue) or "reject".

For each one this script reads the real issue from GitHub (comments written after the fix hidden),
sends the same prompt and payload the agent uses to the ultra model, applies the same decision rule
as the agent, and prints a table. The number that matters most is FALSE ACCEPTS: a wrong script the
judge approved. Nothing here runs generated code.

    pip install pyyaml
    python scripts/try_verdict_prompt.py            # all 11
    python scripts/try_verdict_prompt.py c3186-model
    python scripts/try_verdict_prompt.py --save verdicts.json

Needs NEBIUS_API_KEY and MODEL_ULTRA (and GITHUB_TOKEN) in .env. Costs a few cents in total.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import try_repro_prompt as tr  # noqa: E402

from itr import config, github  # noqa: E402
from itr.agent.stages.verdict import VERDICT_SCHEMA, build_payload, decide  # noqa: E402
from itr.models import Run  # noqa: E402

tr.MAX_TOKENS["ultra"] = 16000          # reasoning tokens count toward this (decisions D24)


def judge(fx: dict, case: dict, issue: dict) -> dict:
    run = Run(id=fx["id"], issue=issue)
    run.triage = {}
    run.env = {"python": "Python 3.12", "ref": case["ref"], "ref_note": "pinned to the commit before the fix"}
    run.repro = {"script": fx["script"], "python_flags": fx["python_flags"], "installed": fx["installed"],
                 "classification": fx["classification"], "attempts": 1, "expected_symptom": fx["claimed_symptom"],
                 "last_output": fx["output"]}
    ctx = SimpleNamespace(run=run, sh=lambda cmd, timeout=0: SimpleNamespace(stdout="Linux 6.1"))
    payload = build_payload(ctx)
    r = tr.chat("ultra", [{"role": "system", "content": (tr.PROMPTS / "verdict.md").read_text(encoding="utf-8")},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], VERDICT_SCHEMA)
    matches, why = decide(r["data"])
    return {"fixture": fx["id"], "label": fx["label"], "judge": "accept" if matches else "reject",
            "why_rejected": why, "response": r}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture", nargs="?", help="fixture id, e.g. c3186-model (default: all)")
    ap.add_argument("--save", metavar="FILE", help="write the raw results to a JSON file")
    args = ap.parse_args()
    try:
        import yaml
    except ImportError:
        raise SystemExit("pip install pyyaml  (needed to read eval/cases.yaml)")
    config.load_env(ROOT / ".env")
    cases = {c["id"]: c for c in yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]}
    fixtures = json.loads((ROOT / "eval" / "verdict_cases.json").read_text(encoding="utf-8"))
    if args.fixture:
        fixtures = [f for f in fixtures if f["id"] == args.fixture]
        if not fixtures:
            raise SystemExit("unknown fixture id")
    token, issues, results = config.get("GITHUB_TOKEN"), {}, []
    for fx in fixtures:
        case = cases[fx["case"]]
        if fx["case"] not in issues:
            issues[fx["case"]] = github.fetch_issue(case["url"], token, as_of=case.get("as_of"))
        res = judge(fx, case, issues[fx["case"]])
        results.append(res)
        d = res["response"]["data"]
        ok = res["judge"] == fx["label"]
        print(f"\n{'=' * 78}\n{fx['id']:18} label={fx['label']:6} judge={res['judge']:6} {'OK' if ok else '<<< WRONG'}"
              f"   ({res['response']['seconds']}s, {res['response']['tokens']} tokens)")
        print(f"  why this fixture: {fx['why']}")
        print(f"  reported:  {d['reported_symptom']}\n  observed:  {d['observed_symptom']}")
        print(f"  matches_symptom={d['failure_matches_symptom']}  would_pass_if_fixed={d['would_pass_if_fixed']}"
              f"  cannot_reproduce_in_sandbox={d['cannot_reproduce_in_sandbox']}  confidence={d['confidence']}")
        print(f"  reasoning: {d['reasoning']}")
        if d["feedback_for_next_attempt"]:
            print(f"  feedback:  {d['feedback_for_next_attempt']}")
    right = sum(r["judge"] == r["label"] for r in results)
    false_accepts = [r["fixture"] for r in results if r["judge"] == "accept" and r["label"] == "reject"]
    false_rejects = [r["fixture"] for r in results if r["judge"] == "reject" and r["label"] == "accept"]
    print(f"\n{'=' * 78}\nSUMMARY  correct {right}/{len(results)}")
    print(f"  FALSE ACCEPTS (wrong script approved, the dangerous kind): {false_accepts or 'none'}")
    print(f"  false rejects (good script refused, costs one retry):      {false_rejects or 'none'}")
    if args.save:
        Path(args.save).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"saved to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
