from enum import Enum


class State(str, Enum):
    FETCH = "FETCH"
    TRIAGE = "TRIAGE"
    SETUP = "SETUP"
    REPRO = "REPRO"
    VERDICT = "VERDICT"
    FIX = "FIX"
    VERIFY = "VERIFY"
    REPORT = "REPORT"
    DONE = "DONE"
