"""VERIFY (NO LLM, plain code). STUB until Oct 18.

Contract: run the regression test on the ORIGINAL code (must fail) and on the PATCHED code
(must pass). Only then -> REPORT. Gate fails and fix attempts remain -> FIX; otherwise
-> NEEDS_MAINTAINER, DONE. Never let a model decide this."""
from itr.agent.context import Context
from itr.agent.states import State


def verify(ctx: Context) -> State:
    return State.REPORT
