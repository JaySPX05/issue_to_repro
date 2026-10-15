# Eval results

**6/8 outcomes match the expected outcome** (0 not run). Total cost $0.388.

| Case | Expected | Latest run | Match | Match rate (all recorded runs) | Cost | Time | Last run |
|---|---|---|---|---|---|---|---|
| tabulate-180 | CONFIRMED | CONFIRMED | yes | 3/3 | $0.012 | 114s | 10-16 02:32 |
| tabulate-241 | CONFIRMED | NEEDS_MAINTAINER | **NO** | 0/2 | $0.110 | 306s | 10-16 02:37 |
| tabulate-399 | CONFIRMED | NEEDS_MAINTAINER | **NO** | 0/2 | $0.134 | 771s | 10-16 02:50 |
| cachetools-405 | CONFIRMED | CONFIRMED | yes | 2/2 | $0.028 | 241s | 10-16 02:54 |
| click-1925 | CONFIRMED | CONFIRMED | yes | 2/2 | $0.066 | 264s | 10-16 02:58 |
| click-2877 | CONFIRMED | CONFIRMED | yes | 1/2 | $0.032 | 202s | 10-16 03:02 |
| click-3186 | NOT_REPRODUCED | NOT_REPRODUCED | yes | 2/2 | $0.007 | 58s | 10-16 03:03 |
| click-1629 | NEEDS_MAINTAINER | NEEDS_MAINTAINER | yes | 2/2 | $0.000 | 10s | 10-16 03:03 |

CONFIRMED means the patch and a new regression test passed the verification gate (test fails before the patch, passes after, the original repro exits 0, no new failures in the project's own suite; if the suite cannot be run the report says so, and a suite that stops finishing after the patch is a failure).
The two cases that expect a decline are traps: a Windows-only bug and a feature request. Correct behaviour there is to decline, not to invent a fix.
