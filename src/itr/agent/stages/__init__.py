from itr.agent.stages.fetch import fetch
from itr.agent.stages.fix import fix
from itr.agent.stages.repro import repro
from itr.agent.stages.report import report
from itr.agent.stages.setup import setup
from itr.agent.stages.triage import triage
from itr.agent.stages.verdict import verdict
from itr.agent.stages.verify import verify
from itr.agent.states import State

STAGES = {
    State.FETCH: fetch,
    State.TRIAGE: triage,
    State.SETUP: setup,
    State.REPRO: repro,
    State.VERDICT: verdict,
    State.FIX: fix,
    State.VERIFY: verify,
    State.REPORT: report,
}
