"""VERDICT (ultra). STUB until Oct 16. A SEPARATE model call from REPRO, on purpose.

Contract: compare the symptom the issue claims against the symptom the real output shows.
ImportError, script typos and unrelated exceptions do NOT count as reproducing the bug.
run.verdict = {matches_issue, reasoning, confidence}.
matches -> FIX.  repro was bad and attempts remain -> REPRO.  otherwise -> NOT_REPRODUCED, DONE."""
from itr.agent.context import Context
from itr.agent.states import State


def verdict(ctx: Context) -> State:
    ctx.run.verdict = {"stub": True, "matches_issue": True, "reasoning": "", "confidence": 0.0}
    return State.FIX
