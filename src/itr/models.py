"""Shared data model.

CONTRACT FILE: shared by both teammates. Change only by agreement, then update both sides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["nano", "super", "ultra"]

EventType = Literal[
    "stage_started", "model_call", "command_run",
    "artifact", "stage_finished", "outcome",
]

# NOTE: "ERROR" is an addition to the original contract (budget exhausted, crash,
# or step limit). Agree it with your teammate so the UI can render it.
Outcome = Literal["CONFIRMED", "NOT_REPRODUCED", "NEEDS_MAINTAINER", "SETUP_FAILED", "ERROR"]


@dataclass
class Event:
    run_id: str
    seq: int
    type: EventType
    payload: dict
    ts: float


@dataclass
class Run:
    id: str
    issue: dict                      # url, repo, title, body, comments, ref
    triage: dict | None = None       # kind, claimed_version, steps, expected, actual, missing_info
    env: dict | None = None          # working install commands, python version
    repro: dict | None = None        # script, last_output, attempts
    verdict: dict | None = None      # matches_issue, reasoning, confidence
    fix: dict | None = None          # patch, regression_test, before_result, after_result
    budget: dict = field(default_factory=dict)
    outcome: Outcome | None = None
