"""Save a finished run from your local database into demo_runs/ so the hosted demo always has it.

    python scripts/export_run.py                 # list recent runs
    python scripts/export_run.py <run_id> [name] # write demo_runs/<name or run_id>.json

The server loads every demo_runs/*.json at startup (SEED_DIR), so judges can open and Replay
real runs even if the demo host restarts or new runs are switched off (ALLOW_NEW_RUNS=false).
Exports contain issue text, model output and command output; read one before committing it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from itr import config  # noqa: E402
from itr.store import Store  # noqa: E402


def main(argv: list[str]) -> int:
    config.load_env()
    store = Store(config.get("ITR_DB_PATH", "data/itr.db"))
    if not argv:
        for r in store.list_runs(20):
            print(f"{r['id']:<22} {r['outcome'] or r['state']:<17} {r['issue_url']}")
        print("\nUsage: python scripts/export_run.py <run_id> [name]")
        return 0
    run_id, name = argv[0], (argv[1] if len(argv) > 1 else argv[0])
    try:
        data = store.export_run(run_id)
    except KeyError:
        print(f"No run {run_id!r} in {config.get('ITR_DB_PATH', 'data/itr.db')}")
        return 1
    if data["run"]["state"] != "done":
        print("That run has not finished yet; export it once it has an outcome.")
        return 1
    out = ROOT / "demo_runs" / f"{name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)} ({len(data['events'])} events, outcome {data['run']['outcome']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
