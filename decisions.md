# Decisions Log

A running record of every technical decision in this project: what we chose, why, what we rejected, and the concept behind it. It doubles as a study guide and as evidence of design thinking for the judges.

**How to use this file**
- New decisions are appended with the next number. Old entries are never rewritten; if a decision changes, add a new entry and mark the old one `Superseded by D#`.
- Each entry has: **Decision**, **Why**, **Rejected**, and a **Concept** note that explains the idea in plain terms.

## Tech and methods at a glance

| Area | Choice | Entry |
|---|---|---|
| Language | Python 3.10+ (target libraries are Python too) | D2 |
| Control flow | Hand-written state machine | D3 |
| Models | NVIDIA Nemotron via Nebius Token Factory, routed by tier (nano / super / ultra) | D4 |
| Correctness | Separate VERDICT call + code-only VERIFY gate | D5, D6 |
| Code execution | Nebius Sandboxes (ConTree, `contree-sdk` 0.3.6) behind a `Sandbox` interface | D7, D20 |
| Model output | JSON with schema validation and one retry | D9 |
| Testing | pytest with in-memory fakes | D7, D8 |
| GitHub access | Python standard library (`urllib`) | D13 |
| Only third-party runtime dependency | `jsonschema` | D19 |
| License | MIT | D16 |
| Evaluation | Real closed issues, checked by running the fix's own tests | D31-D35 |
| Reproduction | Script that asserts correct behaviour; outcome classified by code | D36-D40 |

---

## D1: Build the Issue-to-Repro Agent in the Coding and Agentic Engineering track
- **Decision:** An agent that takes a GitHub issue, reproduces the bug in a sandbox, and proposes a fix proven by a regression test.
- **Why:** Open-source maintainers face a real triage bottleneck (clear audience), the sandbox is central to the idea rather than decoration (strong use of Token Factory), and "paste an issue, get a proven repro" demos well in under 3 minutes.
- **Rejected:** Other ideas from the shortlist (Proof-Backed Code Reviewer, SQL Migration Verifier, UPI Money Coach). They were good, but this one best balances impact, demo clarity, and what two people can finish in 9 days.
- **Concept:** Judges score four things equally: technical implementation, design, impact, and idea quality. An idea is strong when one sentence explains it and a 3-minute video can prove it.

## D2: Python only, pytest-based projects only
- **Decision:** The agent supports Python libraries that use pytest. Other languages are future work.
- **Why:** Python tracebacks and pytest output are easy to parse, `pip` installs are predictable, and it is the language we both know. Supporting one language well beats supporting five badly.
- **Rejected:** Multi-language support (JS, Go, Java). Each adds its own install, test, and error-format problems, which would eat the schedule.
- **Concept:** **Scope control.** In a 9-day sprint, a narrow product that works reliably scores better than a broad one that sometimes works.

## D3: A hand-written state machine instead of an agent framework
- **Decision:** Each pipeline step is a *state* (FETCH, TRIAGE, SETUP, REPRO, VERDICT, FIX, VERIFY, REPORT). Each state is a function that returns the next state. `machine.py` loops until DONE.
- **Why:** The flow is known in advance, so a framework adds nothing but hidden behavior. A state machine is easy to debug (you always know which state you are in), easy to test (call one stage alone), and maps straight onto the UI timeline (one state = one row).
- **Rejected:** LangChain/LangGraph-style frameworks and a single free-form "agent loop where the model decides everything".
- **Concept:** A **state machine** is a program that is always in exactly one named state and moves between states by fixed rules. Autonomy lives *inside* a state (the model writes a repro script), but the *overall route* is controlled by code, which makes behavior predictable.

## D4: Route models by tier: nano, super, ultra
- **Decision:** Nano for cheap fast tasks (triage, comment drafting), Super for everyday work (setup, repro scripts, first patches), Ultra for the hardest reasoning (the verdict, and fixes that keep failing).
- **Why:** The hackathon explicitly rewards using the right Nemotron model for each job, and it saves credits and time. Most calls are easy; only a few need the strongest model.
- **Rejected:** Using one big model for everything (slow and expensive) or one small model for everything (weaker at the hard steps).
- **Concept:** **Model routing** is like staffing: you do not ask the senior engineer to rename a variable, and you do not ask the intern to design the architecture. Stage code never names a model; it asks for a *tier*, and one config file maps tiers to model IDs. That means changing models later touches one file.

## D5: VERDICT is a separate model call from REPRO
- **Decision:** One call writes and runs the repro script; a different call (Ultra) judges whether the output actually matches the bug the issue describes.
- **Why:** A model that writes a test tends to believe its own test worked. The most common failure of this kind of agent is "reproducing" a bug it invented: an `ImportError` or a typo in the script looks like a failure, but it is not the reported bug. An independent judge with the original issue in front of it catches that.
- **Rejected:** Letting the repro step declare its own success.
- **Concept:** **Separation of duties** (the same reason a person who writes code should not be the only person who approves it). Checking symptom against symptom is a distinct reasoning task from producing a script.

## D6: VERIFY is plain code, never a model
- **Decision:** After a fix is proposed, code runs the regression test on the original code (must fail) and on the patched code (must pass). Only then can the run reach CONFIRMED.
- **Why:** This is our guarantee that we never claim a fix that does not work. A model cannot be trusted to grade its own work; a test run is objective.
- **Rejected:** Asking a model "does this fix look correct?".
- **Concept:** **Fail-before / pass-after.** A good regression test must fail without the fix (otherwise it proves nothing) and pass with it. This rule is enforced in code so no prompt wording can weaken it.

## D7: Sandbox sits behind an interface, with a fake for tests
- **Decision:** All code execution goes through a `Sandbox` protocol (`create`, `exec`, `write_file`, `read_file`, `snapshot`, `destroy`). Implementations: local Docker (development), Nebius (the real thing), and an in-memory `FakeSandbox` (tests).
- **Why:** Two people work in parallel: my half can be built and tested before the Nebius sandbox exists. It also isolates platform risk: if the Nebius API surprises us, only one file changes.
- **Rejected:** Calling the Nebius SDK directly from stage code.
- **Concept:** An **interface** (here a Python `Protocol`) says *what* something can do without saying *how*. Code written against the interface works with any implementation. A **fake** is a simple stand-in used in tests so they run in milliseconds, offline, and deterministically.

## D8: Stages reach the outside world only through `Context`
- **Decision:** A `Context` object bundles the run, the sandbox, the LLM caller, and the event emitter. Stages call `ctx.sh(...)` and `ctx.model_json(...)`, never the sandbox or LLM directly.
- **Why:** Budget checks, event logging, output truncation, and JSON validation then happen in exactly one place instead of being copied into seven stages (where one would eventually be forgotten).
- **Rejected:** Stages calling `llm.call` and `sandbox.exec` themselves.
- **Concept:** **Dependency injection**: the things a function needs are handed to it rather than created inside it. That is what lets tests hand it fakes.

## D9: Models must return JSON that matches a schema; invalid output gets one retry
- **Decision:** Every model call that feeds program logic declares a JSON schema. The reply is parsed and validated; if invalid, the exact error is sent back to the model for one more try; if that fails too, the run ends with a clear error.
- **Why:** Programs cannot act reliably on free text. Validation turns "the model said something odd" into a handled, visible event instead of a crash three stages later. Feeding the error back usually fixes formatting mistakes on the first retry.
- **Rejected:** Regex-parsing free-text answers; retrying many times (burns budget on a model that is not going to comply).
- **Concept:** **Structured output** plus **validation at the boundary.** Treat model output like user input: never trust it until it has been checked.

## D10: The budget lives in `run.budget`, with two different kinds of limit
- **Decision:** Per-stage *attempt* limits (e.g. 4 repro tries) return `False` when spent, and the stage decides what that means. *Global* limits (total tokens, wall-clock time) raise `BudgetExceeded`, and the machine ends the run as ERROR. Token use is also tracked per tier.
- **Why:** Running out of repro attempts is a normal, meaningful result ("could not reproduce"). Running out of tokens or time is an emergency stop. They deserve different handling. The budget is a plain dict so it saves straight to storage and the UI can show it; per-tier counts let us show the routing story and cost.
- **Rejected:** One shared counter for everything; unlimited retries.
- **Concept:** **Bounded loops.** Any loop that includes an LLM must have a hard limit, because models can loop forever on the same mistake.

## D11: Every run ends in a defined outcome, including `ERROR`
- **Decision:** Outcomes are CONFIRMED, NOT_REPRODUCED, NEEDS_MAINTAINER, SETUP_FAILED, and ERROR (a deliberate addition to the original list). The machine catches crashes, budget exhaustion, runaway loops (40-step limit), and runs that end with no outcome, and converts each into ERROR. The sandbox is always destroyed.
- **Why:** In a live demo, a hung or crashed run is the worst case. A run that always ends cleanly with a stated reason is demo-safe and honest.
- **Rejected:** Letting exceptions propagate.
- **Concept:** **Fail closed and fail visibly.** Always release resources (`try/finally`) and always tell the user what happened.

## D12: Clone the repo in SETUP, not in FETCH
- **Decision:** FETCH only reads the issue through the GitHub API. The repo is cloned into the sandbox in SETUP.
- **Why:** Feature requests and questions stop at TRIAGE and never need a sandbox, so they cost zero sandbox time.
- **Rejected:** Cloning up front.
- **Concept:** **Do the cheap check first** before spending on the expensive step.

## D13: GitHub access with the Python standard library
- **Decision:** `github.py` uses `urllib` and an optional `GITHUB_TOKEN`. The HTTP function is passed in as a parameter (`http_get`) so tests can fake it.
- **Why:** Three read-only API calls do not justify adding a dependency. Injecting the HTTP function keeps tests offline.
- **Rejected:** `requests` or PyGithub (extra dependency, no benefit here).
- **Concept:** Unauthenticated GitHub API calls are rate-limited per IP address (we hit this while testing), so set `GITHUB_TOKEN` for real use.

## D14: Prompts live in `.md` files, not inside Python strings
- **Decision:** One prompt file per stage under `agent/prompts/`, loaded by `ctx.prompt(name)`.
- **Why:** Prompts are the part we will edit most. Keeping them separate makes them readable, easy to diff in git, and editable without touching logic.
- **Concept:** **Separate what changes often from what changes rarely.**

## D15: Events are emitted as `emit(type, payload)`; the store assigns `seq` and `ts`
- **Decision:** Agent code reports "something happened" through a callback and does not number or timestamp events itself.
- **Why:** One component (the store) owns ordering, so numbers cannot collide, and the agent stays simple. The UI timeline is built entirely from the stored event stream, which also allows replaying a past run in the demo without calling any model.
- **Status:** To be confirmed with the platform teammate.
- **Concept:** **Event-driven UI.** The agent produces a log of facts; the interface is just a view of that log.

## D16: MIT license, `src/` layout, and the shared files as contracts
- **Decision:** MIT license from day one. Code lives under `src/itr/`. `models.py`, `sandbox/base.py`, and `llm.py` are *contract files* that both teammates build against and change only by agreement.
- **Why:** The hackathon requires an open-source license visible on the repo. The `src/` layout prevents accidentally importing code that is not installed. Contract files let two people work in parallel without breaking each other.
- **Concept:** **Agree on interfaces first, implement later.**

## D17: Only issues classified as `bug` proceed past TRIAGE
- **Decision:** Features, questions, duplicates, and unclear reports end as NEEDS_MAINTAINER.
- **Why:** The agent's value is proving bugs. Trying to "reproduce" a feature request wastes budget and risks nonsense output.
- **Trade-off:** A vaguely written but real bug may be classed "unclear" and skipped. The triage prompt tells the model to call anything with a traceback or a clear wrong result a bug, even if steps are missing. We will measure how often this misfires in the evaluation.

## D18: Work split by folder with one owner each
- **Decision:** One teammate owns the agent core (`agent/`, `github.py`); the other owns platform and product (sandbox, LLM client, store, API, UI, eval, docs).
- **Why:** Separate folders mean almost no merge conflicts. The interface contracts (D16) let each person build against fakes of the other's half.

## D19: Keep dependencies minimal
- **Decision:** One runtime dependency (`jsonschema`); `pytest` only for development.
- **Why:** Fewer dependencies mean fewer install problems for judges following the README, and a smaller surface for things to break. `jsonschema` is worth it because hand-writing schema validation would be slower and buggier.

## D20: Treat the Nebius Sandbox as chained images, not a live shell (researched from the docs)
- **Decision:** The `NebiusSandbox` adapter will keep a *current image*. Each command runs on that image; when we want its changes kept (installs, a clone, a patch) the result becomes the new current image. `snapshot()` returns the current image ID. SETUP does the slow work once (clone, install) and saves that image as the **environment checkpoint**. REPRO and FIX then start from that checkpoint, and VERIFY runs the regression test on the original checkpoint and on a patched copy of it (two branches from the same starting point).
- **Why:** Per the Nebius docs, Sandboxes (a beta product built on ConTree) is a VM-isolated sandbox with git-like branching: you fork from a checkpoint, explore in parallel, and roll back. The SDK's `run()` does not keep a VM alive between calls; filesystem changes carry forward only when you persist them into a result image. Designing around that avoids fighting the platform.
- **Why it helps us:** Fail-before / pass-after (D6) becomes cheap and trustworthy: both runs start from byte-identical environments. Retries in REPRO and FIX can roll back to a clean checkpoint instead of rebuilding.
- **Rejected:** Pretending it is a persistent terminal session (one long-lived shell). That is not how the API works and would break on retries.
- **Concept:** A **checkpoint** (snapshot) is a saved, immutable copy of a system's state. **Branching** means starting several independent runs from one checkpoint. It is the same idea as git branches, applied to a whole filesystem.
- **Open:** To confirm with a real probe (see D21): network access for `git clone` and `pip install`, and the exact fields on a run result. The docs and example repos show package installs and a network-enabled sandbox, but we have not run it ourselves yet.

## D21: Probe the real sandbox before writing SETUP; propose a `restore()` on the Sandbox contract
- **Decision:** Before SETUP is written against Nebius, one of us runs a short probe (apt/git/pip/pytest inside a sandbox) and records the result here. Meanwhile SETUP is developed against the local Docker sandbox and the fake. We also propose adding `restore(snapshot_id)` to the `Sandbox` protocol so the agent can roll back to a checkpoint (D20).
- **Why:** Platform surprises are the biggest schedule risk, and the platform is in beta. A 10-minute probe now is cheaper than discovering a network block on Oct 18. `restore()` is the operation the docs highlight as the platform's main strength, and our retry loops need it.
- **Status:** `restore()` is a proposed contract change and is not yet agreed with the platform teammate. Probe result (Oct 11): `contree auth` accepted the key but reported Sandboxes are disabled on our project (no `list` permission), so no sandbox command can run until Nebius enables it. See D25.
- **Concept:** **Spike / probe**: a small throwaway experiment whose only job is to answer one risky question early.

## D22: Credentials live in a gitignored `.env`; one Nebius key serves inference and Sandboxes
- **Decision:** Secrets go in `.env` (gitignored). `.env.example` (committed) lists every variable with no values. A small standard-library loader (`itr/config.py`) reads `.env` into the environment, and real environment variables override the file. `scripts/check_setup.py` verifies the setup and masks secrets in its output. Variables: `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID`, `NEBIUS_BASE_URL`, `MODEL_NANO/SUPER/ULTRA`, `GITHUB_TOKEN`.
- **Why:** Keys committed to a public repo (a hackathon requirement) get scraped within minutes. Environment variables are the standard way to keep secrets out of code, and letting the real environment win means a deployed endpoint can inject secrets with no file. One Nebius key covers both Token Factory inference and Sandboxes per the docs, so there is one thing to protect. Model IDs are configuration, not code (see D4).
- **Rejected:** Hard-coding keys; `python-dotenv` (a 25-line loader does the job and keeps D19's one-dependency rule).
- **Rule:** Never pass the Nebius or GitHub token into the sandbox itself. Sandboxed code is exactly the code we do not fully trust, so it must never be able to read our credentials.
- **Concept:** **Secrets management.** Code is public and versioned; secrets are private and local. Anything that must never leak lives outside the repo and is injected at run time.

## D23: Tier-to-model mapping (from the models visible on our Token Factory account)
- **Decision:** `MODEL_NANO=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `MODEL_SUPER=nvidia/nemotron-3-super-120b-a12b`, `MODEL_ULTRA=nvidia/Nemotron-3-Ultra-550b-a55b`.
- **Why:** These are the three Nemotron 3 sizes the account can see (found with `scripts/check_setup.py`), and they line up with the three jobs in D4: cheap and fast, everyday, and hardest reasoning. A fourth ID, `nvidia/Nemotron-3_5-Lightning`, is also visible; we have not evaluated it and are not using it yet.
- **Concept:** In names like `30B-A3B`, the first number is the model's total parameters and the second (after `A`) is the parameters *active* for each token. These are mixture-of-experts models: only a small slice of the network runs per token, which is why a big model can still be fast. Read the active number as a rough guide to speed and cost.
- **Result (Oct 11, `scripts/check_models.py`):** all three tiers answered in about 1 second on a trivial prompt, and all three support strict `json_schema` output. Each also returns its reasoning in a separate field from the answer.

## D24: Use strict `json_schema` mode, and keep validation and retry as a backstop
- **Decision:** `llm.py` sends `response_format={"type": "json_schema", ...}` for every call that has a schema. Our own validation and single retry (D9) stay in place anyway. Callers must give generous `max_tokens`, and logic reads only the answer field, never the reasoning field.
- **Why:** The probe showed every tier supports strict schema mode, so the model is constrained to our structure at generation time and we avoid most parsing failures. Schema mode does not cover everything: a reply can still be cut off by the token limit, or be valid JSON with a nonsense value, so the D9 check remains the safety net.
- **Watch out:** These models think before answering, and thinking tokens count toward `max_tokens`. A trivial test used about 50 tokens, but real tasks (writing a repro script, a verdict) can think for thousands. If `max_tokens` is too low, the answer can come back empty or truncated even though the call "succeeded". Log the reasoning-token share per call and size limits per stage.
- **Rejected:** Prompt-only JSON ("please reply in JSON"), which relies on the model behaving.
- **Concept:** **Constrained decoding.** Instead of asking the model nicely, the server only lets it generate tokens that keep the output valid against the schema. It guarantees the *shape* of the answer, not that the *content* is right.

## D25: Request Sandboxes activation now, and keep building against the local Docker sandbox meanwhile
- **Decision:** Email the Sandboxes team (contree@nebius.com) with the project ID and the exact warning, and ask in the hackathon channel too. Until access is on, develop SETUP and the later stages against `LocalDockerSandbox` and `FakeSandbox`. Set a checkpoint on **Oct 14**: if Sandboxes are still off by then, we ship on local Docker for development and keep Nebius inference as our live platform use, and we keep chasing activation for the final demo.
- **Why:** The docs say a token can be valid while the Sandboxes permission is missing, and that activation goes through the Sandboxes team. Our interface-first design (D7) means this blocks only the final adapter, not the agent logic. The hackathon's definition of "runs on Token Factory" is met by runtime calls to the inference API, so eligibility is safe, but the track's pitch (code running in Token Factory Sandboxes) is much stronger with real Sandboxes, so activation is worth pushing for.
- **Rejected:** Waiting idle for the reply, or redesigning the agent to avoid sandboxes.
- **Concept:** **Risk with a deadline.** For any external dependency you do not control, ask early, build around it in parallel, and set a date at which you switch to the fallback.

## D26: Public GitHub repo from day one, with a check that no secret is ever staged
- **Decision:** The project lives in a public GitHub repo (MIT license, detected by GitHub from the `LICENSE` file). Before the first commit and before every push we confirm `.env` is ignored and not tracked. Work happens on short-lived branches (`a/...`, `b/...`) merged by pull request into `main`, which must always run; the teammate is added as a collaborator.
- **Why:** The hackathon requires a public, open-source repo with a visible license, and a repo created late tends to have messy history and, worse, committed secrets. Git remembers everything: a key committed once stays in history even if the file is later deleted, so prevention is the only reliable protection (and if one ever leaks, revoke it, because deleting it does not help).
- **Rejected:** Pushing everything first and cleaning up later; committing straight to `main`.
- **Concept:** A **commit** is a saved snapshot of the project, a **branch** is a separate line of work, a **remote** is the copy on GitHub, and a **pull request** is a proposal to merge one branch into another, with a chance for review.

## D27: SETUP tries a fixed plan first and asks the model only after a failure
- **Decision:** SETUP clones the repo, checks out the right version, and builds a virtual environment from a fixed install plan based on the files in the repo. No model is called on the first attempt. If an install command or the final check fails, the `super` model gets the error output and the config files and proposes the next commands (max 6, JSON with a `give_up` flag). The loop is limited by `max_attempts["setup"]` (4 by default). Running out of attempts, a failed clone, or a bad checkout ends the run as SETUP_FAILED with the error attached.
- **Why:** Most Python projects install the same few ways, so a model call on every run would be slow and cost credits for nothing. Real failures (a missing system library, a test extra, a pinned old dependency) are exactly what a model reading the error output is good at. This is the same cheap-first idea as D12.
- **Rejected:** Asking the model to write the whole install plan up front, and a fixed plan with no fallback.
- **Concept:** **Heuristics first, model as fallback.** Deterministic code is fast, free and predictable; use the model only for the long tail that code cannot anticipate.

## D28: Success means the tests can be *collected*, and the environment is checkpointed
- **Decision:** SETUP only succeeds when `pytest --collect-only` runs (exit 0, or 5 for "no tests found"). Then `sandbox.snapshot()` is saved in `run.env["snapshot"]`.
- **Why:** "pip install exited 0" is not enough. A real run on `pallets/itsdangerous` installed fine and then failed at collection because a test-only dependency was missing. Collection catches that without running any test. The snapshot is the checkpoint from D20: REPRO, FIX and VERIFY all start from the same clean state.
- **Concept:** **Smoke test.** A cheap check that the thing is basically alive before spending effort on it.

## D29: Version selection and the `ref_pinned` flag
- **Decision:** If the issue claims a version and the repo has a matching tag (`v1.2.0`, `1.2.0`, `release-1.2.0`...), SETUP checks out that tag, because a bug reported against 1.2.0 may not exist on today's main. If there is no matching tag, it stays on the default branch and records why in `run.env["ref_note"]`. If `issue["ref_pinned"]` is true (the eval harness sets this to the commit just before a known fix), the ref is used exactly as given and never overridden.
- **Why:** Reproducing against the wrong version is a common reason an agent wrongly reports "cannot reproduce". Evaluation on closed issues only works if the code is exactly the pre-fix commit.
- **Contract note:** `ref_pinned` is a new optional key in the `issue` dict. Tell the teammate who owns `eval/`.

## D30: Read how the project declares its test dependencies (found by a real run)
- **Decision:** The fixed plan reads `pyproject.toml`. It installs `[dependency-groups]` (PEP 735) with `pip install --group <name>` and `[project.optional-dependencies]` with `-e ".[name]"`, preferring the names tests, test, testing, then dev. It also still installs `requirements*.txt` files when they exist. It uses `tomllib` when available (Python 3.11+) and a small hand-written scanner on 3.10.
- **Why:** I ran SETUP for real (a local shell, not a mock) on two public repos. The first version failed on `pallets/itsdangerous`: it declares test dependencies as a dependency group, which my plan did not know. Mocks cannot find this kind of gap; only a real repo can. After the fix both `itsdangerous` and `requests` set up on the first attempt with zero model calls.
- **Limits:** `--group` needs pip 25.1 or newer; the plan upgrades pip first. Not yet tested: the real Nebius sandbox image (it may lack `python3-venv` or `git`; the model fallback and prompt cover `apt-get install`), and projects that need system libraries or a GPU.
- **Concept:** **Test against reality early.** A fake proves your logic matches your assumptions; a real example tests the assumptions.

## D31: The evaluation set is real closed issues, each one checked by running code
- **Decision:** `eval/cases.yaml` holds 8 real, closed GitHub issues from tabulate, cachetools and click, each pinned to the commit just before its fix. For every case that has a fix with tests, `scripts/verify_case.py` proves the case is real: the tests the fix added FAIL on the pinned commit and PASS on the fix. Cases were found by mining each repository's own history for small "fixes #N" commits that change both source and tests (GitHub search is not reachable from the build sandbox), then verified. One candidate (more-itertools #813) was dropped because its test passed even before the fix.
- **Why:** An agent can only be graded against a known answer. A fix's own regression test is the most objective ground truth available, and checking it mechanically means no case is a guess. The set is deliberately varied so the results show *where* the model's understanding breaks, not just an average: crash on edge input (tabulate-180), wrong output with no error (tabulate-241), a hang (tabulate-399), a stateful logic bug (cachetools-405), flag semantics in a big library (click-1925), a bug visible only under `python -bb` (click-2877), plus two traps (D34).
- **Rejected:** Cherry-picking easy issues; copying issue text into the repo (it goes stale and hides what FETCH really sees).
- **Limit:** I could not read the issue pages while building this (the fetch tool needs per-URL approval), so each case's description comes from the fix commit and its test. Open each issue link once and confirm it reads as described.
- **Concept:** **Ground truth.** A test set is only useful if the correct answer is known independently of the thing being tested.

## D32: `as_of` hides comments written after the fix
- **Decision:** Each case has `as_of` (the fix commit's date). `fetch_issue(..., as_of=...)` drops comments created after it. Timestamps are compared as real instants, so time zones cannot cause a wrong drop. Comments with no timestamp are kept.
- **Why:** Closed issues often contain the maintainer explaining the fix. If the agent can read that, a successful run proves nothing. This keeps the evaluation honest.
- **Limit:** Comments written shortly before the fix can still contain hints, and an issue's body may have been edited later. This makes the evaluation fairer, not perfect.
- **Concept:** **Data leakage.** Information from the future must not be visible to the system being tested.

## D33: FETCH must never overwrite fields the caller preset (bug found while testing)
- **Decision:** FETCH now saves `ref`, `ref_pinned` and `as_of` before merging fetched data and puts them back afterwards. A new test covers the real fetch path, and I confirmed it fails when the bug is reintroduced.
- **Why:** The fetched data included `ref` = the repo's default branch. For eval cases that silently replaced the pinned commit, so every run would have tested today's code, where the bug is already fixed, and reported "cannot reproduce". The existing test did not catch it because it preset a `title`, which skips the fetch entirely.
- **Concept:** **A test that takes a shortcut does not test the main path.** After writing a test, break the code on purpose (a mutation check) and make sure the test notices.

## D34: Two trap cases measure the mistakes that matter most
- **Decision:** `click-3186` is a Windows-only bug whose fix has no test; the correct outcome in a Linux sandbox is NOT_REPRODUCED (NEEDS_MAINTAINER also accepted), and CONFIRMED means the agent faked a reproduction. `click-1629` is a feature request ("accept on/off for BOOL"); the correct outcome is NEEDS_MAINTAINER, reached at TRIAGE without touching the sandbox.
- **Why:** A system that always says "reproduced" would score perfectly on the six real bugs. Traps measure false positives, which is what decides whether a maintainer would trust the tool (this is what D5 and D6 are designed for).
- **Metrics to report in the README:** reproduction rate on real bugs; false-positive rate on traps; per-category results; tokens by tier (nano / super / ultra) and cost per case.

## D35: Known hard parts the cases are meant to expose
- **Hangs (tabulate-399):** the bug is an infinite loop, not an exception. REPRO must run scripts with a timeout and treat "timed out" as evidence. The fix's own test would hang the test run, so VERIFY also needs timeouts.
- **Optional dependencies (tabulate-399):** the fix's tests are skipped unless `wcwidth` is installed, so a repro without it silently passes. SETUP's fixed plan will not install it; the agent has to notice the skipped test or the wide-character clue in the issue.
- **Interpreter flags (click-2877):** a plain script run shows nothing; the repro has to use `python -bb`.
- **Stateful bugs (cachetools-405):** the repro needs a specific sequence of operations and a `getsizeof` function.
- **Expectation:** the model will not solve all of these. Reporting honestly where it fails is part of the result.

## D36: A repro script asserts the CORRECT behaviour, so it fails while the bug exists
- **Decision:** REPRO asks the model for a script that checks what the reporter *expected*. While the bug exists the script must fail (an exception, a failed assert, or a hang); once fixed it exits 0. It prints the real value first, so the output is evidence. What happened when it ran is classified by code, not by a model: `failed` (non-zero exit) and `hang` (killed by the timeout) are candidate reproductions that go to VERDICT; `passed` (exit 0) means the bug did not show and the model retries with that result in its prompt; `script_error` (a SyntaxError in our own script) is our mistake and is retried without bothering the judge.
- **Why:** One convention gives a binary, mechanical signal for every kind of bug, and the same script becomes the seed for the regression test in FIX/VERIFY. Separating "did it fail" (code) from "did it fail for the right reason" (VERDICT, a separate model call, D5) keeps each judgment simple and stops the agent grading its own work.
- **Proof it works:** on all six real bugs, the correct script fails on the buggy commit and passes on the fixed one (see D40).
- **Concept:** **Test oracle.** The script encodes an expectation, so a failure is information.

## D37: A hang is a result; the timeout is 30 seconds
- **Decision:** Scripts run with a 30-second limit. A run that hits it counts as reproducing a hang (the output says the call never returned). The sandbox adapters must kill the whole process group on timeout, not only the shell.
- **Why:** tabulate-399 is an infinite loop. With no timeout it would hold the whole run forever, and with a timeout but no meaning attached it would look like an error. When I first tried it in a plain shell the grandchild process survived the timeout, which is why the process-group requirement is written down.

## D38: Anything the model controls that reaches a shell is an allow-list or a validated pattern
- **Decision:** Interpreter flags must come from a fixed list (`-b`, `-bb`, `-O`, `-OO`, `-X dev`, `-X utf8`, `-W error`, `-W default`); `-I` is excluded because it ignores `PYTHONPATH` and would import the wrong copy of the library. Extra packages must match a plain requirement pattern (at most 3, each quoted); anything else (`x; rm -rf /`, `$(whoami)`, `-e .`) is refused and reported back to the model. Empty or oversized scripts cost an attempt but never crash the run.
- **Why:** Issue text is written by strangers, and the model reads it. A malicious issue could try to steer the model into a harmful command. The sandbox limits the damage; these checks stop it reaching the shell at all.
- **Concept:** **Defence in depth** and **least privilege**: validate at the boundary even when something else (the sandbox) also protects you.

## D39: Project install is essential; test dependencies are best-effort; a broken test suite does not block reproduction (found by real runs)
- **Decision:** In SETUP, installing the project itself is essential. Dependency groups, extras and `requirements*.txt` files are best-effort: a failure is recorded as a warning and the run continues. The collection smoke check runs with `-p no:warnings`. If collection still fails after the attempt budget but the project is installed (editable, or the repo has no packaging files and Python works), SETUP continues with `env["tests_collect_ok"] = False` instead of failing. It still fails if the clone, checkout, or project install fails.
- **Why:** Running click at a 2021 commit showed two problems: its pinned `requirements/dev.txt` includes an old `pyyaml` that does not build on a modern Python, and its config turns warnings into errors, so a newer pytest could not even collect its tests. Neither stops the library from being imported, and reproducing a bug needs the library, not a healthy test suite. A scripted mock could not have found either problem.
- **Cost:** FIX and VERIFY must read `tests_collect_ok` and handle `False` (use their own test file and run it with `-p no:warnings`).
- **Concept:** **Be strict about what is essential and forgiving about what is not.**

## D40: What was proven, and what was not
- **Proven (real shell, real repositories, a stand-in for the model):** SETUP then REPRO on the pinned commits of all six real-bug cases. The correct script fails on the buggy commit (IndexError; unescaped pipe; hang caught by the timeout; wrong cache size; wrong flag value; BytesWarning under `-bb`) and passes on the fixed commit. This tests the machinery: cloning old versions, installing, running with the repo first on the import path, flags, extra packages, timeouts, classification.
- **Not proven:** whether the real Nemotron models write scripts like these. That is what `scripts/try_repro_prompt.py` measures: it prints the model's triage and script beside the known answer without executing anything.
- **Why say it:** a passing test that quietly stands in for the model proves less than it looks like. The write-up for judges must say which numbers come from real model runs.

## D41: Budget in dollars as well as tokens (from the real price list)
- **Decision:** Each run has a cost cap (`max_cost_usd`, default $0.15) next to the token and time caps. Cost is computed per call from a price table in `budget.py`, with input and output priced separately (USD per 1M tokens: nano 0.06/0.24, super 0.30/0.90, ultra 1.00/3.00, read from the Token Factory catalog on 2026-10-12). Default token cap lowered from 400k to 300k. An unknown tier is priced as ultra so a mistake overestimates rather than underestimates.
- **Why:** The account has a $1.00 trial credit. A token count alone says nothing about money: a million Ultra output tokens costs about 12 times a million Nano ones, and reasoning tokens bill as output. A rough run (cheap triage, a few Super attempts, one Ultra verdict) costs about $0.10 to $0.15, so $1 is only about 6 to 8 runs and the 8-case eval would not fit. Hackathon credits are needed, and the cap stops one runaway run from using up the rest.
- **Rejected:** A cap on tokens only (blind to which model spent them). Hard-coding prices in several places (they live in one table, and the cost shown in the UI uses the same one).
- **Concept:** **Budget in the unit you actually pay in.** Tokens are a proxy for cost, and the proxy breaks when prices differ by 10x across tiers.

## D42: First results from the real Nemotron models (try_repro_prompt on all 8 cases), and what we changed
- **Observed (real Nebius calls, 2026-10-12):** triage right on 7 of 8 (both traps correct). REPRO produced a plausible script every time, each call ~2-4k tokens (a fraction of a cent). I then ran the model's actual scripts on the real pinned commits (buggy and fixed):
  - **Good:** cachetools-405 and click-2877 fail on the buggy commit for the right reason and pass on the fix.
  - **Passes on both (no repro):** tabulate-399. The model did not ask for `wcwidth`, so the script cannot hang. The retry loop shows it "passed" next; whether the model then finds `wcwidth` is the real test.
  - **Fails on both (wrong reason):** click-1925. The script calls the command with `--switch` and no value; a non-flag bool option rightly demands a value, so it fails before AND after the fix, and it never asserts `is_bool_flag`. click-3186 (trap): `click._termui_impl` is not imported by `import click`, so it dies with an `AttributeError` on both commits, which is not the reported bug.
  - **Broken script:** tabulate-180 came back as one line with literal `\n` sequences (a SyntaxError).
- **Changes:** (1) `unescape_newlines`: a script with no real newline but two or more literal `\n` is unescaped, once, before running. (2) The triage prompt now says broken output for input the library claims to support is a bug even if phrased as a request; tabulate-241 ("escape pipes") had been classed as a feature.
- **Why not more prompt fixes:** the click-1925 and click-3186 scripts fail *mechanically* ("failed"), so only a judge reading the failure can tell the reason is wrong. They are the first real test cases for VERDICT and show why REPRO alone must not decide. The fail-before/pass-after gate (D6) is the second safety net.
- **Caution:** the triage tweak is tuned on one case. The eval must show it did not turn click-1629 (a real feature request) into a bug.
- **Concept:** **Test the real thing early.** A model that looks fine in a printout can write scripts that fail for the wrong reason; running them on real code showed it in minutes.
