"""Check that your credentials work. Safe to run any time; never prints full secrets.

    python scripts/check_setup.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from itr import config  # noqa: E402

REQUIRED = ["NEBIUS_API_KEY", "NEBIUS_PROJECT_ID"]
OPTIONAL = ["GITHUB_TOKEN", "MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA"]


def get_json(url: str, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "issue-to-repro", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:                                   # DNS, timeout, proxy...
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    loaded = config.load_env()
    print(f".env: loaded {len(loaded)} value(s)" if loaded else ".env: nothing loaded (file missing or empty)")
    ok = True

    print("\n1. Environment variables")
    for name in REQUIRED + OPTIONAL:
        v = config.get(name)
        tag = "OK     " if v else ("MISSING" if name in REQUIRED else "unset  ")
        print(f"   {tag} {name:18} {config.mask(v) if 'KEY' in name or 'TOKEN' in name else (v or '')}")
        if name in REQUIRED and not v:
            ok = False

    key = config.get("NEBIUS_API_KEY")
    base = config.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").rstrip("/")
    if key:
        print("\n2. Token Factory inference key")
        status, body = get_json(f"{base}/models", {"Authorization": f"Bearer {key}"})
        if status == 200 and isinstance(body, dict):
            ids = [m.get("id", "") for m in body.get("data", [])]
            print(f"   OK: key accepted, {len(ids)} models visible")
            nemo = sorted(i for i in ids if "nemotron" in i.lower())
            print("   Nemotron models visible to you:" if nemo else "   No model ID contains 'nemotron' (check the catalog in the console).")
            for i in nemo:
                print(f"     - {i}")
        else:
            ok = False
            print(f"   FAILED (HTTP {status}): {body}")
            if status in (401, 403):
                print("   -> key is wrong, revoked, or from a different project.")
            if status == 404:
                print("   -> this server has no /models list; test with a chat call in the playground instead.")
            if status == 0:
                print("   -> network problem: check internet/VPN/proxy.")

    gh = config.get("GITHUB_TOKEN")
    print("\n3. GitHub token")
    status, body = get_json("https://api.github.com/rate_limit", {"Authorization": f"Bearer {gh}"} if gh else {})
    if status == 200 and isinstance(body, dict):
        core = body["resources"]["core"]
        print(f"   {'authenticated' if gh else 'NOT authenticated'}: {core['remaining']}/{core['limit']} requests left this hour")
        if not gh:
            print("   -> add GITHUB_TOKEN to .env (60/hour is too low for eval runs).")
    else:
        print(f"   could not check (HTTP {status}): {body}")

    print("\n4. Sandboxes: run `contree auth ls` and look for status 'ok' (see walkthrough).")
    print("\nRESULT:", "required settings present." if ok else "fix the items marked MISSING/FAILED above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
