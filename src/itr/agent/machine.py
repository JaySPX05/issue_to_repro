"""The state machine. It owns: stage sequencing, the sandbox lifecycle, the step limit, and
turning every failure into a clean terminal outcome. Stage logic lives in stages/."""
from __future__ import annotations

from typing import Callable

from itr.agent.budget import BudgetExceeded, init_budget
from itr.agent.context import Context
from itr.agent.stages import STAGES
from itr.agent.states import State

MAX_STEPS = 40   # loop guard: REPRO/FIX retry loops are bounded by budget, this is the backstop

Stage = Callable[[Context], State]


def _finish(ctx: Context, outcome: str, reason: str | None = None) -> None:
    ctx.run.outcome = outcome            # type: ignore[assignment]
    payload = {"outcome": outcome}
    if reason:
        payload["reason"] = reason
    ctx.emit("outcome", payload)


def run_machine(ctx: Context, stages: dict[State, Stage] | None = None,
                start: State = State.FETCH, max_steps: int = MAX_STEPS) -> Context:
    stages = stages or STAGES
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
