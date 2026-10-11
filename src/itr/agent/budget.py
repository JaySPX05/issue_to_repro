"""Run budget helpers. The budget lives in `run.budget` (a plain dict) so it serialises
straight into the store and the UI can display it."""
from __future__ import annotations

import time

from itr.models import Run

DEFAULTS = {
    "max_attempts": {"setup": 4, "repro": 4, "fix": 4},
    "max_tokens": 400_000,
    "max_seconds": 900,
}


class BudgetExceeded(Exception):
    """Global budget (tokens or wall time) is spent. The machine ends the run with ERROR."""


def init_budget(run: Run, **overrides) -> None:
    b = {
        "max_attempts": dict(DEFAULTS["max_attempts"]),
        "max_tokens": DEFAULTS["max_tokens"],
        "max_seconds": DEFAULTS["max_seconds"],
        "attempts": {},
        "tokens_used": 0,
        "tokens_by_tier": {},
        "started_at": time.time(),
    }
    for k, v in overrides.items():
        if k == "max_attempts":
            b["max_attempts"].update(v)
        else:
            b[k] = v
    run.budget = b


def check(run: Run) -> None:
    b = run.budget
    if b["tokens_used"] >= b["max_tokens"]:
        raise BudgetExceeded(f"token budget exhausted ({b['tokens_used']}/{b['max_tokens']})")
    if time.time() - b["started_at"] >= b["max_seconds"]:
        raise BudgetExceeded(f"time budget exhausted ({b['max_seconds']}s)")


def record_tokens(run: Run, tier: str, n: int) -> None:
    b = run.budget
    b["tokens_used"] += n
    b["tokens_by_tier"][tier] = b["tokens_by_tier"].get(tier, 0) + n


def spend_attempt(run: Run, stage: str) -> bool:
    """Use one attempt for `stage`. Returns False if none are left (stage decides what that means)."""
    b = run.budget
    used = b["attempts"].get(stage, 0)
    if used >= b["max_attempts"].get(stage, 1):
        return False
    b["attempts"][stage] = used + 1
    return True


def attempts_used(run: Run, stage: str) -> int:
    return run.budget["attempts"].get(stage, 0)
