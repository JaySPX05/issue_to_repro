"""Vercel entry point. Vercel looks for a FastAPI instance named `app` in a root-level app.py.

Locally, use `python -m itr.api` instead (background mode, http://127.0.0.1:8000).

On Vercel an instance keeps nothing between requests, so this sets single-request ("inline")
mode: a run lives inside the request that streams it, and the database is a scratch file in /tmp
that is rebuilt from demo_runs/ whenever a new instance starts. Settings can still be overridden
in the Vercel project's Environment Variables."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("RUN_MODE", "inline")
os.environ.setdefault("ITR_DB_PATH", "/tmp/itr.db")
os.environ.setdefault("SANDBOX_BACKEND", "nebius")
os.environ.setdefault("WEB_DIR", str(ROOT / "web"))
os.environ.setdefault("SEED_DIR", str(ROOT / "demo_runs"))

from itr.api import create_app  # noqa: E402

app = create_app()
