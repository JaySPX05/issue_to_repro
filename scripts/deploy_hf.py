"""Deploy the app to a Hugging Face Space (Docker) in one command.

    pip install huggingface_hub
    hf auth login                                  # once; paste a token with WRITE access
    python scripts/deploy_hf.py                    # code only
    python scripts/deploy_hf.py --secrets          # code + secrets/settings from your .env

It creates <your-username>/issue-to-repro (public, Docker) if needed, uploads the app (src, web,
demo_runs, Dockerfile, ...; never .env, data/ or tests), and gives the Space its own README
header that tells Hugging Face to build the Dockerfile and serve port 7860.

--secrets copies these from .env into the Space settings:
    secrets   (hidden):  NEBIUS_API_KEY, NEBIUS_PROJECT_ID, GITHUB_TOKEN
    variables (visible): MODEL_NANO, MODEL_SUPER, MODEL_ULTRA, ALLOW_NEW_RUNS, MAX_CONCURRENT_RUNS
Values are sent only to Hugging Face and never printed. The Space rebuilds after each upload.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from itr import config  # noqa: E402

SPACE_NAME = "issue-to-repro"
SECRETS = ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID", "GITHUB_TOKEN")
VARIABLES = ("MODEL_NANO", "MODEL_SUPER", "MODEL_ULTRA", "ALLOW_NEW_RUNS", "MAX_CONCURRENT_RUNS")
UPLOAD = ["Dockerfile", ".dockerignore", "pyproject.toml", "LICENSE",
          "src/**", "web/**", "demo_runs/**"]
NEVER = [".env", ".env.*", "data/**", "**/__pycache__/**", "*.db", "*.db-*", "tests/**", ".git/**"]

SPACE_README = """---
title: Issue-to-Repro
emoji: 🔁
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Reproduces GitHub issues in a sandbox, then patches and verifies
---

# Issue-to-Repro

Paste a GitHub issue URL for a Python library. The agent reproduces the bug in a Nebius
Token Factory Sandbox, has a separate NVIDIA Nemotron model judge whether the failure matches
the report, and only then proposes a patch whose regression test must fail before and pass after.

Source code: {repo_url}
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--space", help=f"owner/name (default: <you>/{SPACE_NAME})")
    ap.add_argument("--secrets", action="store_true", help="also copy secrets and settings from .env")
    ap.add_argument("--repo-url", default="https://github.com/JaySPX05/issue_to_repro")
    args = ap.parse_args(argv)

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("Install it first:  python -m pip install huggingface_hub")
        return 1
    api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception:
        print("Not logged in to Hugging Face. Run:  hf auth login   (use a token with WRITE access)")
        return 1
    space = args.space or f"{user}/{SPACE_NAME}"

    print(f"1/3  Creating or reusing the Space {space} (public, Docker)…")
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True, private=False)

    if args.secrets:
        config.load_env(ROOT / ".env")
        missing = [k for k in SECRETS[:2] + VARIABLES[:3] if not config.get(k)]
        if missing:
            print(f"Missing in .env: {', '.join(missing)}. Fill them in, or run without --secrets.")
            return 1
        print("2/3  Setting secrets and variables (values not shown)…")
        for k in SECRETS:
            if config.get(k):
                api.add_space_secret(space, k, config.get(k))
                print(f"       secret   {k}")
        for k in VARIABLES:
            if config.get(k):
                api.add_space_variable(space, k, config.get(k))
                print(f"       variable {k} = {config.get(k)}")
    else:
        print("2/3  Skipping secrets (add --secrets to copy them from .env)")

    print("3/3  Uploading the app…")
    api.upload_folder(repo_id=space, repo_type="space", folder_path=str(ROOT),
                      allow_patterns=UPLOAD, ignore_patterns=NEVER,
                      commit_message="Deploy Issue-to-Repro")
    api.upload_file(repo_id=space, repo_type="space", path_in_repo="README.md",
                    path_or_fileobj=SPACE_README.format(repo_url=args.repo_url).encode("utf-8"),
                    commit_message="Space README")

    host = space.replace("/", "-").replace("_", "-").lower()
    print(f"\nDone. Build log: https://huggingface.co/spaces/{space}")
    print(f"App (after the build, ~3-5 min): https://{host}.hf.space")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
