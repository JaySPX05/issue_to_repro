"""Does Token Factory let us turn down Nemotron's reasoning? Compare tokens, time and correctness on one small
FIX-style task (the real fix prompt, a 5-line buggy function), with several candidate settings.

    python scripts/probe_reasoning.py              # super tier, 2 runs per variant (about 5 cents)
    python scripts/probe_reasoning.py --tier ultra --runs 1

Why: live runs show FIX calls that think until the 16000-token cap, return nothing, and are paid for twice.
If a variant keeps the answer correct with far fewer tokens, the platform teammate can add it to llm.py
(for example via an `extra_body`). A variant the API rejects shows its HTTP status instead of a number."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from itr import config  # noqa: E402

FILE_VIEW = "    1| def total(xs):\n    2|     s = 0\n    3|     for x in xs[1:]:\n    4|         s += x\n    5|     return s\n"
PAYLOAD = {
    "title": "total() ignores the first item", "body": "total([1, 2, 3]) returns 5, expected 6.", "comments": [],
    "triage": {"kind": "bug"}, "repro": {"script": "from lib import total\nassert total([1, 2, 3]) == 6\n",
                                         "python_flags": [], "output": "AssertionError"},
    "judge": {"reported_symptom": "wrong sum", "observed_symptom": "5", "reasoning": ""},
    "files": {"lib.py": FILE_VIEW}, "test_style": {"path": "tests/test_x.py", "text": "def test_a():\n    assert True\n"},
    "attempts_left_including_this_one": 4, "previous_attempts": [],
}
VARIANTS = [
    ("baseline", {}),
    ("enable_thinking=false", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("reasoning_effort=low", {"reasoning_effort": "low"}),
    ("thinking + low_effort", {"chat_template_kwargs": {"enable_thinking": True, "low_effort": True}}),
    ("/no_think in system", {"_system_suffix": "\n/no_think"}),
]


def post(base: str, key: str, body: dict):
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "issue-to-repro"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return r.status, json.loads(r.read().decode()), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200], time.time() - t0
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", time.time() - t0


def judge(content: str) -> str:
    try:
        d = json.loads(content)
        e = d["edits"][0]
        return "right" if e["start_line"] == 3 and "[1:]" not in e["replace"] else "wrong edit"
    except Exception:
        return "no valid JSON"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="super", choices=["nano", "super", "ultra"])
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()
    config.load_env(ROOT / ".env")
    key = config.get("NEBIUS_API_KEY")
    base = config.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").rstrip("/")
    model = config.get(f"MODEL_{args.tier.upper()}")
    if not key or not model:
        raise SystemExit("Set NEBIUS_API_KEY and MODEL_* in .env")
    fix_mod = importlib.import_module("itr.agent.stages.fix")
    system = (ROOT / "src/itr/agent/prompts/fix.md").read_text(encoding="utf-8")
    print(f"tier {args.tier}: {model}\n")
    print(f"{'variant':26} {'run':>3} {'HTTP':>5} {'out tokens':>10} {'secs':>6}  result")
    for name, extra in VARIANTS:
        for run in range(1, args.runs + 1):
            extra = dict(extra)
            suffix = extra.pop("_system_suffix", "")
            body = {"model": model, "max_tokens": 8000,
                    "messages": [{"role": "system", "content": system + suffix},
                                 {"role": "user", "content": json.dumps(PAYLOAD)}],
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "fix", "schema": fix_mod.FIX_SCHEMA, "strict": True}},
                    **extra}
            st, resp, secs = post(base, key, body)
            if st != 200:
                print(f"{name:26} {run:>3} {st:>5} {'-':>10} {secs:6.1f}  {str(resp)[:90]}")
                continue
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            usage = resp.get("usage", {})
            finish = (resp.get("choices") or [{}])[0].get("finish_reason")
            print(f"{name:26} {run:>3} {st:>5} {usage.get('completion_tokens', '?'):>10} {secs:6.1f}  "
                  f"{judge(msg.get('content') or '')}{' (hit max_tokens)' if finish == 'length' else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
