"""Where things live inside the sandbox. Shared by every stage that runs commands."""
REPO_DIR = "/work/repo"
VENV_DIR = "/work/venv"
PYTHON = f"{VENV_DIR}/bin/python"
PIP = f"{VENV_DIR}/bin/pip"
SCRIPT_DIR = "/work/repro"          # reproduction scripts live OUTSIDE the repo so they never dirty it
