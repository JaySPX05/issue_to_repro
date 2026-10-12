"""See how the real model understands a bug, without any sandbox.

For an eval case this script: reads the issue from GitHub (hiding comments written after the fix),
asks the nano model to triage it, then asks the super model for a reproduction script using the same
prompts the agent uses, and prints the result next to the known answer.

The generated script is NOT run. It is model-written code; run it only inside a sandbox.

    pip install pyyaml
    python scripts/try_repro_prompt.py click-2877
    python scripts/try_repro_prompt.py --all

Needs NEBIUS_API_KEY, MODEL_NANO, MODEL_SUPER (and GITHUB_TOKEN) in .env.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jsonschema  # noqa: E402

from itr import config, github  # noqa: E402
from itr.agent.stages.repro import REPRO_SCHEMA, build_payload  # noqa: E402
from itr.agent.stages.triage import TRIAGE_SCHEMA  # noqa: E402
from itr.models import Run  # noqa: E402

PROMPTS = ROOT / "src" / "itr" / "agent" / "prompts"
MAX_TOKENS = {"nano": 6000, "super": 12000}      # thinking tokens count toward this (decisions D24)


def chat(tier: str, messages: list[dict], schema: dict) -> dict:
    """One Token Factory call. Tries strict JSON-schema output, falls back to plain JSON mode."""
    key = config.get("NEBIUS_API_KEY")
    base = config.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").rstrip("/")
    model = config.get(f"MODEL_{tier.upper()}")
    if not (key and model):
        raise SystemExit(f"Set NEBIUS_API_KEY and MODEL_{tier.upper()} in .env first.")
    modes = [
        {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema, "strict": True}},
        {"type": "json_object"},
    ]
    last = ""
    for i, response_format in enumerate(modes):
        msgs = messages if i == 0 else [
            {"role": "system", "content": messages[0]["content"] + "\n\nReply with ONE JSON object matching this "
             "JSON Schema:\n" + json.dumps(schema)}] + messages[1:]
        body = {"model": model, "messages": msgs, "max_tokens": MAX_TOKENS[tier], "response_format": response_format}
        req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                              "User-Agent": "issue-to-repro"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code in (400, 422) and i == 0:
                continue                       # this server/model rejected strict schema mode: try plain JSON
            raise SystemExit(f"Token Factory call failed: {last}")
        choice = resp["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        usage = resp.get("usage", {})
        info = {"model": model, "seconds": round(time.time() - t0, 1), "finish": choice.get("finish_reason"),
                "tokens": usage.get("total_tokens"), "json_mode": response_format["type"]}
        try:
            data = json.loads(text)
            jsonschema.validate(data, schema)
            return {"data": data, **info}
        except Exception as e:                  # cut off, empty, or off-schema
            last = f"{type(e).__name__}: {str(e)[:200]} (finish={info['finish']}, content={text[:150]!r})"
    raise SystemExit(f"No valid JSON from {tier} model. {last}\n"
                     "If finish=length the thinking used up max_tokens: raise MAX_TOKENS in this script.")


def source_files(repo: str, ref: str, token: str | None) -> str:
    """The same hint REPRO gets in the real run: the library's non-test .py files."""
    try:
        tree = github._http_get(f"{github.API}/repos/{repo}/git/trees/{ref}?recursive=1", token)
    except Exception as e:
        return ""
    skip = ("/tests/", "/test/", "/docs/", "/doc/", "/examples/", "/benchmarks/")
    paths = [t["path"] for t in tree.get("tree", []) if t["path"].endswith(".py")
             and not any(s in "/" + t["path"] for s in skip) and not t["path"].startswith(("tests/", "test/", "docs/"))]
    return "\n".join(paths[:60])


def run_case(case: dict) -> dict:
    token = config.get("GITHUB_TOKEN")
    issue = github.fetch_issue(case["url"], token, as_of=case.get("as_of"))
    issue["ref"] = case["ref"]
    run = Run(id=case["id"], issue=issue)

    triage_prompt = (PROMPTS / "triage.md").read_text(encoding="utf-8")
    triage_in = {"title": issue["title"], "body": issue["body"][:6000],
                 "comments": [c["body"][:1500] for c in issue["comments"][:10]]}
    t = chat("nano", [{"role": "system", "content": triage_prompt},
                      {"role": "user", "content": json.dumps(triage_in, ensure_ascii=False)}], TRIAGE_SCHEMA)
    run.triage = t["data"]
    out = {"case": case, "issue_title": issue["title"], "comments_visible": len(issue["comments"]), "triage": t}
    if run.triage["kind"] != "bug":
        return out                               # the real agent would stop here: NEEDS_MAINTAINER

    run.env = {"python": "Python 3.12", "ref": case["ref"], "ref_note": "pinned to the commit before the fix"}
    payload = build_payload(SimpleNamespace(run=run), [], source_files(case["repo"], case["ref"], token), 4)
    r = chat("super", [{"role": "system", "content": (PROMPTS / "repro.md").read_text(encoding="utf-8")},
                       {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], REPRO_SCHEMA)
    out["repro"] = r
    return out


def show(res: dict) -> None:
    c, t = res["case"], res["triage"]
    line = "=" * 78
    print(f"\n{line}\n{c['id']}  [{c['category']}, {c['difficulty']}]  {c['url']}")
    print(f"issue: {res['issue_title']}   (comments visible to the model: {res['comments_visible']})")
    print(f"KNOWN ANSWER   expected outcome: {c['expected_outcome']}")
    print(f"               symptom: {c['symptom']}")
    k = t["data"]
    print(f"\nTRIAGE (nano, {t['seconds']}s, {t['tokens']} tokens): kind={k['kind']}")
    print(f"   expected: {k['expected']}\n   actual:   {k['actual']}")
    ok_kind = (k["kind"] == "bug") == (c["expected_outcome"] != "NEEDS_MAINTAINER")
    print(f"   -> {'as expected' if ok_kind else 'DIFFERENT from the known answer'}")
    if "repro" not in res:
        print("   (stops here: not treated as a bug, so no sandbox work)")
        return
    r, d = res["repro"], res["repro"]["data"]
    print(f"\nREPRO (super, {r['seconds']}s, {r['tokens']} tokens, {r['json_mode']}, finish={r['finish']})")
    print(f"   reasoning: {d['reasoning']}\n   expected symptom: {d['expected_symptom']}")
    print(f"   python flags: {d['python_flags'] or '-'}   extra packages: {d['extra_packages'] or '-'}")
    print("   ---- script (NOT executed) ----")
    print("\n".join("   | " + ln for ln in d["script"].splitlines()))
    checks = []
    for f in c.get("expects_flags", []):
        checks.append((f"uses interpreter flag {f}", f in d["python_flags"]))
    for p in c.get("expects_packages", []):
        checks.append((f"asks for package {p}", any(p in x for x in d["extra_packages"])))
    if c["expected_outcome"] == "NEEDS_MAINTAINER":
        checks.append(("should not have been sent to REPRO at all", False))
    for text, ok in checks:
        print(f"   [{'PASS' if ok else 'FAIL'}] {text}")
    print(f"\n   YOU judge: does this script fail for the reason above ({c['symptom'][:70]}...)?")
    print(f"   Ground-truth test of the real fix: {c.get('failing_test_before_fix')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", nargs="?", help="case id from eval/cases.yaml, e.g. click-2877")
    ap.add_argument("--all", action="store_true", help="run every case")
    ap.add_argument("--save", metavar="FILE", help="also write the raw results to a JSON file")
    args = ap.parse_args()
    try:
        import yaml
    except ImportError:
        raise SystemExit("pip install pyyaml  (needed to read eval/cases.yaml)")
    config.load_env(ROOT / ".env")
    cases = {c["id"]: c for c in yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]}
    if not (args.all or args.case):
        print("cases:", ", ".join(cases)); return 2
    if args.case and args.case not in cases:
        raise SystemExit(f"unknown case {args.case!r}; choose from: {', '.join(cases)}")
    results = []
    for cid in (cases if args.all else [args.case]):
        res = run_case(cases[cid]); results.append(res); show(res)
    if args.save:
        Path(args.save).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nsaved to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
