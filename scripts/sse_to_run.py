"""Turn a saved event stream (from a hosted run) into a bundled demo run in demo_runs/.

    curl.exe -s -N "https://<your-app>/runs/<run_id>/events" -o demo_runs\\<name>.sse
    python scripts/sse_to_run.py demo_runs/<name>.sse

Writes demo_runs/<name>.json (the format scripts/export_run.py produces), which the server loads at
startup. Use this when the run happened on a serverless host such as Vercel, where the run's database
is temporary: save the stream while the instance is still warm, then convert it here.
Read the result before committing it: it holds the issue text, model output and command output."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def parse_sse(text: str) -> list[dict]:
    """Return the data objects of unnamed SSE messages (the run's events), in order."""
    events, event_name, data_lines = [], None, []
    for line in text.splitlines() + [""]:
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "":
            if data_lines and event_name is None:
                events.append(json.loads("\n".join(data_lines)))
            event_name, data_lines = None, []
    return events


def to_export(events: list[dict]) -> dict:
    """Build the {"run": ..., "events": [...]} structure Store.import_run expects."""
    if not events:
        raise ValueError("no events found: was the run still on the server when you saved it?")
    run_id = events[0]["run_id"]
    issue = next((e["payload"].get("content") or {} for e in events
                  if e["type"] == "artifact" and e["payload"].get("name") == "issue"), {})
    outcome = next((e["payload"].get("outcome") for e in reversed(events) if e["type"] == "outcome"), None)
    return {
        "run": {"id": run_id, "issue_url": issue.get("url") or "", "outcome": outcome,
                "created_at": events[0]["ts"]},
        "events": [{"type": e["type"], "payload": e["payload"], "ts": e["ts"]} for e in events],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 1
    src = Path(argv[0])
    try:
        data = to_export(parse_sse(src.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as e:
        print(f"Could not convert {src}: {e}")
        return 1
    out = src.with_suffix(".json")
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out} ({len(data['events'])} events, outcome {data['run']['outcome']}, "
          f"issue {data['run']['issue_url'] or '?'}).")
    if data["run"]["outcome"] is None:
        print("Warning: the stream has no outcome event; it was probably saved before the run finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
