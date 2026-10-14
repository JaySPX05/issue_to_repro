"""Put one realistic, finished sample run into the local database so the UI can be tried
(and Replay demonstrated) without Docker, Nebius or model credits.

    python scripts/seed_demo_run.py            # then: python -m itr.api  -> open the printed URL

The events mirror what the real stages emit (artifact names and payload shapes from src/itr/agent),
but the content is illustrative sample data, labelled as such in the issue title."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from itr import config  # noqa: E402
from itr.store import Store  # noqa: E402

URL = "https://github.com/example/minicalc/issues/42"

SCRIPT = '''import minicalc

# The issue says mean([]) should return None, but it raises ZeroDivisionError.
result = minicalc.mean([])
assert result is None, f"expected None, got {result!r}"
print("OK")
'''

PATCH = '''--- a/minicalc/stats.py
+++ b/minicalc/stats.py
@@ -10,5 +10,7 @@ def mean(values):
     """Arithmetic mean of `values`; None for an empty sequence."""
+    if not values:
+        return None
     return sum(values) / len(values)
'''

COMMENT = '''Thanks for the report — confirmed on `main` (a1b2c3d).

### Reproduction
```python
''' + SCRIPT + '''```

```
ZeroDivisionError: division by zero
```

### Proposed patch
```diff
''' + PATCH + '''```

### Regression test (`tests/test_mean_empty.py`)
```python
from minicalc import mean

def test_mean_of_empty_is_none():
    assert mean([]) is None
```

The new test fails on the current code and passes with the patch; the existing suite still passes.'''

# (seconds after start, type, payload)
EVENTS = [
    (0.0, "stage_started", {"stage": "FETCH"}),
    (0.6, "artifact", {"name": "issue", "content": {"url": URL, "repo": "example/minicalc",
                                                   "title": "[sample data] mean([]) raises ZeroDivisionError instead of returning None",
                                                   "ref": "main"}}),
    (0.7, "stage_finished", {"stage": "FETCH", "next": "TRIAGE"}),
    (0.8, "stage_started", {"stage": "TRIAGE"}),
    (2.1, "model_call", {"stage": "triage", "tier": "nano", "model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
                         "tokens_in": 812, "tokens_out": 404, "latency_ms": 1260}),
    (2.2, "artifact", {"name": "triage", "content": {
        "kind": "bug", "claimed_version": "1.4.0", "steps": ["import minicalc", "call minicalc.mean([])"],
        "expected": "returns None", "actual": "ZeroDivisionError: division by zero", "missing_info": []}}),
    (2.3, "stage_finished", {"stage": "TRIAGE", "next": "SETUP"}),
    (2.4, "stage_started", {"stage": "SETUP"}),
    (4.0, "command_run", {"cmd": "git clone --depth 50 https://github.com/example/minicalc /work/repo", "exit_code": 0,
                          "timed_out": False, "stdout": "", "stderr": "Cloning into '/work/repo'...\n"}),
    (4.4, "artifact", {"name": "ref_choice", "content": {"ref": "v1.4.0", "note": "issue claims 1.4.0; tag v1.4.0 exists"}}),
    (9.8, "command_run", {"cmd": "cd /work/repo && python -m pip install -e '.[test]'", "exit_code": 0, "timed_out": False,
                          "stdout": "Successfully installed minicalc-1.4.0 pytest-8.3.3\n", "stderr": ""}),
    (12.0, "command_run", {"cmd": "cd /work/repo && python -m pytest --collect-only -q", "exit_code": 0, "timed_out": False,
                           "stdout": "37 tests collected in 0.41s\n", "stderr": ""}),
    (12.1, "artifact", {"name": "environment", "content": {"python": "3.12.7", "ref": "v1.4.0", "tests_collect_ok": True,
                                                          "commands": ["python -m pip install -e '.[test]'"]}}),
    (12.2, "stage_finished", {"stage": "SETUP", "next": "REPRO"}),
    (12.3, "stage_started", {"stage": "REPRO"}),
    (16.9, "model_call", {"stage": "repro", "tier": "super", "model": "nvidia/nemotron-3-super-120b-a12b",
                          "tokens_in": 2310, "tokens_out": 1630, "latency_ms": 4520}),
    (17.0, "artifact", {"name": "repro_script", "content": {"path": "/work/repro.py", "script": SCRIPT,
                                                           "python_flags": [], "attempt": 1}}),
    (18.2, "command_run", {"cmd": "cd /work/repo && python /work/repro.py", "exit_code": 1, "timed_out": False, "stdout": "",
                           "stderr": "Traceback (most recent call last):\n  File \"/work/repro.py\", line 4, in <module>\n"
                                     "    result = minicalc.mean([])\n  File \"/work/repo/minicalc/stats.py\", line 12, in mean\n"
                                     "    return sum(values) / len(values)\nZeroDivisionError: division by zero\n"}),
    (18.3, "artifact", {"name": "repro_result", "content": {"classification": "failed", "exit_code": 1,
                                                           "output": "ZeroDivisionError: division by zero"}}),
    (18.4, "stage_finished", {"stage": "REPRO", "next": "VERDICT"}),
    (18.5, "stage_started", {"stage": "VERDICT"}),
    (25.1, "model_call", {"stage": "verdict", "tier": "ultra", "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
                          "tokens_in": 1980, "tokens_out": 2240, "latency_ms": 6550}),
    (25.2, "artifact", {"name": "verdict", "content": {
        "matches_issue": True, "confidence": 0.93, "failure_matches_symptom": True, "would_pass_if_fixed": True,
        "reported_symptom": "mean([]) raises ZeroDivisionError", "observed_symptom": "ZeroDivisionError at stats.py:12",
        "reasoning": "The script calls mean([]) exactly as the issue describes and fails with the reported exception "
                     "at the division. It asserts the documented behaviour (None), so it would pass once fixed.",
        "rejected_because": "", "cannot_reproduce_in_sandbox": False}}),
    (25.3, "stage_finished", {"stage": "VERDICT", "next": "FIX"}),
    (25.4, "stage_started", {"stage": "FIX"}),
    (27.0, "model_call", {"stage": "fix", "tier": "super", "model": "nvidia/nemotron-3-super-120b-a12b",
                          "tokens_in": 1450, "tokens_out": 610, "latency_ms": 1580}),
    (27.1, "artifact", {"name": "fix_locate", "content": {"files": ["minicalc/stats.py"], "terms": ["def mean"]}}),
    (32.6, "model_call", {"stage": "fix", "tier": "super", "model": "nvidia/nemotron-3-super-120b-a12b",
                          "tokens_in": 3120, "tokens_out": 1890, "latency_ms": 5400}),
    (32.7, "artifact", {"name": "fix_plan", "content": {"attempt": 1, "tier": "super",
                                                       "root_cause": "mean() divides by len(values) without handling an empty sequence.",
                                                       "patch": PATCH, "test_path": "tests/test_mean_empty.py", "plan_errors": []}}),
    (32.8, "stage_finished", {"stage": "FIX", "next": "VERIFY"}),
    (32.9, "stage_started", {"stage": "VERIFY"}),
    (34.0, "command_run", {"cmd": "cd /work/repo && python -m pytest -q tests/test_mean_empty.py   # original code",
                           "exit_code": 1, "timed_out": False,
                           "stdout": "F\nFAILED tests/test_mean_empty.py::test_mean_of_empty_is_none - ZeroDivisionError\n1 failed in 0.05s\n",
                           "stderr": ""}),
    (35.2, "command_run", {"cmd": "cd /work/repo && git apply fix.patch && python -m pytest -q tests/test_mean_empty.py",
                           "exit_code": 0, "timed_out": False, "stdout": ".\n1 passed in 0.04s\n", "stderr": ""}),
    (36.1, "command_run", {"cmd": "cd /work/repo && python /work/repro.py", "exit_code": 0, "timed_out": False,
                           "stdout": "OK\n", "stderr": ""}),
    (38.9, "command_run", {"cmd": "cd /work/repo && python -m pytest -q", "exit_code": 0, "timed_out": False,
                           "stdout": "38 passed in 1.12s\n", "stderr": ""}),
    (39.0, "artifact", {"name": "verify_passed", "content": {"before": 1, "after": 0,
                                                            "suite": {"ran": True, "passed_before": 37, "passed_after": 38}}}),
    (39.1, "stage_finished", {"stage": "VERIFY", "next": "REPORT"}),
    (39.2, "stage_started", {"stage": "REPORT"}),
    (41.0, "model_call", {"stage": "report", "tier": "nano", "model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
                          "tokens_in": 1720, "tokens_out": 690, "latency_ms": 1750}),
    (41.1, "artifact", {"name": "report", "content": {"title": "Confirmed: mean([]) raises ZeroDivisionError",
                                                     "comment": COMMENT, "generated_by_model": True}}),
    (41.2, "stage_finished", {"stage": "REPORT", "next": "DONE"}),
    (41.3, "outcome", {"outcome": "CONFIRMED"}),
]


def main() -> int:
    config.load_env()
    store = Store(config.get("ITR_DB_PATH", "data/itr.db"))
    run_id = store.create_run(URL, run_id=f"sample-{int(time.time())}")
    t0 = time.time() - EVENTS[-1][0]
    for offset, type_, payload in EVENTS:
        store.append(run_id, type_, payload, ts=t0 + offset)
    print(f"Seeded sample run {run_id}.")
    print("Start the server:  python -m itr.api")
    print(f"Then open:         http://127.0.0.1:8000/?run={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
