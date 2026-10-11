# Agent core

| State | Model | Next states |
|---|---|---|
| FETCH | none | TRIAGE |
| TRIAGE | nano | SETUP (bug) / DONE: NEEDS_MAINTAINER (anything else) |
| SETUP | super | REPRO / DONE: SETUP_FAILED |
| REPRO | super | VERDICT / DONE: NOT_REPRODUCED |
| VERDICT | ultra | FIX / REPRO / DONE: NOT_REPRODUCED |
| FIX | super -> ultra | VERIFY |
| VERIFY | none (plain code) | REPORT / FIX / DONE: NEEDS_MAINTAINER |
| REPORT | nano | DONE: CONFIRMED |

Any stage may also end the run as ERROR (crash, token/time budget, step limit); the machine
handles that, stages do not.

Rules: stages touch the world only through `Context` (`ctx.sh`, `ctx.model_json`, `ctx.emit`),
so budgeting, logging and JSON validation live in one place. VERIFY never calls a model.

Add a stage: write `stages/<name>.py` returning the next `State`, a prompt in
`prompts/<name>.md`, register it in `stages/__init__.py`, test it with the fakes in `tests/fakes.py`.
