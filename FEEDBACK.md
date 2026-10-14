# Feedback on Nebius and NVIDIA tools

Notes from building Issue-to-Repro during the NVIDIA × Nebius hackathon (Oct 2026): what worked well,
what surprised us, and what would have saved us time. Each entry is dated. Where an entry links to
`decisions.md`, the full measurement and our response are there.

Legend: ✅ worked well · ⚠️ problem or friction · 💡 surprise worth documenting

## Nebius Token Factory (inference)

- **2026-10-11** ✅ The OpenAI-compatible `chat/completions` endpoint worked first time with plain Python
  `urllib`, no SDK needed. One API key covers inference and Sandboxes, which kept secrets management simple. (D22)
- **2026-10-11** ✅ The model list made picking the three Nemotron 3 sizes easy. All three answered a trivial
  prompt in about 1 second. (D23)
- **2026-10-11** ✅ Strict `json_schema` response format works on all three Nemotron tiers. Constrained output
  removed most parsing failures, and we kept our own validation only as a backstop. (D24)
- **2026-10-11** 💡 Reasoning tokens count toward `max_tokens`. With a low limit the call "succeeds" but the
  answer comes back empty or cut off, because the model spent the budget thinking. We now give each tier a large
  limit and watch `finish_reason == "length"`. **Suggestion:** call this out prominently in the docs, and/or offer a
  separate reasoning-budget parameter. (D24)
- **2026-10-12** ⚠️ The $1.00 trial credit covers only about 6 to 8 full runs of an agent like ours, because one
  Ultra call costs about 12 to 17 times a Nano call at the same token count. That is not enough for an 8-case
  evaluation. **Suggestion:** a larger default credit for hackathon projects, or a visible per-project spend meter. (D41)

## NVIDIA Nemotron models

- **2026-10-12** ✅ **Nemotron 3 Nano** classified 7 of 8 real GitHub issues correctly (bug vs feature vs question),
  including both trap cases. It is the right size for triage. (D42)
- **2026-10-12** ✅ **Nemotron 3 Super** wrote a plausible reproduction script every time, at about 2 to 4k tokens
  per call. (D42)
- **2026-10-12** 💡 Some Super scripts failed *for the wrong reason*: a missing required argument, or a Windows-only
  bug run on Linux. They looked like reproductions but were not. This is why a separate judge step and a code-only
  verification gate matter for any coding agent. (D42, D43)
- **2026-10-12** ✅ **Nemotron 3 Ultra** as a judge: 11 of 11 labelled reproduction attempts classified correctly,
  with no false accepts, at 1.6k to 8k tokens (about a cent) per call. (D44)
- **2026-10-12** ⚠️ In one case Ultra's free-text advice suggested asserting the *buggy* behaviour, the reverse of the
  convention in our prompt. The decision itself was right; only the advice was wrong. We now forbid that advice in the
  prompt. **Lesson for other builders:** check a model's explanations, not only its scores, when they feed a loop. (D44)
- **2026-10-11** 💡 Each tier returns its reasoning in a separate field from the answer, which made it easy to log and
  ignore reasoning safely. (D23)

## Nebius Token Factory Sandboxes

- **2026-10-11** ⚠️ `contree auth` accepted our key, but the project had Sandboxes disabled (no `list` permission).
  Activation needed an email to the Sandboxes team. **Suggestion:** say in the CLI error *how* to request access, or
  offer self-serve activation for hackathon projects. (D21, D25)
- **2026-10-13** ✅ Access arrived within about two days. The first probe was impressive: about 2 s per run, 4 CPUs,
  4 GB RAM, network to PyPI, changes carried between runs, failed commands keep their changes and exit code, a saved
  image can be reused for several branches, and our API keys were not visible inside. (D45, D46)
- **2026-10-13** ✅ The chain-of-images model (snapshot, branch, restore) fits our "fail before / pass after" check
  well: both runs start from identical states. (D20, D45)
- **2026-10-13** ⚠️ `python:3.12-slim` has no `git` (or `curl`), so the agent could not clone. `python:3.12` works and
  starts in about a second. **Suggestion:** mention this in the getting-started guide, since cloning is the first thing
  a coding agent does. (D46, D47)
- **2026-10-13** ⚠️ SDK gotcha: `apply_files({"/path": "text"})` treats a `str` as a *local file path* and fails with
  `OSError: Invalid argument`; content must be `bytes`. **Suggestion:** a type check with a clear error message. (D46)
- **2026-10-13** ✅ Output over 65,535 characters is truncated *and flagged* as truncated, so we could tell the user
  instead of silently losing output. (D46)
- **2026-10-13** ⚠️ A timeout is reported in two different ways depending on which timer fires first: an
  `OperationTimedOutError` exception, or a normal result with a negative exit code. Our hang detection missed the second
  form until a live test. **Suggestion:** one consistent timeout signal (for example a `timed_out` flag on the result). (D48)

## Other platforms

- **2026-10-14** ⚠️ Hugging Face Docker Spaces now need a PRO subscription (`402 Payment Required` when creating a
  Docker Space), so we moved the demo to Render's free plan, which still runs Docker.
