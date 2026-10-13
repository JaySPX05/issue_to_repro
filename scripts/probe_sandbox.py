"""Find out what the real Nebius Sandbox can and cannot do, before we build the adapter on guesses.

Run it once your project has Sandboxes access:

    pip install "contree-sdk==0.3.6"
    python scripts/probe_sandbox.py                 # default image python:3.12-slim
    python scripts/probe_sandbox.py --image python:3.12 --save probe.json

It needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID in .env (the project id looks like aiproject-xxxx).
Sandboxes are free while in beta. Everything it runs is our own fixed commands, in throwaway VMs.

What it answers (the numbers decide how the NebiusSandbox adapter is written):
  1  Can we connect and run Python?                      5  Does a FAILED command still keep its changes?
  2  Is git / venv / enough CPU, RAM, disk there?        6  What happens on a timeout?
  3  Is there network (GitHub, PyPI, anything else)?     7  Can two branches start from one saved state?
  4  Do changes carry over between runs (our chain)?     8  Can we write and read files without a shell? (content must be bytes)
  9  Are our API keys visible inside the sandbox? (they must not be)   10 How are huge outputs cut?
 11  The real thing: clone click at a pinned commit, build a venv, install, run a repro script, with times.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from itr import config  # noqa: E402

RESULTS: list[dict] = []


def make_client():
    from contree_sdk import ContreeSync
    return ContreeSync()


class Chain:
    """One sandbox 'session': each command runs on the image the previous command produced."""

    def __init__(self, client, image):
        self.client, self.image = client, image
        self.log: list[tuple[str, float]] = []

    def run(self, cmd: str, timeout: int = 120, keep: bool = True):
        t0 = time.time()
        out = self.image.run(shell=cmd, timeout=timeout, disposable=not keep).wait()
        self.log.append((cmd[:60], round(time.time() - t0, 1)))
        if keep:
            self.image = out
        return out


def record(num: int, name: str, status: str, detail: str, **data) -> None:
    RESULTS.append({"n": num, "name": name, "status": status, "detail": detail, **data})
    print(f"  [{status:4}] {num:>2} {name}: {detail}")


def short(text, n=160) -> str:
    return " | ".join((text or "").strip().splitlines())[:n]


def attempt(num, name, fn):
    t0 = time.time()
    try:
        fn()
    except Exception as e:  # a probe must never crash the whole run
        record(num, name, "FAIL", f"{type(e).__name__}: {short(str(e), 200)}  ({time.time() - t0:.0f}s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default="python:3.12-slim")
    ap.add_argument("--save", metavar="FILE", help="write the results to a JSON file")
    ap.add_argument("--skip-setup-smoke", action="store_true", help="skip probe 11 (the slowest)")
    args = ap.parse_args()

    config.load_env(ROOT / ".env")
    for name in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID"):
        if config.get(name):
            os.environ[name] = config.get(name)
        else:
            raise SystemExit(f"Set {name} in .env first (see .env.example).")
    client = make_client()
    base = client.images.use(args.image)
    print(f"image: {args.image}   project: {config.mask(os.environ['NEBIUS_PROJECT_ID'])}\n")

    # 1 ---------------------------------------------------------------- connect
    def p1():
        t0 = time.time()
        r = base.run(shell="python --version", timeout=60).wait()
        record(1, "connect and run python", "OK" if r.exit_code == 0 else "FAIL",
               f"{short(r.stdout or r.stderr)}  wall {time.time() - t0:.1f}s, vm {r.result.elapsed_time.total_seconds():.1f}s, cost {r.result.cost}")
    attempt(1, "connect and run python", p1)

    # 2 ---------------------------------------------------------------- tools and size
    def p2():
        r = base.run(shell="for t in git python3 pip curl; do printf '%s=' $t; command -v $t || echo MISSING; done; "
                           "python3 -m venv --help >/dev/null 2>&1 && echo venv=ok || echo venv=MISSING; "
                           "echo cpus=$(nproc); grep MemTotal /proc/meminfo; df -h / | tail -1; id -un; uname -sr",
                     timeout=60).wait()
        text = (r.stdout or "") + (r.stderr or "")
        missing = [w for w in ("git=MISSING", "venv=MISSING") if w in text]
        record(2, "tools, cpu, ram, disk", "WARN" if missing else "OK",
               ("missing: " + ", ".join(missing) + "; " if missing else "") + short(text, 300), raw=text)
    attempt(2, "tools, cpu, ram, disk", p2)

    def p2b():
        for other in ("python:3.12", "python:3.12-bookworm"):
            if other == args.image:
                continue
            t0 = time.time()
            try:
                r = client.images.use(other).run(shell="command -v git || echo git=MISSING; git --version; python --version", timeout=300).wait()
                record(2, f"image {other}: git?", "OK" if "MISSING" not in (r.stdout or "") else "NO",
                       f"{short(r.stdout or r.stderr)}  ({time.time() - t0:.0f}s incl. any first pull)")
                return
            except Exception as e:
                record(2, f"image {other}: git?", "NO", f"{type(e).__name__}: {short(str(e), 150)}")
    attempt(2, "other image", p2b)

    # 3 ---------------------------------------------------------------- network
    def p3():
        checks = {
            "github (git ls-remote)": "git ls-remote https://github.com/pallets/click HEAD",
            "pypi (pip download)": "python -m pip download --no-deps -q -d /tmp/dl tabulate && echo downloaded",
            "other site (curl example.com)": "python -c \"import urllib.request as u; print(u.urlopen('https://example.com', timeout=15).status)\"",
        }
        for label, cmd in checks.items():
            r = base.run(shell=cmd, timeout=90).wait()
            ok = r.exit_code == 0
            record(3, f"network: {label}", "OK" if ok else "NO", short(r.stdout if ok else (r.stderr or r.stdout)), ok=ok)
    attempt(3, "network", p3)

    # 4 and 5 ---------------------------------------------------------------- persistence
    snap = {}

    def p4():
        c = Chain(client, base)
        c.run("mkdir -p /work && echo hello > /work/a.txt")
        r = c.run("cat /work/a.txt", keep=False)
        record(4, "files persist to the next run", "OK" if "hello" in (r.stdout or "") else "NO", short(r.stdout), )
        lost = base.run(shell="echo temp > /work_tmp.txt", timeout=60).wait()           # disposable
        try:
            again = lost.run(shell="cat /work_tmp.txt", timeout=60).wait()
            record(4, "disposable run keeps nothing (as documented)", "OK" if again.exit_code != 0 else "WARN",
                   f"next-run exit {again.exit_code}")
        except Exception as e:                      # the SDK refuses to continue from a discarded image
            record(4, "disposable run keeps nothing (as documented)", "OK",
                   f"cannot continue from it: {type(e).__name__}  (so every exec in our adapter must use disposable=False)")
        snap["chain"] = c
    attempt(4, "persistence", p4)

    def p5():
        c = Chain(client, base)
        c.run("mkdir -p /work")
        failed = c.run("echo kept > /work/b.txt; exit 3")
        record(5, "failed command: exit code is reported, not raised", "OK" if failed.exit_code == 3 else "FAIL",
               f"exit_code={failed.exit_code}")
        r = c.run("cat /work/b.txt", keep=False)
        record(5, "failed command still keeps its changes", "OK" if "kept" in (r.stdout or "") else "NO", short(r.stdout or r.stderr))
    attempt(5, "failed command", p5)

    # 6 ---------------------------------------------------------------- timeout
    def p6():
        c = Chain(client, base)
        t0 = time.time()
        try:
            r = c.run("sleep 90; echo done", timeout=8)
            record(6, "timeout of 8s on 'sleep 90'", "WARN",
                   f"returned normally after {time.time() - t0:.0f}s: exit_code={r.exit_code} stdout={short(r.stdout)!r}")
        except Exception as e:
            record(6, "timeout of 8s on 'sleep 90'", "OK",
                   f"raised {type(e).__name__} after {time.time() - t0:.0f}s: {short(str(e), 120)}  (adapter must turn this into ExecResult(timed_out=True))",
                   exception=type(e).__name__)
        # a leftover process cannot survive: the next run is a new VM from the saved image
        r2 = base.run(shell="ps -e | wc -l", timeout=60).wait()
        record(6, "fresh VM per run (no leftover processes)", "INFO", f"processes in a new run: {short(r2.stdout)}")
    attempt(6, "timeout", p6)

    # 7 ---------------------------------------------------------------- branching
    def p7():
        c = Chain(client, base)
        c.run("mkdir -p /work && echo root > /work/state.txt")
        saved = c.image
        sid = str(saved.uuid)
        a = client.images.use(sid).run(shell="echo A > /work/branch.txt", disposable=False, timeout=60).wait()
        b = client.images.use(sid).run(shell="ls /work", timeout=60).wait()
        record(7, "branch from a saved image id", "OK" if "branch.txt" not in (b.stdout or "") else "NO",
               f"saved id {sid[:8]}..; branch A wrote branch.txt; branch B from the same id sees: {short(b.stdout)}")
        again = client.images.use(sid).run(shell="cat /work/state.txt", timeout=60).wait()
        record(7, "restore by id after more work", "OK" if "root" in (again.stdout or "") else "NO", short(again.stdout), restore_ok=True)
    attempt(7, "branching", p7)

    # 8 ---------------------------------------------------------------- files without a shell
    def p8():
        c = Chain(client, base)
        c.image = c.image.apply_files({"/work/s.py": b"print('from uploaded file')\n"})   # bytes = content; a str would be read as a local path
        r = c.run("python /work/s.py", keep=False)
        record(8, "upload a file, then run it", "OK" if "uploaded" in (r.stdout or "") else "NO", short(r.stdout or r.stderr))
        c.run("echo output-data > /work/out.txt")
        data = c.image.read("/work/out.txt")
        record(8, "read a file back from the image", "OK" if b"output-data" in data else "NO", repr(data[:40]))
    attempt(8, "files", p8)

    # 9 ---------------------------------------------------------------- secrets
    def p9():
        r = base.run(shell="env | grep -iE 'nebius|api_key|token|secret' || echo none", timeout=60).wait()
        clean = (r.stdout or "").strip() == "none"
        record(9, "our keys are NOT visible inside the sandbox", "OK" if clean else "WARN", short(r.stdout))
    attempt(9, "secrets", p9)

    # 10 --------------------------------------------------------------- output size
    def p10():
        r = base.run(shell="python -c \"print('x' * 300000)\"", timeout=60).wait()
        record(10, "300 KB of output", "INFO", f"received {len(r.stdout or '')} chars, truncated={r.result.truncated}")
    attempt(10, "output size", p10)

    # 11 --------------------------------------------------------------- the real thing
    def p11():
        ref = "df2e5ed8c4e89f51ff4eddb9600d913083613e62"      # click-2877, the commit before the fix
        script = ("from click.types import Path\nresult = Path(allow_dash=True).convert('', '', '')\n"
                  "print(f'Result: {result!r}')\nassert isinstance(result, str)\n")
        c = Chain(client, base)
        steps = [
            ("clone", "mkdir -p /work && git clone -q https://github.com/pallets/click /work/repo", 300),
            ("checkout", f"cd /work/repo && git checkout -q --detach {ref}", 60),
            ("venv", "python -m venv /work/venv", 120),
            ("install", "/work/venv/bin/pip install -q -e /work/repo", 300),
        ]
        t_all = time.time()
        if c.run("command -v git", keep=False).exit_code != 0:
            t0 = time.time()
            r = c.run("apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git", timeout=300)
            record(11, "install git with apt-get (image has none)", "OK" if r.exit_code == 0 else "FAIL",
                   f"{time.time() - t0:.0f}s" + ("" if r.exit_code == 0 else f"  exit {r.exit_code}: {short(r.stderr or r.stdout, 250)}"))
            if r.exit_code != 0:
                return
        for label, cmd, tmo in steps:
            t0 = time.time()
            r = c.run(cmd, timeout=tmo)
            ok = r.exit_code == 0
            record(11, f"setup step '{label}'", "OK" if ok else "FAIL",
                   f"{time.time() - t0:.0f}s" + ("" if ok else f"  exit {r.exit_code}: {short(r.stderr or r.stdout, 250)}"))
            if not ok:
                if label == "clone" and "git" in (r.stderr or "") and "not found" in (r.stderr or ""):
                    record(11, "hint", "INFO", "git is missing in this image: rerun with --image python:3.12 (full image) or install git in a prepared base image")
                return
        c.image = c.image.apply_files({"/work/repro/repro_1.py": script.encode()})
        r = c.run("cd /work/repo && PYTHONPATH=/work/repo:/work/repo/src /work/venv/bin/python -bb -W error /work/repro/repro_1.py", keep=False)
        last = short((r.stderr or "").strip().splitlines()[-1] if (r.stderr or "").strip() else r.stdout)
        good = r.exit_code != 0 and "BytesWarning" in (r.stderr or "")
        record(11, "repro script on the buggy commit", "OK" if good else "WARN",
               f"exit {r.exit_code}: {last}   (expected: a BytesWarning failure)")
        fixed = c.run("cd /work/repo && git checkout -q --detach 6f85d26dcd50c1745e81040ed4f2b61f8bf293cf && "
                      "PYTHONPATH=/work/repo:/work/repo/src /work/venv/bin/python -bb -W error /work/repro/repro_1.py", keep=False)
        record(11, "same script on the fixed commit", "OK" if fixed.exit_code == 0 else "WARN",
               f"exit {fixed.exit_code} (expected 0)   whole setup+repro took {time.time() - t_all:.0f}s")
        snap["setup_id"] = str(c.image.uuid)
    if not args.skip_setup_smoke:
        attempt(11, "setup smoke", p11)

    counts = {s: sum(r["status"] == s for r in RESULTS) for s in ("OK", "WARN", "NO", "FAIL", "INFO")}
    print(f"\nSUMMARY  {counts}")
    print("Send the whole output above back to the agent author. Nothing in it is secret.")
    if args.save:
        Path(args.save).write_text(json.dumps(RESULTS, indent=1, default=str), encoding="utf-8")
        print(f"saved to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
