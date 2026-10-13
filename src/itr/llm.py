"""LLM client: tier -> Nemotron model on Nebius Token Factory (OpenAI-compatible API).

CONTRACT FILE: `LLMResponse`, `call()`, `Tier` and `LLMCall` are shared with the agent code.
Change their shapes only by agreement.

Configuration (environment or .env, see .env.example):
    NEBIUS_API_KEY            required
    NEBIUS_BASE_URL           default https://api.tokenfactory.nebius.com/v1/
    MODEL_NANO / MODEL_SUPER / MODEL_ULTRA   required, exact model IDs
    LLM_MAX_TOKENS_<TIER>     optional, default nano 4000 / super 12000 / ultra 16000
                              (Nemotron thinks before answering and thinking counts toward this, D24)
    LLM_TIMEOUT_S             optional, default 180
    LLM_RETRIES               optional, default 2 (retries on 429 / 5xx / network errors only)

Standard library only (D19). Every call is logged with tier, model, tokens and latency.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Literal

from itr import config

Tier = Literal["nano", "super", "ultra"]

log = logging.getLogger("itr.llm")

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_MAX_TOKENS: dict[str, int] = {"nano": 4000, "super": 12000, "ultra": 16000}
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class LLMResponse:
    data: dict | str          # parsed JSON when a schema is given, else text
    tokens_in: int
    tokens_out: int
    latency_ms: int
    tier: str
    model: str


class LLMError(Exception):
    """The API could not be reached or returned a non-retryable error."""


_env_loaded = False


def _setting(name: str, default: str | None = None) -> str | None:
    global _env_loaded
    if not _env_loaded:
        config.load_env()
        _env_loaded = True
    return config.get(name, default)


def model_for(tier: str) -> str:
    """Return the configured model ID for `tier`. Raises LLMError if it is not set."""
    if tier not in DEFAULT_MAX_TOKENS:
        raise LLMError(f"unknown tier {tier!r}; expected nano, super or ultra")
    model = _setting(f"MODEL_{tier.upper()}")
    if not model:
        raise LLMError(f"MODEL_{tier.upper()} is not set (see .env.example)")
    return model


def _max_tokens(tier: str) -> int:
    raw = _setting(f"LLM_MAX_TOKENS_{tier.upper()}")
    return int(raw) if raw else DEFAULT_MAX_TOKENS[tier]


def _build_body(model: str, tier: str, messages: list[dict], schema: dict | None) -> dict:
    body: dict = {"model": model, "messages": messages, "max_tokens": _max_tokens(tier)}
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "schema": schema, "strict": True},
        }
    return body


def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "issue-to-repro"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _parse_json(text: str) -> dict | str:
    """Parse a JSON object, tolerating a code fence or surrounding prose. On failure return the
    raw text unchanged so the caller's validation and retry (Context.model_json) can handle it."""
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else text
    except json.JSONDecodeError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        try:
            obj = json.loads(text[a:b + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return text


def call(tier: Tier, messages: list[dict], schema: dict | None = None) -> LLMResponse:
    """Send `messages` to the model configured for `tier`.

    With `schema`, requests strict json_schema output and returns `data` as a dict when the
    reply parses (otherwise the raw string, for the caller to retry). Without `schema`, `data`
    is the reply text. Only the answer field is read; the reasoning field is never used.
    Retries 429/5xx/network errors with backoff; raises LLMError on anything else.
    """
    key = _setting("NEBIUS_API_KEY")
    if not key:
        raise LLMError("NEBIUS_API_KEY is not set (see .env.example)")
    model = model_for(tier)
    base = (_setting("NEBIUS_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
    timeout = float(_setting("LLM_TIMEOUT_S", "180") or 180)
    retries = int(_setting("LLM_RETRIES", "2") or 2)
    body = _build_body(model, tier, messages, schema)

    t0 = time.monotonic()
    resp: dict | None = None
    last_err = ""
    for attempt in range(retries + 1):
        try:
            resp = _post(f"{base}/chat/completions", key, body, timeout)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code not in RETRYABLE_STATUS:
                raise LLMError(f"{tier}/{model}: {last_err}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            wait = 2 ** attempt
            log.warning("llm %s retry %d/%d in %ds (%s)", tier, attempt + 1, retries, wait, last_err)
            time.sleep(wait)
    if resp is None:
        raise LLMError(f"{tier}/{model}: failed after {retries + 1} attempts ({last_err})")
    latency_ms = int((time.monotonic() - t0) * 1000)

    choice = (resp.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    usage = resp.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    reasoning_tokens = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    finish = choice.get("finish_reason")

    log.info("llm tier=%s model=%s tokens_in=%d tokens_out=%d reasoning=%d latency_ms=%d finish=%s",
             tier, model, tokens_in, tokens_out, reasoning_tokens, latency_ms, finish)
    if finish == "length":
        log.warning("llm %s hit max_tokens=%d; raise LLM_MAX_TOKENS_%s if answers come back empty",
                    tier, body["max_tokens"], tier.upper())

    data: dict | str = _parse_json(text) if schema is not None else text
    return LLMResponse(data=data, tokens_in=tokens_in, tokens_out=tokens_out,
                       latency_ms=latency_ms, tier=tier, model=resp.get("model") or model)


LLMCall = Callable[..., LLMResponse]


if __name__ == "__main__":
    # Live smoke test: python -m itr.llm [tier]
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = sys.argv[1] if len(sys.argv) > 1 else "nano"
    s = {"type": "object", "properties": {"kind": {"enum": ["bug", "feature"]}},
         "required": ["kind"], "additionalProperties": False}
    r = call(t, [{"role": "user", "content":
                  'Classify "IndexError on empty list" as bug or feature. Reply as JSON.'}], s)  # type: ignore[arg-type]
    print(r)