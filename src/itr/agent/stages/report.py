"""REPORT (nano). STUB until Oct 18. Drafts the comment for the issue. Sets outcome CONFIRMED."""
from itr.agent.context import Context
from itr.agent.states import State


def report(ctx: Context) -> State:
    ctx.run.outcome = "CONFIRMED"
    return State.DONE
