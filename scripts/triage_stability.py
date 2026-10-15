"""Run only FETCH + TRIAGE several times on eval cases and count the answers. Costs well under a cent per call.

    python scripts/triage_stability.py tabulate-241 click-1629 --n 6

Shows whether the triage label is stable, and whether the trap (click-1629, a feature request) stays "feature"."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from itr import config  # noqa: E402
from itr.agent.budget import init_budget  # noqa: E402
from itr.agent.context import Context  # noqa: E402
from itr.agent.machine import run_machine  # noqa: E402
from itr.agent.stages import STAGES  # noqa: E402
from itr.agent.states import State  # noqa: E402
from itr.models import Run  # noqa: E402


class NullSandbox:
    def create(self, image=None): pass
    def destroy(self): pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    import yaml
    config.load_env(ROOT / ".env")
    from itr import llm
    cases = {c["id"]: c for c in yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]}
    stages = {**STAGES, State.SETUP: lambda c: State.DONE}
    for cid in args.cases:
        case = cases[cid]
        kinds = Counter()
        for _ in range(args.n):
            run = Run(id=cid, issue={"url": case["url"], "repo": case["repo"], "ref": case["ref"],
                                     "ref_pinned": True, "as_of": case["as_of"]})
            init_budget(run)
            ctx = Context(run=run, sandbox=NullSandbox(), llm_call=llm.call, emit_fn=lambda k, p: None)
            run_machine(ctx, stages=stages)
            kinds[(run.triage or {}).get("kind", "ERROR")] += 1
        want = "feature/other" if case["expected_outcome"] == "NEEDS_MAINTAINER" else "bug"
        print(f"{cid:16} expected {want:14} got {dict(kinds)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
