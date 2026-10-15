## Evaluation

Eight real, closed GitHub issues from three libraries (`astanin/python-tabulate`, `pallets/click`, `tkem/cachetools`).
Each case is pinned to the commit just before the maintainers' fix, and comments written after the fix are hidden from the agent
(`eval/cases.yaml`). Two cases are traps where the right answer is *not* a patch: a Windows-only bug (the sandbox is Linux) and a
feature request.

Reproduce with `python scripts/run_eval.py` (`--report-only` rebuilds the table from saved runs). Results: [`eval/RESULTS.md`](eval/RESULTS.md).

**What CONFIRMED means.** It is never a model's opinion. The agent's patch and new regression test must pass a gate run by plain code:
the test fails on the original code, passes on the patched code, the original reproduction exits 0, and the project's own tests show no
new failures. If any check fails the agent retries with the exact reason, and after four attempts it reports NEEDS_MAINTAINER with the
reproduction and, if one exists, the closest candidate patch clearly marked unverified.

**Results (one full run on the final code, 33 minutes, $0.39 in total).**

| Case | Expected | Got | Cost | Time |
|---|---|---|---|---|
| tabulate-180 | CONFIRMED | CONFIRMED | $0.012 | 114 s |
| tabulate-241 | CONFIRMED | NEEDS_MAINTAINER | $0.110 | 306 s |
| tabulate-399 | CONFIRMED | NEEDS_MAINTAINER | $0.134 | 771 s |
| cachetools-405 | CONFIRMED | CONFIRMED | $0.028 | 241 s |
| click-1925 | CONFIRMED | CONFIRMED | $0.066 | 264 s |
| click-2877 | CONFIRMED | CONFIRMED | $0.032 | 202 s |
| click-3186 (Windows-only trap) | NOT_REPRODUCED | NOT_REPRODUCED | $0.007 | 58 s |
| click-1629 (feature-request trap) | NEEDS_MAINTAINER | NEEDS_MAINTAINER | $0.000 | 10 s |

6 of 8 outcomes match, with no crashes. Every run is appended to `eval/history.jsonl`; the table in `eval/RESULTS.md` shows a match rate over
all recorded runs, and a network outage is not counted as the agent failing. Results vary from run to run (model output is not
deterministic): the two tabulate misses have each ended NEEDS_MAINTAINER in every recorded run, and tabulate-399 once reported a patch
that the gate now rejects (see below).

**Audit of the CONFIRMED patches (what the gate can and cannot see).** We applied the agent's patches to the real repositories and tried
cases the tests do not cover:

- `click-1925`: identical to the maintainers' one-line fix.
- `tabulate-399` (an earlier run, before the gate was tightened): the project's own suite hung on an existing test. The old gate treated
  "the suite did not finish" as "could not run" and let the patch through; it is now a failure.
- `cachetools-405`: fixes the reported over-eviction and passes all tests, but computes the space needed once, before the loop, so growing the
  *oldest* key in an LRU cache evicts that key itself and leaves the cache over its `maxsize` (12 of 10 in our probe). The maintainers' fix does not.
- `tabulate-180`: fixes the crash, but returns `''` for empty data with headers when `maxcolwidths` is set, where the maintainers' fix (and the
  call without `maxcolwidths`) returns the header row.

So CONFIRMED means *"reproduced, and a patch plus a new test passed every automatic check"*. It does not mean *"correct in every case"*.
Each report lists the checks that ran and says that a maintainer should review the patch.

**What the misses taught us** (full notes in `decisions.md`, D58 to D60): in most misses the gate was right and an input was wrong.
A model-written reproduction counted `|` characters and could never accept a correct escape fix; a reviewer approved a reproduction
that tested an error that is correct by design; a bug only appears with an optional package the issue never mentions; models put
the wrong indentation on edits four times in a row (now repaired by code, still gated); models rewrote 50 lines where the
maintainers changed one (now refused by code).

