"""Everything a stage needs, behind one object. Stages never talk to the sandbox, the LLM,
or the event sink directly: they go through Context so budgeting, logging and validation
happen in exactly one place."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import jsonschema

from itr.agent import budget
from itr.llm import LLMCall, LLMResponse
from itr.models import Run, Tier
from itr.retry import retry_call
from itr.sandbox.base import ExecResult, Sandbox

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_OUTPUT_CHARS = 4000


ESCALATE = {"nano": "super", "super": "ultra", "ultra": "ultra"}     # who answers after an unusable reply


class StageError(Exception):
    """A stage could not produce valid output (e.g. the model kept returning bad JSON)."""


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [{len(text) - limit} chars omitted] ...\n" + text[-half:]


@dataclass
class Context:
    run: Run
    sandbox: Sandbox
    llm_call: LLMCall
    emit_fn: Callable[[str, dict], None]
    prompts_dir: Path = PROMPTS_DIR

    # ---- events -------------------------------------------------------
    def emit(self, event_type: str, payload: dict | None = None) -> None:
        self.emit_fn(event_type, payload or {})

    def artifact(self, name: str, content) -> None:
        self.emit("artifact", {"name": name, "content": content})

    # ---- prompts ------------------------------------------------------
    def prompt(self, name: str) -> str:
        return (self.prompts_dir / f"{name}.md").read_text(encoding="utf-8")

    # ---- sandbox ------------------------------------------------------
    def sh(self, cmd: str, timeout: int = 120) -> ExecResult:
        budget.check(self.run)
        res = self.sandbox.exec(cmd, timeout=timeout)
        self.emit("command_run", {
            "cmd": cmd, "exit_code": res.exit_code, "timed_out": res.timed_out,
            "stdout": clip(res.stdout), "stderr": clip(res.stderr),
        })
        return res

    # ---- models -------------------------------------------------------
    def model(self, tier: Tier, messages: list[dict], schema: dict | None = None,
              stage: str = "") -> LLMResponse:
        budget.check(self.run)
        resp = retry_call(self.llm_call, tier, messages, schema,
                          on_retry=lambda n, e: self.emit("model_retry", {"tier": tier, "attempt": n, "error": str(e)[:200]}))
        budget.record_tokens(self.run, resp.tier, resp.tokens_in, resp.tokens_out)
        self.emit("model_call", {
            "stage": stage, "tier": resp.tier, "model": resp.model,
            "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
            "latency_ms": resp.latency_ms,
        })
        return resp

    def model_json(self, tier: Tier, messages: list[dict], schema: dict,
                   stage: str = "", retries: int = 1) -> LLMResponse:
        """Call a model and require JSON matching `schema`. On an unusable reply, retry with the problem fed
        back, and on the NEXT tier up: a reply that is empty or cut off usually means the model spent its whole
        output budget thinking, and asking the same model the same question just does it again (seen live: two
        16000-token replies in a row ended three eval runs as ERROR). Raises StageError when retries run out."""
        msgs = list(messages)
        last_err = ""
        for _ in range(retries + 1):
            resp = self.model(tier, msgs, schema, stage=stage)
            data = resp.data
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError as e:
                    last_err = f"not valid JSON: {e}"
                    empty = not data.strip()
                    note = ("Your last reply was EMPTY: you ran out of room while thinking. Think briefly and "
                            "reply with only the JSON." if empty else
                            f"Your last reply was {last_err}. Reply with only valid JSON.")
                    msgs = msgs + [{"role": "user", "content": note}]
                    nxt = ESCALATE.get(tier, tier)
                    if nxt != tier:
                        self.emit("model_escalated", {"stage": stage, "from": tier, "to": nxt, "reason": last_err[:120]})
                    tier = nxt
                    continue
            try:
                jsonschema.validate(data, schema)
            except jsonschema.ValidationError as e:
                last_err = f"schema violation: {e.message}"
                msgs = msgs + [{"role": "user", "content":
                                f"Your last reply had a {last_err}. Fix it and reply with only valid JSON."}]
                continue
            resp.data = data
            return resp
        raise StageError(f"{stage or 'model'}: no valid JSON after {retries + 1} tries ({last_err})")
