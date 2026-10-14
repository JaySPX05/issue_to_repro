# Bundled demo runs

Every `*.json` file here is loaded into the database when the server starts, so the hosted demo
always has finished runs to open and replay, even right after a restart.

Add one from a real run on your machine:

```
python scripts/export_run.py              # list recent runs
python scripts/export_run.py <run_id> click-2877-confirmed
```

Only commit real runs, and read the file first: it holds the issue text, model output and command output.
