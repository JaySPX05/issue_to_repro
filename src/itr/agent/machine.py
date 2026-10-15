"""The state machine. It owns: stage sequencing, the sandbox lifecycle, the step limit, and
turning every failure into a clean terminal outcome. Stage logic lives in stages/."""
from __future__ import annotations

from typing import Callable

from itr.agent.budget import BudgetExceeded, init_budget
from itr.agent.context import Context
from itr.agent.stages import STAGES
from itr.agent.states import State
from itr.retry import RetrySandbox

MAX_STEPS = 40   # loop guard: REPRO/FIX retry loops are bounded by budget, this is the backstop

Stage = Callable[[Context], State]

# When the budget runs out AFTER the bug was reproduced, the run still owns something valuable (a verified
# reproduction). A small one-time allowance pays for the cheap REPORT stage so the result is
# NEEDS_MAINTAINER with a drafted comment, not ERROR with nothing (D58).
GRACE = {"max_cost_usd": 0.03, "max_tokens": 30_000, "max_seconds": 90}


def _has_reproduction(ctx: Context, state: State) -> bool:
    repro, verdict = ctx.run.repro or {}, ctx.run.verdict or {}
    return (state in (State.FIX, State.VERIFY) and verdict.get("matches_issue") is True
            and repro.get("classification") in ("failed", "hang"))


def _finish(ctx: Context, outcome: str, reason: str | None = None) -> None:
    ctx.run.outcome = outcome            # type: ignore[assignment]
    payload = {"outcome": outcome}
    if reason:
        payload["reason"] = reason
    ctx.emit("outcome", payload)


def run_machine(ctx: Context, stages: dict[State, Stage] | None = None,
                start: State = State.FETCH, max_steps: int = MAX_STEPS) -> Context:
    stages = stages or STAGES
    if not isinstance(ctx.sandbox, RetrySandbox):      # every sandbox call survives a transient API failure
        ctx.sandbox = RetrySandbox(ctx.sandbox, on_retry=lambda call, n, e: ctx.emit(
            "sandbox_retry", {"call": call, "attempt": n, "error": str(e)[:200]}))
    if not ctx.run.budget:
        init_budget(ctx.run)

    state, steps = start, 0
    reason: str | None = None
    try:
        ctx.sandbox.create()
        while state is not State.DONE:
            steps += 1
            if steps > max_steps:
                ctx.run.outcome = "ERROR"
                reason = f"step limit ({max_steps}) reached"
                break
            ctx.emit("stage_started", {"stage": state.value})
            try:
                nxt = stages[state](ctx)
            except BudgetExceeded as e:
                ctx.emit("stage_finished", {"stage": state.value, "error": str(e)})
                b = ctx.run.budget
                if _has_reproduction(ctx, state) and not b.get("grace_used"):
                    b["grace_used"] = True
                    for key, extra in GRACE.items():
                        b[key] += extra
                    ctx.run.outcome = "NEEDS_MAINTAINER"
                    reason = f"{e}; the reproduction is reported without a verified patch"
                    state = State.REPORT
                    continue
                ctx.run.outcome, reason = "ERROR", str(e)
                break
            except Exception as e:                      # a stage crashed: end cleanly, never hang
                ctx.emit("stage_finished", {"stage": state.value, "error": f"{type(e).__name__}: {e}"})
                ctx.run.outcome, reason = "ERROR", f"{state.value} failed: {type(e).__name__}: {e}"
                break
            ctx.emit("stage_finished", {"stage": state.value, "next": nxt.value})
            state = nxt
        if ctx.run.outcome is None:
            ctx.run.outcome, reason = "ERROR", "run ended without an outcome"
    except Exception as e:                              # sandbox.create() failed etc.
        ctx.run.outcome, reason = "ERROR", f"{type(e).__name__}: {e}"
    finally:
        try:
            ctx.sandbox.destroy()
        except Exception:
            pass
    _finish(ctx, ctx.run.outcome, reason)
    return ctx
