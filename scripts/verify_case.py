"""Check that an eval case is real: the tests added by the fix FAIL on the commit just before it
and PASS on the fix commit. Run it on any clone of the project's repository.

    python scripts/verify_case.py <path-to-clone> <fix-sha>

Uses the Python that runs this script, so install pytest (and a case's optional dependency, such as
wcwidth for tabulate-399) in that environment first. A test run that exceeds CASE_TIMEOUT seconds
(default 60) counts as a failure: it is how hangs are detected.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

TIMEOUT = int(os.environ.get("CASE_TIMEOUT", "60"))


def git(repo: str, *args: str, cwd: str | None = None) -> str:
    r = subprocess.run(["git", "-C", cwd or repo, *args], capture_output=True, text=True)
    return r.stdout.strip()


def run_tests(repo: str, rev: str, targets: list[str], fix: str | None) -> tuple[object, str]:
    """Check out `rev` in a temporary worktree, optionally overlay the fix's test files, run pytest."""
    wt = tempfile.mkdtemp(prefix="itr_case_")
    try:
        git(repo, "worktree", "add", "-q", "--detach", wt, rev)
        if fix:
            git(repo, "checkout", fix, "--", *targets, cwd=wt)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(wt, "src"), wt]))
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *targets],
                               cwd=wt, env=env, capture_output=True, text=True, timeout=TIMEOUT)
            return p.returncode, " | ".join((p.stdout + p.stderr).strip().splitlines()[-2:])[-200:]
        except subprocess.TimeoutExpired:
            return "TIMEOUT", f"hung for more than {TIMEOUT}s"
    finally:
        git(repo, "worktree", "remove", "--force", wt)
        shutil.rmtree(wt, ignore_errors=True)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    repo, sha = sys.argv[1], sys.argv[2]
    full = git(repo, "rev-parse", sha)
    parent = git(repo, "rev-parse", full + "^")
    files = git(repo, "show", "--name-only", "--format=", full).split()
    tests = [f for f in files if re.search(r"(^|/)tests?(/|_)|test_", f) and f.endswith(".py")]
    if not tests:
        print(f"NO TESTS  {full[:10]}: the fix changed no test files, so it cannot be checked this way.")
        return 3
    # some projects (cachetools) keep shared test code in tests/__init__.py: run the whole folder
    targets = [os.path.dirname(tests[0]) or "tests"] if all(os.path.basename(t) == "__init__.py" for t in tests) else tests
    before = run_tests(repo, parent, targets, fix=full)
    after = run_tests(repo, full, targets, fix=None)
    ok = before[0] != 0 and after[0] == 0
    print(f"{'VALID' if ok else 'INVALID'}  fix={full[:10]} parent={parent[:10]} tests={targets}")
    print("  before the fix:", before)
    print("  after the fix: ", after)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
