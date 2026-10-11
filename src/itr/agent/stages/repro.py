"""REPRO (super). STUB until Oct 15.

Contract: write a minimal script from run.triage, run it in the sandbox, read the output, and
retry (feeding the error back) while spend_attempt(run, "repro") is True. Produces
run.repro = {script, last_output, attempts} and goes to VERDICT. If the script never runs
cleanly enough to judge, outcome NOT_REPRODUCED, DONE."""
from itr.agent.context import Context
from itr.agent.states import State


def repro(ctx: Context) -> State:
    ctx.run.repro = {"stub": True, "script": "", "last_output": "", "attempts": 0}
    return State.VERDICT
