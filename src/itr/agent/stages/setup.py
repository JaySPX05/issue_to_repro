"""SETUP (super). STUB until Oct 14.

Contract: clone issue["repo"] at issue["ref"] into /work/repo, find and run install commands,
retrying on error up to budget max_attempts["setup"]. Success -> run.env = {commands, python}
and REPRO. Exhausted retries -> outcome SETUP_FAILED, DONE."""
from itr.agent.context import Context
from itr.agent.states import State


def setup(ctx: Context) -> State:
    ctx.run.env = {"stub": True}
    return State.REPRO
