"""Tiny .env loader (standard library only) plus helpers for reading settings.

Real environment variables always win over .env, so deployment (e.g. a Nebius endpoint) can
inject secrets without a file."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> list[str]:
    """Load KEY=VALUE lines from `path` into os.environ (without overriding existing values).
    Returns the names that were set. A missing file is fine and returns []."""
    p = Path(path)
    if not p.is_file():
        return []
    set_names: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            set_names.append(key)
    return set_names


def get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value else default


def mask(secret: str | None) -> str:
    """Show only enough of a secret to recognise it: never print the whole thing."""
    if not secret:
        return "(not set)"
    return f"{secret[:4]}...{secret[-3:]} ({len(secret)} chars)" if len(secret) > 10 else "(set, short)"
