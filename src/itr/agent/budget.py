"""Run budget helpers. The budget lives in `run.budget` (a plain dict) so it serialises
straight into the store and the UI can display it."""
from __future__ import annotations

import time

from itr.models import Run

# USD per 1M tokens (input, output), read from the Nebius Token Factory catalog on 2026-10-12.
# Prices are approximate and can change; update here only.
PRICES = {
    "nano": (0.06, 0.24),
    "super": (0.30, 0.90),
    "ultra": (1.00, 3.00),
}

DEFAULTS = {
    "max_attempts": {"setup": 4, "repro": 4, "fix": 4},
    "max_tokens": 300_000,
    "max_seconds": 900,
    "max_cost_usd": 0.15,   # ~6 runs per $1 of trial credit
}


class BudgetExceeded(Exception):
    """Global budget (tokens or wall time) is spent. The machine ends the run with ERROR."""


def init_budget(run: Run, **overrides) -> None:
    b = {
        "max_attempts": dict(DEFAULTS["max_attempts"]),
        "max_tokens": DEFAULTS["max_tokens"],
        "max_seconds": DEFAULTS["max_seconds"],
        "max_cost_usd": DEFAULTS["max_cost_usd"],
        "cost_usd": 0.0,
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
    if b["cost_usd"] >= b["max_cost_usd"]:
        raise BudgetExceeded(f"cost budget exhausted (${b['cost_usd']:.3f}/${b['max_cost_usd']:.2f})")
    if time.time() - b["started_at"] >= b["max_seconds"]:
        raise BudgetExceeded(f"time budget exhausted ({b['max_seconds']}s)")


def record_tokens(run: Run, tier: str, n_in: int, n_out: int = 0) -> None:
    """Record one model call. Input and output are priced differently, so keep them apart.
    (Reasoning tokens are billed as output and are included in n_out by the API.)"""
    b = run.budget
    n = n_in + n_out
    p_in, p_out = PRICES.get(tier, PRICES["ultra"])   # unknown tier: assume the dearest
    b["cost_usd"] += (n_in * p_in + n_out * p_out) / 1_000_000
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
