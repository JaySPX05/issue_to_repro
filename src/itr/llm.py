"""LLM client contract. The real implementation (Token Factory, tier -> Nemotron model) is
owned by the platform teammate. Agent code only ever calls through a `LLMCall`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

Tier = Literal["nano", "super", "ultra"]


@dataclass
class LLMResponse:
    data: dict | str          # parsed JSON when a schema is given, else text
    tokens_in: int
    tokens_out: int
    latency_ms: int
    tier: str
    model: str


def call(tier: Tier, messages: list[dict], schema: dict | None = None) -> LLMResponse:
    raise NotImplementedError("Implemented by the platform teammate (Token Factory client).")


LLMCall = Callable[..., LLMResponse]
