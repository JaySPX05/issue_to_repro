"""Run the agent on every eval case and report how many outcomes match `expected_outcome`.

    python scripts/run_eval.py                      # all cases, skipping ones already in live_runs/
    python scripts/run_eval.py --fresh              # re-run everything (costs real money)
    python scripts/run_eval.py --only click-3186 click-1629
    python scripts/run_eval.py --report-only        # rebuild the table from live_runs/ without running anything

Each case runs in its own subprocess (scripts/live_run.py), so one crash cannot stop the batch.
Writes eval/RESULTS.md (commit it; it is the evidence for the README) from live_runs/<case>.json."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "live_runs"
HISTORY = ROOT / "eval" / "history.jsonl"      # one line per finished run, appended and never rewritten


def load_cases() -> list[dict]:
    import yaml
    return yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]


def network_ok() -> bool:
    import socket
    try:
        for host in ("github.com", "api.github.com", "api.tokenfactory.nebius.com"):
            socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def wait_for_network(max_wait: int = 150) -> bool:
    """A dropped connection must not be recorded as the agent failing (a whole pass was lost to one)."""
    waited = 0
    while not network_ok():
        if waited >= max_wait:
            return False
        print(f"    no network (DNS lookup failed); waiting 15 s ({waited}/{max_wait} s)", flush=True)
        time.sleep(15)
        waited += 15
    return True


def infra_error(case_id: str) -> bool:
    """True when the run ended in ERROR because of the network or the API, not because of the agent."""
    p = RUNS / f"{case_id}.json"
    if not p.exists():
        return False
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("outcome") != "ERROR":
        return False
    errs = [e["payload"].get("error", "") for e in d.get("events", []) if e["type"] == "stage_finished"]
    text = " ".join(x for x in errs if x).lower()
    markers = ("timeout", "timed out", "getaddrinfo", "urlerror", "remotedisconnected", "connection reset",
               "connection aborted", "connectionerror", "bad gateway", "service unavailable", "503", "502", "504")
    return any(m in text for m in markers)


def git_state() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return (sha or "unknown") + ("+local-changes" if dirty else "")
    except Exception:
        return "unknown"


def record(case: dict) -> None:
    """Append this run to eval/history.jsonl, so the table can say "CONFIRMED in 2 of 3 runs", not just the last run."""
    r = row(case)
    if r["got"] == "NOT RUN":
        return
    HISTORY.parent.mkdir(exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M"), "case": case["id"], "expected": r["expected"],
                            "got": r["got"], "ok": r["ok"], "infra": infra_error(case["id"]), "cost": round(r.get("cost", 0), 4),
                            "secs": round(r.get("secs", 0)), "code": git_state()}) + "\n")


def load_history() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out.setdefault(d["case"], []).append(d)
    return out


def run_case(case: dict, max_cost: float) -> None:
    print(f"\n=== {case['id']}", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "live_run.py"), case["id"], "--max-cost", str(max_cost)],
                   cwd=ROOT, check=False)
    record(case)


def row(case: dict) -> dict:
    p = RUNS / f"{case['id']}.json"
    if not p.exists():
        return {"id": case["id"], "expected": case["expected_outcome"], "got": "NOT RUN", "ok": False}
    d = json.loads(p.read_text(encoding="utf-8"))
    events = d.get("events", [])
    tiers = sorted({e["payload"]["tier"] for e in events if e["type"] == "model_call"})
    fx = d.get("fix") or {}
    return {"id": case["id"], "expected": case["expected_outcome"], "got": d.get("outcome"),
            "ok": d.get("outcome") == case["expected_outcome"],
            "cost": d["budget"]["cost_usd"], "tokens": d["budget"]["tokens_used"],
            "secs": events[-1]["t"] if events else 0, "tiers": tiers,
            "when": time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "attempts": fx.get("attempt") or fx.get("attempts"),
            "verified": bool(fx.get("patch")) and d.get("outcome") == "CONFIRMED"}


def render(rows: list[dict], history: dict[str, list[dict]] | None = None) -> str:
    history = history or {}
    done = [r for r in rows if r["got"] != "NOT RUN"]
    ok = sum(r["ok"] for r in done)
    cost = sum(r.get("cost", 0) for r in done)
    lines = [f"# Eval results", "",
             f"**{ok}/{len(rows)} outcomes match the expected outcome** "
             f"({len(rows) - len(done)} not run). Total cost ${cost:.3f}.", "",
             "| Case | Expected | Latest run | Match | Match rate (all recorded runs) | Cost | Time | Last run |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["got"] == "NOT RUN":
            lines.append(f"| {r['id']} | {r['expected']} | not run | - | - | - | - | - |")
            continue
        allh = history.get(r["id"], [])
        h = [x for x in allh if not x.get("infra")]            # a network/API outage is not the agent failing
        rate = f"{sum(x['ok'] for x in h)}/{len(h)}" if h else "-"
        if len(allh) != len(h):
            rate += f" (+{len(allh) - len(h)} network error)"
        lines.append(f"| {r['id']} | {r['expected']} | {r['got']} | {'yes' if r['ok'] else '**NO**'} | {rate} | "
                     f"${r['cost']:.3f} | {r['secs']:.0f}s | {r.get('when', '-')} |")
    lines += ["", "CONFIRMED means the patch and a new regression test passed the verification gate "
              "(test fails before the patch, passes after, the original repro exits 0, no new failures in the project's own suite; if the suite cannot be run the report says so, and a suite that stops finishing after the patch is a failure).",
              "The two cases that expect a decline are traps: a Windows-only bug and a feature request. "
              "Correct behaviour there is to decline, not to invent a fix."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--max-cost", type=float, default=0.25)
    args = ap.parse_args()

    cases = load_cases()
    if args.only:
        unknown = set(args.only) - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"unknown case(s): {', '.join(sorted(unknown))}")
    t0 = time.time()
    if not args.report_only:
        for c in cases:
            if args.only and c["id"] not in args.only:
                continue
            if not args.fresh and (RUNS / f"{c['id']}.json").exists() and not args.only:
                print(f"=== {c['id']}: using saved result (use --fresh to re-run)")
                continue
            if not wait_for_network():
                print("\nStopping: the network is down, so further runs would only record outages. "
                      "Re-run when it is back (finished cases are kept).", flush=True)
                break
            run_case(c, args.max_cost)
    rows = [row(c) for c in cases]
    text = render(rows, load_history())
    (ROOT / "eval" / "RESULTS.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    print(f"wrote eval/RESULTS.md ({time.time() - t0:.0f}s)")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
