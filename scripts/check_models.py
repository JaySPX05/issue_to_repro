"""Smoke-test each model tier: does it answer, how fast, and which JSON mode works?

    python scripts/check_models.py

Needs NEBIUS_API_KEY and MODEL_NANO / MODEL_SUPER / MODEL_ULTRA in .env."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from itr import config  # noqa: E402

SCHEMA = {"type": "object", "properties": {"kind": {"enum": ["bug", "feature"]}, "n": {"type": "integer"}},
          "required": ["kind", "n"], "additionalProperties": False}
JSON_PROMPT = 'Classify: "app crashes with IndexError on empty list". Reply with JSON {"kind": "bug" or "feature", "n": 3}.'


def post(base: str, key: str, body: dict) -> tuple[int, dict | str, float]:
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "issue-to-repro"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode()), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300], time.time() - t0
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", time.time() - t0


def extract_json(text: str):
    """Parse JSON, tolerating a code fence or surrounding prose. Returns (obj, needed_cleanup)."""
    try:
        return json.loads(text), False
    except Exception:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(text[a:b + 1]), True
        except Exception:
            pass
    return None, True


def message(resp: dict) -> dict:
    return (resp.get("choices") or [{}])[0].get("message") or {}


def check_tier(base: str, key: str, tier: str, model: str) -> dict:
    out = {"tier": tier, "model": model}
    status, body, secs = post(base, key, {"model": model, "max_tokens": 400,
                                          "messages": [{"role": "user", "content": "Reply with the single word: pong"}]})
    if status != 200:
        return {**out, "ok": False, "error": f"HTTP {status}: {body}"}
    m, usage = message(body), body.get("usage", {})
    out.update(ok=True, secs=round(secs, 1), reply=(m.get("content") or "").strip()[:40],
               tokens=usage.get("total_tokens"), reasoning_field=bool(m.get("reasoning_content") or m.get("reasoning")))

    out["json_mode"] = "none"
    modes = [("json_schema", {"type": "json_schema", "json_schema": {"name": "t", "schema": SCHEMA, "strict": True}}),
             ("json_object", {"type": "json_object"}), ("prompt_only", None)]
    for name, rf in modes:
        req = {"model": model, "max_tokens": 600, "messages": [{"role": "user", "content": JSON_PROMPT}]}
        if rf:
            req["response_format"] = rf
        st, b, _ = post(base, key, req)
        if st != 200:
            continue
        obj, cleaned = extract_json(message(b).get("content") or "")
        if isinstance(obj, dict) and obj.get("kind") in ("bug", "feature"):
            out["json_mode"], out["needs_cleanup"] = name, cleaned
            break
    return out


def main() -> int:
    config.load_env()
    key = config.get("NEBIUS_API_KEY")
    base = config.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").rstrip("/")
    if not key:
        print("NEBIUS_API_KEY missing"); return 1
    bad = False
    for tier in ("nano", "super", "ultra"):
        model = config.get(f"MODEL_{tier.upper()}")
        if not model:
            print(f"{tier:6} MODEL_{tier.upper()} not set in .env"); bad = True; continue
        r = check_tier(base, key, tier, model)
        if not r["ok"]:
            print(f"{tier:6} FAILED  {model}\n       {r['error']}"); bad = True; continue
        print(f"{tier:6} OK      {model}\n       {r['secs']}s, {r['tokens']} tokens, reply={r['reply']!r}, "
              f"separate reasoning field: {r['reasoning_field']}\n       JSON mode that works: {r['json_mode']}"
              f"{' (output needed cleanup)' if r.get('needs_cleanup') else ''}")
        bad = bad or r["json_mode"] == "none"
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
