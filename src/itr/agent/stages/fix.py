"""FIX (super, escalating to ultra after repeated failures). STUB until Oct 17.

Contract: locate the root cause, write a patch and a regression test.
run.fix = {patch, regression_test, before_result, after_result}. Always -> VERIFY."""
from itr.agent.context import Context
from itr.agent.states import State


def fix(ctx: Context) -> State:
    ctx.run.fix = {"stub": True, "patch": "", "regression_test": ""}
    return State.VERIFY
