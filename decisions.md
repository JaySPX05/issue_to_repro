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

## D43: VERDICT: the model answers questions, code makes the decision
- **Decision:** VERDICT (ultra) is shown the issue, the script, the flags, the platform and the real output, and returns facts, not a verdict: `failure_matches_symptom` (is the observed failure the reported one?), `would_pass_if_fixed` (if the library behaved as the reporter expects, would this script exit 0?), `confidence`, plus feedback for the next try. The rule is in code (`decide`): the run is accepted only if both booleans are true and confidence is at least 0.6. A model that says "matches" while admitting the script would still fail after a fix is overruled. A rejection with repro attempts left goes back to REPRO with the feedback (REPRO already puts it in its prompt as `judge_feedback`); with none left the outcome is NOT_REPRODUCED.
- **Why:** The real-model run (D42) showed scripts that fail mechanically for the wrong reason: click-1925 calls a command without a required value, so it fails before and after any fix; click-3186 is a Windows-only bug, and the script dies on Linux with an AttributeError of its own making. A single "does this match? yes/no" question invites a yes because the output looks like an error. `would_pass_if_fixed` forces the judge to imagine the fixed library, which exposes that kind of mistake. Splitting the answer into two checkable facts also lets the code, not the model's mood, make the call.
- **Rejected:** letting the model return the final "matches_issue" (it can talk itself into yes); a second model call to double-check every accept (doubles the cost for the most expensive tier; the fail-before/pass-after gate in VERIFY is the second safety net); mechanical checks on the exception type alone (an unrelated crash and the reported crash can both be `AttributeError`).
- **Cost:** one ultra call per failed attempt, bounded by the repro attempt budget (at most 4 calls). The ultra rate is about 17 times nano, but a call is a few thousand tokens.
- **Measuring it:** `eval/verdict_cases.json` holds 11 real attempts run on the buggy commits (5 correct, 6 wrong for a known reason, each with a label). `scripts/try_verdict_prompt.py` sends them to the real Ultra model and reports correct/11, FALSE ACCEPTS (a wrong script approved: the dangerous kind) and false rejects (a good script refused: costs a retry). Not yet run against the real model.
- **Not proven:** the machinery and the decision rule are tested (14 tests, mutation-checked). Whether Ultra judges the 11 fixtures correctly is unknown until the script is run.
- **Concept:** **Ask for evidence, not a verdict.** Counterfactual questions ("would it pass if the bug were fixed?") are harder to fool than "is this right?"; and keep the decision in code so it is auditable.

## D44: First real Ultra run of the judge: 11/11, then two fixes it exposed
- **Observed (real Ultra, 2026-10-12):** 11 of 11 fixtures judged correctly: no false accepts, no false rejects. Both real wrong-reason scripts were rejected for the right reason (click-1925: the failure matches but `would_pass_if_fixed` was false; click-3186: AttributeError instead of the reported failure). Each call used 1.6k to 8k tokens, about a cent.
- **Caveat:** 11 fixtures is a small sample, and 4 of the 6 bad scripts are obvious (typos, a missing argument). The subtle failures will show up in the full eval, so the false-accept count there is the number to report.
- **Problem 1:** for click-1925 the judge advised "assert the buggy condition and exit cleanly". That reverses the convention in D36 (a script fails while the bug exists). Fed back to REPRO it would produce scripts that PASS on buggy code. The prompt now states the convention in the feedback instruction and forbids advising an assertion on the buggy behaviour.
- **Problem 2:** for click-3186 (a Windows-only bug on a Linux sandbox) the judge saw the platform problem but could only ask for another try. New field `cannot_reproduce_in_sandbox`: when the judge sets it with confidence of at least 0.8 and the script is rejected, the run ends at once as NOT_REPRODUCED. The bar is higher than for accepting (0.8 against 0.6) because giving up on a real bug is also an error, and the flag cannot override an accepted reproduction.
- **Rejected:** hard-coding "Windows-only means give up" in code (the judge reads the issue; keywords would be brittle).
- **Concept:** **Read the model's explanations, not only its scores.** The decisions were right, but the advice in the reasoning field would have pushed the next step the wrong way. Feedback that is fed back into a loop is an input to the next stage and has to be checked like one.

## D45: Nebius Sandboxes access granted; what the SDK source shows, and the probe that checks the rest
- **Status:** beta access approved on the project (2026-10-13). Before writing the real adapter, `scripts/probe_sandbox.py` measures what the sandbox can do. Everything below marked "(source)" comes from reading `contree-sdk==0.3.6`; everything else is a question the probe answers.
- **How it works (source):** a sandbox is a chain of images. `image.run(shell=..., timeout=..., disposable=False).wait()` runs the command in a fresh microVM started from that image and returns a NEW image holding the result (`.uuid`, `.exit_code`, `.stdout`, `.stderr`, `.result.elapsed_time`, `.result.cost`, `.result.truncated`). A run with the default `disposable=True` keeps nothing and cannot be continued. Files are put in with `apply_files({path: content})` (a new image) and read with `image.read(path)`. A saved state is just the image's uuid, and `client.images.use(uuid)` starts a new chain from it.
- **Consequences for our adapter (source):**
  - `exec(cmd)` = `run(shell=cmd, disposable=False)`, then keep the returned image as the current one. A command that exits non-zero is returned as a normal result (the SDK raises only for infrastructure problems), so `exit_code` maps straight onto `ExecResult`.
  - `snapshot()` = the current image's uuid; `restore(id)` = `images.use(id)`. This settles D21: `restore()` is cheap, so a failed fix attempt can roll back to the state after SETUP instead of re-cloning and re-installing.
  - Every run is a new VM, so no process survives from one `exec` to the next. The "kill the whole process group on timeout" requirement from D37 is then the platform's job; the adapter only has to map the timeout error to `ExecResult(timed_out=True)`. The probe confirms how a timeout appears.
  - Auth is two environment variables, `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID`, read by the SDK. Our own keys are never passed to a sandbox command (the probe checks they are not visible inside).
- **Open questions the probe answers:** git and `venv` present in `python:3.12-slim`? Network to GitHub and PyPI? How is a timeout reported? Does a failed command keep its changes? CPU, RAM and disk size? Output limit (default cut at 65,535 bytes)? And the real SETUP of click at the pinned commit with timings.
- **Why probe first:** the adapter is the one part we cannot test with fakes. Writing it from documentation alone risks building on a wrong assumption (for example a missing `git`), and we have seven days.
- **Concept:** **Measure the platform before building on it.** A short script that asks the real system its limits is cheaper than a design that assumes them.

## D46: First real probe of the Nebius Sandbox (2026-10-13): what it showed
- **Works as the source suggested:** a run costs about 2 s of wall time and a fraction of a cent (the beta is free); 4 CPUs, 4 GB RAM, huge disk, user `root`; changes carry over between runs; a failed command still keeps its changes and its exit code is returned (no exception); a saved image id can be reused for several branches; restoring a saved state works; each run is a new VM (0 leftover processes); our API keys are not visible inside; output is cut at 65,535 characters and flagged `truncated`.
- **A timeout is an exception**, `OperationTimedOutError`, raised about 1 s after the limit (8 s limit, 9 s observed). The adapter turns it into `ExecResult(stdout="", stderr="", exit_code=-1, timed_out=True)`. Open: whether the changes made before the timeout survive (the failed run produces no new image, so assume NOT: the adapter keeps the previous image and the stage treats the command as having done nothing).
- **Network works** to PyPI and ordinary HTTPS sites. GitHub could not be tested because the slim image has no `git`.
- **Problem 1: `python:3.12-slim` has no `git` and no `curl`.** SETUP cannot clone. Options (decided after the second probe run): install git with `apt-get` once, bake it into a saved base image and start every run from that image id; or use a larger image that already has git. Baking is better either way: pay the cost once, not per run.
- **Problem 2 (my probe, and a rule for the adapter): file content must be bytes.** `apply_files({"/path": "text"})` treats a `str` as a path on the local machine and fails with `OSError: Invalid argument`. `write_file(path, content: str)` must encode to bytes.
- **Adapter rules that follow:** every exec uses `disposable=False` and keeps the returned image; `write_file` uses `apply_files({path: content.encode()})` (a new image, so each write costs a run); `read_file` uses `image.read(path)`; `snapshot()` returns the image uuid; `restore(id)` uses `images.use(id)`.
- **Concept:** **Immutable state is easy to roll back.** Because every command makes a new image and the old one still exists, undo is free: keep the id and go back to it.

## D47: Default sandbox image `python:3.12`, and a reference NebiusSandbox adapter
- **Decision:** The Nebius sandbox starts from `python:3.12` (Debian, git included), not `python:3.12-slim`. `src/itr/sandbox/nebius.py` implements the Sandbox contract from what the probe showed (D45, D46): every exec is a non-disposable run whose returned image becomes the current one; a timeout becomes `ExecResult(timed_out=True, exit_code=-1)` and that command is treated as having changed nothing; `write_file` sends bytes; `snapshot()` is the image id; `restore(id)` is new, and cheap.
- **Why `python:3.12`:** the second probe run showed the full image already has git and starts in about a second. The slim image needs `apt-get install git`, which took 14 s on every run unless baked into a saved base image. A real SETUP plus REPRO on click-2877 took 31 s in total, including that install, so the image choice is a speed and simplicity win rather than a necessity.
- **Rejected:** slim image plus apt (14 s per run, one more thing that can fail); a custom base image (a saved image id has to be created and kept alive for the whole hackathon; not worth it until runs are the bottleneck).
- **Detail:** the adapter recognises the timeout by the exception's class NAME (`OperationTimedOutError`), so its tests need no SDK and a rename in the SDK would show up as an error rather than being swallowed. Infrastructure failures (network down, auth) raise `SandboxError`; the machine ends the run as ERROR.
- **Ownership:** `src/itr/sandbox/` belongs to the platform teammate (D18). This file is a reference implementation written because the probe made the platform behaviour concrete; the teammate can adopt it, change it, or replace it. `restore()` is an addition to the contract and needs their agreement.
- **Tests:** 9 adapter tests against an SDK stand-in modelled on the probe (state carries over, failures are results, timeout, bytes, truncation, snapshot/restore, infra errors, use-before-create), each checked by breaking the code on purpose. `scripts/smoke_nebius_sandbox.py` runs our real SETUP and REPRO inside the real sandbox for click-2877 and tabulate-399 (a hang, which exercises the timeout path) and checks restore; its logic was dry-run against a local stand-in, the real run is for you.
- **Not proven:** that the adapter works on the real service (the smoke script is how we find out), and whether images pile up in the project over many runs (nothing deletes them).
- **Concept:** **Adapter pattern.** The stages talk to a small contract; the adapter translates it to whatever the platform offers, so swapping local Docker for Nebius changes one file.

## D48: A timeout reaches us two ways; the live smoke test found the second (adapter fix)
- **Observed (live run, 2026-10-13):** click-2877 passed end to end in the real sandbox. tabulate-399, whose script hangs on purpose, came back from REPRO as `failed` with exit code -1 and empty output instead of `hang`. The platform HAD killed the command at the 30 s limit, but as an ordinary result with a negative exit code, not as the `OperationTimedOutError` the adapter was waiting for. The earlier probe (a `sleep` with an 8 s limit) had shown only the exception form.
- **Cause:** the client waits with the same limit that the platform gives the command, so two timers race. If the client's timer fires first we get the exception; if the platform's fires first we get a normal result with exit code -1.
- **Decision:** `exec` treats both as a timeout (`ExecResult(timed_out=True, exit_code=-1)`, partial output kept, a note appended to stderr, the previous image kept). The second form is recognised by a kill-type exit code (-1, -9, 124, 137) AND the command having run for at least the limit, measured on our own clock (which includes about 2 s of platform overhead, so a command killed at the limit always passes the check). A quick negative exit, for example an out-of-memory kill, stays a plain failure.
- **Rejected:** treating every negative exit code as a timeout (an out-of-memory kill would be reported as a hang and sent to VERDICT as evidence of an infinite loop); trusting the platform's reported `elapsed_time` (not yet shown to be reliable for killed runs).
- **Why it matters:** a hang is a real kind of bug (D37). Reporting it as "failed with no output" would have made VERDICT reject a correct reproduction.
- **Tests:** 2 new tests (killed at the limit is a timeout with partial output; quick negative exit is a failure), mutation-checked. The race itself cannot be forced from our side, so the live smoke test is the final check: rerun it and tabulate-399 should now report `hang`.
- **Concept:** **Race conditions between two timers.** When two parties enforce the same deadline, handle both outcomes; otherwise the code works on the run you tried and fails on the next one.

## D49: FIX proposes exact search/replace edits, and code validates them before anything runs
- **Decision:** FIX makes two small model calls per attempt. LOCATE (super) picks up to 4 source files and some search terms from the list of tracked non-test `.py` files. PATCH (super; ultra from the 3rd attempt) sees only those files and returns `root_cause`, up to 5 `edits` of the form `{path, search, replace}`, and a new test (`test_path`, `test_code`). Code then checks the plan (paths, sizes, a `def test_` exists), dry-applies the edits to prove every `search` text occurs exactly once, stores the diff, resets the repo, and always returns VERIFY.
- **Why search/replace and not a diff:** models write unified diffs with wrong line numbers and context lines, and a diff that does not apply tells us little. "This exact text, once, becomes that text" is checked mechanically and the failure message ("matches 3 places", "not found") is precise enough to feed straight back. Files with Windows line endings are matched in their own style.
- **Anti-cheating rules in code (`patching.py`):** a patch may edit only existing tracked `.py` files outside tests, docs, examples, benchmarks, `conftest.py`, `setup.py`, `noxfile.py`; the new test must be a new file named `test_*.py`. Otherwise the easiest "fix" would be to weaken an existing test or the test configuration.
- **Big files:** shown as windows around traceback lines and the lines that mention a search term, with omitted ranges marked, capped at 12k chars per file and 32k in total, so a large module does not flood the prompt or the bill.
- **Rejected:** letting the model run shell commands to explore (open-ended, costly, hard to bound); a single call with the whole repo (too big); `git apply` of a model diff.
- **Concept:** **Make the model's output checkable.** Choose an output format whose failures are detectable and explainable by code.

## D50: VERIFY is a gate of four mechanical checks
- **Decision:** a fix is accepted only if: (1) the new test FAILS on the original code (pytest exit 1; a timeout counts only when the reproduction itself was a hang; exit 2 or 5, a collection or syntax error, is "the test could not run", not "bug detected"); (2) it PASSES on the patched code; (3) the original reproduction script exits 0 on the patched code; (4) when the project's suite collects (`tests_collect_ok`), no test that passed before now fails (baseline run vs patched run, comparing `FAILED` lines, ignoring the new test; a suite timeout is non-blocking and recorded as `suite.ran = False`). Any failure sends the exact reason back to FIX while attempts remain; with none left the outcome is NEEDS_MAINTAINER and REPORT drafts a "reproduced, no verified patch" comment. The repo is reset (and the added test removed) in a `finally`.
- **Why check 3 as well as 1 and 2:** a model can write a test that fails and passes for a reason unrelated to the issue. The reproduction was already judged to match the issue (VERDICT), so making it pass ties the patch back to the reported problem.
- **Why exit codes of pytest matter:** a test with a syntax error also "fails" on the original code, which would wrongly look like a detected bug. Only exit 1 (a test ran and failed) counts.
- **Rejected:** asking a model whether the patch is good (the thing we are building must not depend on a model agreeing with itself); running the suite unconditionally (when the suite does not collect, a baseline is meaningless).
- **Tests:** 13 tests against a real temporary git repo and real pytest (`tests/localbox.py`), including a test that does not detect the bug, a patch that does nothing, a test that cannot run, a patch that breaks an existing test, a patch that edits protected files, a hang, escalation to ultra, and running out of attempts. Four deliberate breakages of the gate were each caught.
- **Concept:** **Fail-before / pass-after.** A test that has never been seen failing proves nothing.

## D51: REPORT never decides the outcome
- **Decision:** REPORT (nano) only writes the comment. It keeps an outcome already set (NEEDS_MAINTAINER), otherwise it is CONFIRMED because the run passed the gate. If the model fails or returns junk, a plain-code comment is used (`generated_by_model: false`) so a verified result is never lost to a formatting problem. The comment is emitted as an `artifact` named `report` (`{title, comment}`) because `Run` has no field for it; the store/UI (teammate) should read that artifact. Open: whether to add `report` to the `Run` model.
- **Concept:** **Separate deciding from describing.**

## D52: `scripts/live_run.py`, the first end-to-end run with real models
- **Decision:** one script runs the whole machine (FETCH to REPORT) on an eval case against real GitHub, real Nemotron models and the real Nebius sandbox, prints progress per stage, the cost, the patch, the regression test and the drafted comment, and saves everything to `live_runs/<case>.json` (gitignored) so a result can be studied without paying again. The exit code says whether the outcome matched the case's `expected_outcome`.
- **Why:** FIX and VERIFY were proven only with scripted model replies. Real models write different patches, and the surprises (search text that does not match, tests that do not fail) are what the retry design must absorb. Cases have a known real fix (`fix_sha`) so the agent's patch can be compared with the maintainers'.
- **Cost control:** default cap $0.15 per run (`--max-cost` to raise it). A budget stop ends the run as ERROR (see the open note in the audit: a confirmed repro should probably become NEEDS_MAINTAINER).

## D53: Edits are numbered line ranges, not quoted text (first live FIX run)
- **Observed (live run, tabulate-180, 2026-10-14):** TRIAGE, SETUP, REPRO and VERDICT all worked with real Nemotron models in the real sandbox. FIX failed all four attempts (super twice, ultra twice) with the same error: the `search` text "was not found". The comment the models quoted (`# determine number of columns`) does not exist anywhere in the file; the model invented the line it was supposed to copy. Retrying with the error message did not help, because the message could not show what the file really said. The run ended NEEDS_MAINTAINER at $0.072. A first super call also hit `max_tokens=12000` (its thinking used the whole allowance) and the output was lost.
- **Decision:** edits are `{path, start_line, end_line, first_line, replace}` over a numbered view of the file that we show (`  2065| code`). The model points at lines instead of retyping them. `first_line` must equal the text of `start_line` (indentation ignored): a cheap check that the numbers are right, and when it fails the error shows the real line. All edits in a file use the original numbering and are applied bottom-up, so they never shift each other; overlapping edits, ranges past the end, and edits that change nothing are refused; CRLF files keep their line endings; nothing is written unless every edit is valid.
- **Both sides use the same split:** the numbered view and `apply_edits` split on `"\n"` only (`str.splitlines` also splits on form feeds and other separators, which would shift every number after one).
- **Rejected:** keeping search/replace and adding fuzzy matching (it repairs wrong indentation but not an invented line); asking the model for a unified diff (wrong line numbers and context are the most common failure); letting the model rewrite whole files (large cost, and it silently drops code).
- **Also fixed:** the REPORT comment came back as one line with literal backtick-n (a PowerShell habit) instead of line breaks, so `tidy()` repairs that case (and only when the text has almost no real line breaks). `.env`: raise `LLM_MAX_TOKENS_SUPER` to 16000 for FIX, which reasons at length on big prompts.
- **Not proven:** that line ranges work better with these models. The next live run is the test; if they mis-number often, the `first_line` error text is the retry's lifeline.
- **Concept:** **Don't ask a model to reproduce text you can point at.** Language models are good at deciding and poor at exact copying; give them coordinates and check the coordinates.

## D54: First CONFIRMED run, and three things it taught (tabulate-180)
- **Result (live, 2026-10-14):** CONFIRMED in 230 s for $0.064. Attempt 3 (ultra) produced a patch that adds `if not list_of_lists: return ''` before `len(list_of_lists[0])`, plus a regression test that failed before (exit 1) and passed after (exit 0); the original repro passed on the patched code and the project's own suite had no new failures. Line-range edits (D53) worked: no "not found" errors.
- **Problem 1: attempts 1 and 2 were the same broken patch.** The replacement lost its indentation (an `if` at the wrong depth), so the file was not valid Python and VERIFY could only say "pytest exit 2". The retry then repeated the identical patch. Two fixes, both in plain code: (a) after the dry-run apply, every edited file is parsed (`ast.parse` in the project's own interpreter); a failure becomes a plan error naming the line and showing the lines around it, before any model-written test runs; (b) a patch AND test identical to an earlier attempt is refused with the earlier failure quoted ("do something different"). Only the pair counts as a repeat: the same patch with a corrected test is a legitimate retry.
- **Problem 2: REPORT's nano call hit `max_tokens=4000` twice** (thinking used the allowance) and fell back to the plain-code comment, which worked as designed but cost two calls. REPORT now makes one attempt (`retries=0`), since the plain fallback is already good; and set `LLM_MAX_TOKENS_NANO=8000` in `.env`. The plain comment also repeated the title as its first line; it now opens with a sentence.
- **Cost/time:** the FIX attempts took 12-45 s each; ultra's 12k-token answer was the slowest and dearest call. Escalating to ultra on the 3rd attempt paid off here, as designed.
- **Not yet shown:** a second case. cachetools-405 is a state-logic bug that needs a multi-step repro; the expected difficulty is higher.
- **Concept:** **Catch cheap mistakes cheaply.** A parse check costs milliseconds and gives the model a precise error; discovering the same mistake through a full test run costs a minute and an opaque message.

## D55: The model never retypes code: REPORT is assembled by code (second live run, cachetools-405)
- **Result (live, 2026-10-14):** CONFIRMED on the first attempt, $0.011, 147 s. The patch changes `Cache.__setitem__` to reserve only the extra size when an existing key grows (`needed = size - old_size`), the new test failed before (exit 1) and passed after (exit 0), the repro passed on the patched code, and the project's suite had no new failures. A second hard case now passes (state-logic bug, multi-step repro).
- **Problem 1: the drafted comment showed a wrong patch.** The nano model was asked to write the whole comment and retyped the diff and the repro, and got the diff wrong (a duplicated `if size > maxsize` check and a changed error message that are not in the verified patch). A maintainer would have read code that was never tested. It also dropped line breaks and mangled a quote.
- **Decision:** REPORT's model writes only a `title` and a short `summary` (plain prose, no code, shown none). Code assembles the markdown: the repro script, its output, the cause (`root_cause` from FIX, verbatim), the diff and the test are pasted exactly as stored, and the "Checks" list is built from what VERIFY recorded (including which tests failed before the patch). If the model fails, a fixed sentence replaces the prose and the rest is unchanged. The model is also never sent the diff or the test, so it cannot be tempted to retype them (tested).
- **Problem 2: sloppy regression test.** The test file contained four tests, three of them near-duplicates and one with the model's scratch notes in comments (`# currsize == 11? Wait exceeds maxsize...`). The gate only requires the FILE to fail before and pass after, so tests that pass before the patch ride along. Prompt now asks for one test function (two at most), all exercising the bug, no scratch notes. VERIFY records `failing_before` (which tests failed on the original code) and shows it in the `verified_fix` artifact and in the comment's checks, so a reader can see which test proves the bug.
- **Not done (considered):** automatically deleting tests that passed before the patch (changes model-written code; the gate would need to run again). If sloppy tests keep appearing, this is the next step.
- **Concept:** **Copy with code, write with models.** Whatever can be pasted must be pasted; a model's job is the part that needs language, and anything it retypes must be assumed to drift.

## D56: Windows test skips; what the first public deployment needs (audit, 2026-10-14)
- **Windows:** `test_config_peek_command_is_valid_shell` (SETUP) runs `bash -n` on a command that executes in the Linux sandbox, so it is skipped when no POSIX bash is on PATH (WSL's `System32\bash.exe` does not count). The hang test in `test_fix_verify.py` is skipped on Windows because killing bash on a timeout does not kill its Python child there, which blocks the test run. Both pass on Linux. Neither skip hides product behaviour: the product runs in the Linux sandbox.
- **Deployment notes (the demo is live on Vercel):** (1) real runs on Vercel are cut at 270 s; the two live cases took 147 s and 230 s, so a harder case or one with several REPRO retries can end as ERROR for time; (2) the per-instance concurrency limit does not bound a serverless deployment, so the only hard stop on spend is the per-run $0.15 cap and `ALLOW_NEW_RUNS=false` (replay-only); keep that switch ready and watch the credit balance; (3) `probe.json` and `verdicts.json` are tracked on `main` by accident and should be untracked (no secrets found in history).

## D57 - Eval harness: one subprocess per case, table built from saved JSON
**What:** `scripts/run_eval.py` runs `live_run.py` for each case in `eval/cases.yaml` (skipping cases already saved unless `--fresh`), then writes `eval/RESULTS.md`: expected vs got, cost, time, models. `--report-only` rebuilds the table without spending anything.
**Why:** The headline number for Potential Impact is "N of 8 outcomes correct, at $X total". Subprocess isolation means one crash or hang does not lose the batch; saved JSON means the table is reproducible and re-runs are opt-in because they cost money.
**Rejected:** looping in-process (a crash loses everything); recomputing outcomes from events (the saved outcome is the machine's own answer).
**Concept:** an eval is only evidence if it includes cases where the right answer is "no". click-3186 and click-1629 are those; a tool that says CONFIRMED on everything would score 6/8 at best.

## D58 - First full eval (4/8): what each miss taught us
**Result:** tabulate-180, cachetools-405, click-3186, click-1629 matched. tabulate-241, tabulate-399, click-1925, click-2877 did not. Total $0.44.
**Findings, and what changed:**
1. **My bug: wrong line in syntax errors.** The check runs via `python -c`, so the first `line N` in the traceback was the wrapper's `line 1`, not the model's file. Models were shown the top of the file as "the lines around it" and could not fix their indentation (click-2877 lost 3 of 4 attempts, tabulate-241 one). Now `syntax_error_line` reads the `File "<path>", line N` frame and the message says "at line N".
2. **click-1925: the model's attempt-1 patch was identical to the maintainers' fix** (`is_flag and isinstance(...)`), but its regression test asserted a behaviour the issue does not ask for, so the gate rejected a correct patch and later attempts drifted to wrong ones. The gate was right to reject the pair; the model trusted its test over its patch. Prompt now says: if your change would equal an earlier attempt, suspect the test. Not fully solved (the REPRO stage also picked the wrong scenario twice).
3. **tabulate-241: the repro script, not the patch, was wrong.** It counted `|` characters with a naive parser, which no correct escape fix can satisfy, so gate 3 (repro exits 0 on the patch) blocked a plausible patch whose own test passed. REPRO prompt now says to assert an exact expected string or the named property, never a home-made parser over the output. VERIFY keeps gate 3 strict.
4. **tabulate-399: the hang needs the optional `wcwidth` package and the issue never says so.** Four REPRO attempts all "passed" (the code took the non-wide-char path). REPRO now receives the project's declared optional dependencies (`optional_dependencies`) and is told to install the one that matches the bug.
5. **click-1925 ended ERROR (budget) after a verified repro.** Now a one-time small grace (+$0.03, +30k tokens, +90s) pays for REPORT, and the outcome is NEEDS_MAINTAINER. Budget exhaustion before a reproduction is still ERROR.
6. **Closest candidate shown, clearly unverified.** When no patch passes every check but one passed its own test (which fails on the original), the report includes it under "Closest candidate (NOT verified)" with the check that failed. A maintainer gets a starting point; the outcome is not upgraded.
**Rejected:** letting FIX rewrite the repro script (a model could weaken the oracle; would need its own before/after check); relaxing gate 3 when the test passes (it would let a patch through that does not fix the reported scenario); a higher per-run cap (hides the problem, spends credits).
**Concept:** when a verifier rejects a correct answer, the question is which input is wrong, the answer or the oracle. Here the oracle (a model-written repro or test) was wrong in 3 of 4 misses. Gates that stay strict make that visible instead of hiding it, and they make the failure analysable: every miss was explainable from the saved run.

## D59 - Second eval pass (still 4/8): the failures moved, two were tooling and two were oracles
**What the second run showed:** tabulate-399 now reproduces the hang (the optional-dependency change from D58 worked: REPRO installed `wcwidth`). The syntax-error line fix worked (messages now say "at line 1109"). But:
1. **click-2877: the model put the wrong indentation on the first replaced line in all four attempts** (4 spaces where the file has 8), even with the exact line shown. Telling a model the same thing four times did not help; code can fix it. FIX now tries two re-indentations of the replacement (shift the whole block, or fix only the first line) when the raw edit does not parse, and uses one only if the result parses. The message also states both indents. The patch still has to pass the whole VERIFY gate, so this repairs a typo and cannot hide a wrong fix. (Bug found while testing it: the variants were first computed AFTER the broken edit was written, so they compared against the model's own broken line; the repo is reset first now.)
2. **click-1925: the repro asserted the wrong thing and VERDICT passed it.** The issue is about the attribute `Option.is_bool_flag`; "--switch requires an argument" is correct by design. The reviewer already had a rule for "calling a command that rightly requires an argument", and still approved it. New rules for the reviewer: if the issue's own code asserts an attribute or value, a script that checks only a downstream error is suspect; REPRO is told to build from the issue's code and flip buggy asserts into correct ones. Note the report now shows the closest candidate, which for this case is exactly the maintainers' one-line fix.
3. **tabulate-241: the repro and the model's own test both counted `|` characters**, which no escape-based fix can satisfy. The REPRO prompt rule from D58 was ignored, so the check moved to the reviewer: if the script parses rendered output itself and a natural fix would change that text, it cannot pass (would_pass_if_fixed = false). FIX is also told to compare to the exact expected value, and never to put the test file in `edits` (seen once).
4. **Cost: calls that hit max_tokens (16000) are wasted money.** A reasoning model can spend the whole allowance thinking and return nothing; the client then retries. One such ultra call costs $0.048 and pushed tabulate-399 over its cap. Default per-run cap raised 0.15 -> 0.25 so eval and demo behave the same. The proper lever is in `llm.py` (teammate): detect `finish_reason == length`, and test whether Token Factory accepts a reasoning-effort / thinking switch for Nemotron.
**Rejected:** retrying more often (money); a bigger max_tokens (the model just thinks longer); weakening gate 3.
**Concept:** repair what is mechanical, check what is checkable, and send judgement to the reviewer who has the evidence. Indentation is mechanical (code repairs it); "does this script test what the issue is about" is judgement (the reviewer's prompt); "does the patch really fix it" is checkable (the gate). Each failure belongs to exactly one of those.

## D60 - Third eval pass (6/8): patch-size guard; triage variance noted
**Result:** tabulate-180, cachetools-405, click-1925, click-2877, and both traps match: 6/8, total $0.26 for the last pass. click-1925 (the reviewer now rejects the "requires an argument" repro, REPRO built from the issue's own asserts) and click-2877 (`auto_reindented: True`) are CONFIRMED with the maintainers' own change in substance.
**Still open:**
1. **tabulate-241 stopped at TRIAGE** (nano said not-a-bug, 8 s, $0.000), though it passed triage in earlier runs and the triage prompt is unchanged. Nano is non-deterministic on a borderline issue (the maintainer's reply says the `pipe` format never specified escaping, so "bug or feature" is arguable). To be diagnosed from the saved `triage` field before changing anything. Rejected for now: a second-opinion call that lets any "bug" vote through, because it could let a feature request (the click-1629 trap) reach FIX and be "confirmed".
2. **tabulate-399 reproduces (the optional-dependency fix worked) but FIX never finds the change.** The maintainers' fix is ONE effective line (`i = max(i, 2)`, +4 with comments). All four model attempts rewrote 40-75 lines of `_handle_long_word`, broke existing tests, and burned 710 s and two max_tokens calls. "Make the smallest change" in a prompt did not hold. Now enforced by code: a patch over 40 changed lines (added + removed) is refused with the count and a hint, before it is ever run.
**Concept:** a prompt can ask for a small change; code can require one. When a property is cheap to measure (changed lines), measure it and refuse, instead of asking again.
**Rejected:** a limit of 10 lines (some real fixes touch several places); counting files instead of lines (a one-file rewrite passes).

## D61 - Triage: write the evidence first, decide the label last
**What happened:** on tabulate-241 triage answered `kind: "feature"` while the SAME reply's `actual` said "returns an invalid markdown table with mismatched column counts", which the prompt itself defines as a bug. The label came first in the JSON, so the model committed to it before writing the evidence; nano is also non-deterministic, which is why earlier runs passed. The run ended in 8 s at $0.000 with NEEDS_MAINTAINER.
**Change:** the schema and prompt now order the fields steps, expected, actual, ..., kind, and a rule says to decide `kind` last from `actual`: broken/invalid/crashing output for an accepted input is a bug even if worded as a request; "feature" only when `actual` says the capability never existed. `scripts/triage_stability.py` runs FETCH + TRIAGE N times per case (under a cent) to measure the label's stability, including on the click-1629 trap.
**Rejected:** a second model that lets any "bug" vote through (would let a feature request reach FIX); lowering temperature (not in our control behind the client, and does not fix the contradiction); a code rule that overrides the label from keywords in `actual` (brittle).
**Concept:** with structured output, field order is part of the prompt. A model that must write the answer before the reasons will defend the answer; make it write the reasons first.

## D62 - Transient network failures no longer end a run as ERROR
**What happened:** the last eval pass lost two cases to the network, not to the agent: GitHub dropped the connection during FETCH (`RemoteDisconnected`) and the Nebius sandbox API timed out polling an operation (`ApiTimeoutError`) during REPRO. Triage also took 44 s, so the connection was slow. Both ended as ERROR. A live demo that ends in ERROR because of a dropped packet is a bad demo.
**Change:** `src/itr/retry.py` retries a call up to twice more (waits 2 s, then 6 s) but ONLY when the error looks transient (timeouts, dropped or reset connections, 429/5xx). Applied at the three network edges: GitHub (`_http_get`), the whole sandbox (`RetrySandbox`, installed by `run_machine`, so every stage is covered and `destroy` is never retried), and model calls (`Context.model`). Retries emit `sandbox_retry` / `model_retry` events so they are visible, not silent. A 404, a missing file or a bad request fails at once.
**Risk accepted:** a retried sandbox `exec` re-runs the command. Our commands are re-runnable (repro scripts, pytest, pip, git checkout); a repeated `git clone` would fail the same way it would have without a retry.
**Rejected:** retrying every exception (hides real bugs and repeats work); retrying inside each stage (many places, easy to miss one); longer waits (a demo must not hang for minutes).
**Concept:** distinguish "the world hiccuped" from "the answer is no". Retry the first, never the second, and record that a retry happened.

## D63 - Fourth eval pass: relocate misnumbered edits; stop asking for exact strings; measure reasoning cost
**Result:** still 6/8 (tabulate-241 and tabulate-399 ended NEEDS_MAINTAINER after four FIX attempts, $0.165 and $0.176, 452 s and 522 s). Both now get through triage, set-up and reproduction (the hang on tabulate-399 reproduces every time since the optional-dependency change), so the remaining problem is FIX.
**Findings:**
1. **Most lost attempts were misnumbered edits, not bad ideas.** Refusals of the form "line N is X, not Y" took 3 of the 4 FIX attempts on tabulate-399 and the first on tabulate-241. The model quotes the right line but is off by a few. `first_line` is an anchor, so `relocate_edits` now moves an edit (start and end by the same amount) to the quoted line when it appears EXACTLY ONCE within 25 lines; a quote that is absent, ambiguous or shorter than 5 characters is refused as before. The move is recorded (`auto_relocated`) and VERIFY still judges the result.
2. **My own rule from D58 backfired on tabulate-241.** "Compare with the exact expected string" made the model invent the library's padding (`| col1 | col2\|withpipe |`), which tabulate never emits, so the script could never pass. REPRO now says: check the specific property with substring tests, use a whole expected string only when the issue quotes it, never invent spacing. VERDICT gets the matching rule: compare the author's expected text with the real output printed; if they differ in anything the issue does not mention, `would_pass_if_fixed` is false.
3. **Most FIX calls think until the 16000-token cap, return nothing, and are paid for twice** (e.g. 16000 out, 84 s, then a second call with 3.5k out). On these runs that is roughly a third of the time and a large share of the cost. `scripts/probe_reasoning.py` compares candidate settings (thinking off, low effort, `/no_think`) on a small FIX-style task and prints tokens, time and correctness per variant. If one works, the platform teammate adds it to `llm.py`.
**Rejected:** raising max_tokens (the model thinks longer, not better); retrying without feedback.
**Concept:** a model's numbers are unreliable but its quotes are good; use the quote as the key and the number as a hint. Cheap, checkable anchors let code absorb the model's small mistakes without trusting its big ones.

## D64 - Show the whole function; refuse a patch that already failed on patched code
**Result of the fifth eval pass:** still 6/8. Triage is now stable (`triage_stability.py`: tabulate-241 6/6 bug, click-3186 6/6 bug, click-1629 6/6 feature), so D61 worked. tabulate-399 ended NEEDS_MAINTAINER after four attempts: the first test file would not even collect (a pytest error on the original code), and attempts 1, 3 and 4 proposed the SAME patch (`width = 1` inside the `if width < 1:` branch), which the gate showed does not cure the hang. The real fix changes how `i` is computed further down the loop.
**Cause, in the model's own words:** its root cause for attempt 4 says "the body (lines 2727-2759 omitted)". The numbered view of a large file shows a fixed 25-line window after each search-term hit, so a 45-line function was cut in half and the model "fixed" code it had not read. Nothing in the gate could see that.
**Changes:** (1) when a search term hits a `def`/`class` line, `render_file` now shows the whole block (up to 80 lines, `block_end`); (2) the repeat guard also refuses a patch identical to an earlier one whose failure says "on the patched code" (the checks ran on it and it failed), even with a different test. A patch whose earlier failure was a bad TEST (collection error) may still be retried with a new test.
**Rejected:** a bigger fixed window (a long file would still cut some function); showing whole files (blows the token budget); telling the model to "read carefully" (it cannot read what it was not shown).
**Concept:** before blaming the model, check what it was actually given. Truncated context produces confident answers about code the model never saw, and the only symptom is that the answers keep being wrong in the same way.

## D65 - A suite that stops finishing is a failure; the report may not claim a merge
**What happened:** tabulate-399 came back CONFIRMED (7/8), but when the patch was applied to the real repository the project's own suite hung on `test_wrap_multiword_non_wide` (the original suite passes 317 tests in 2.4 s). The patch also leaves a stray `i = i + 1` under `if i == 0:` and changes how many characters a normal long word takes. The gate missed it because of one branch in `verify.py`: the suite finished on the original, did not finish on the patched code, and that was recorded as `suite.ran = False` ("could not run") and verified anyway. The drafted comment also said "The fix has been merged", which is not true of anything the agent did.
**Changes:** (1) if the baseline suite finished and the patched suite hits the timeout, VERIFY now fails with the last lines of output (the patch hangs or slows an existing test); a suite that cannot be compared (the ORIGINAL timed out, or tests did not collect) is still reported as "could not be re-run". (2) the REPORT prompt says the patch is only a proposal, and `drop_untrue_claims` removes any summary sentence that says merged, released, deployed, committed, pushed or applied. (3) the results note under the eval table says what CONFIRMED means when the suite cannot run.
**Rejected:** treating every unrunnable suite as a failure (some projects cannot run their suite in the sandbox at all); only prompting the report model not to claim a merge (a prompt asks, a regex checks).
**Concept:** a verifier that answers "unknown" must not be allowed to say "yes". Separate "could not check" from "checked and failed", and look for the branch where the first quietly turns into a pass. The 7/8 headline was only caught because I ran the patch somewhere the agent had not.

## D66 - The eval table must come from one version of the code, and report a rate
**What happened:** after the stricter gate (D65) a re-run of tabulate-180 and tabulate-399 gave 5/8. The 7/8 and 6/8 before it were not comparable: `run_eval.py` builds each row from the LAST saved run of that case, so cases that were not re-run (180, 405, 1925, 2877) kept results from older code, and tabulate-180, which had been CONFIRMED, turned out to fail on the current code (four attempts; its patches fixed `num_cols` but not the second `list_of_lists[0]` in `_wrap_text_to_colwidths`). Whether that is variance or a regression from a recent change is unknown from one run. The same run also showed the gate working: tabulate-399 attempts 2 and 3 were refused for breaking `test_break_long_words`, `test_wrap_multiword_non_wide` and `..._with_hypens`, which the old gate would have let through.
**Change:** `run_eval.py` appends every finished run to `eval/history.jsonl` (time, case, outcome, cost, seconds, git commit and whether there were local changes), and the table now shows a "match rate over all recorded runs" and the time of the last run next to the latest outcome. The number to quote is a full `--fresh` run of all 8 on the final code, ideally twice.
**Rejected:** best-of-N (reports luck); quietly dropping the weak cases (the cases were chosen before the runs); hand-editing RESULTS.md.
**Concept:** a benchmark number needs a version attached, and a stochastic system needs a rate, not a sample. A table assembled from old runs measures the past.

## D67 - An empty reply escalates to the next tier; the eval refuses to record outages
**What happened:** in the first full `--fresh` pass three of eight cases ended ERROR with `fix: no valid JSON after 2 tries`: tabulate-241, tabulate-399 and click-2877 (which had been CONFIRMED before). In each, the FIX call returned nothing twice in a row because the model used all 16,000 output tokens thinking (about 90 s and 1.4 cents each time). `model_json` retried the SAME tier with the SAME prompt, so the second try did the same thing, and the run died without a single patch being judged. The other five cases behaved as expected (tabulate-180 CONFIRMED again, cachetools-405 and click-1925 CONFIRMED, both traps correct). The second pass was lost to the machine's internet dropping (`getaddrinfo failed` on seven cases), and those fake failures were written into the history.
**Changes:** (1) after an unusable reply `model_json` asks the next tier up (nano to super to ultra), tells the model plainly when the reply was empty ("you ran out of room while thinking"), and emits `model_escalated`; FIX gets one more retry (`retries=2`) because that is where the runaway happens. The budget check still bounds the cost. (2) `run_eval.py` checks DNS before each case (waits up to 150 s, then stops without recording), marks a run `infra` when it ended in ERROR from a timeout or connection error, and leaves it out of the match rate (it is shown as "+N network error").
**Rejected:** just raising `max_tokens` (the model thinks longer, not better); retrying the same tier a third time; counting outages as failures (it measures the Wi-Fi, not the agent); deleting the bad history by hand without changing the harness (it would happen again).
**Concept:** a retry that repeats the same request against the same deterministic-enough failure is not a retry, it is a second bill. Change something on the retry (a stronger model, a different instruction) or do not retry. And an eval harness must tell "the agent failed" apart from "the test rig failed", or its numbers mean nothing.

## D68 - First clean full pass (6/8) and an audit of what CONFIRMED really guarantees
**Result:** one full `--fresh` run on one version of the code, no ERRORs: tabulate-180, cachetools-405, click-1925 and click-2877 CONFIRMED; both traps correct; tabulate-241 and tabulate-399 NEEDS_MAINTAINER ($0.39, 33 minutes). The escalation to the next tier (D67) removed the empty-reply ERRORs; tabulate-399 still has long runs (771 s) because every FIX attempt that fails costs a full model call plus a verify that waits out a 30 s hang.
**Audit:** patches were applied to the real repositories and tried on cases the tests do not cover. cachetools-405's patch computes `needed` once before the loop, so growing the OLDEST key in an LRU cache evicts that key itself and the cache ends at 12 of maxsize 10 (the maintainers' fix ends at 9). tabulate-180's patch returns `''` for `tabulate([], headers=[...], maxcolwidths=5)` where the maintainers' fix (and the same call without `maxcolwidths`) returns the header row. click-1925's patch is identical to the maintainers'. The gate passed all of them because the project's tests do not cover these inputs.
**Decision:** state the limit plainly in the README and in each report instead of claiming correctness: CONFIRMED means every automatic check passed, a maintainer still reviews. Idea for later (not built): an advisory "reviewer probes" step where a strong model writes up to three scripts for behaviour that must NOT change, run on the original AND patched code; a probe that passes before and fails after is a regression (judgement from the model, verdict from exit codes). It would have caught the cachetools case only if the model thought of that input.
**Rejected:** hiding the audit; tuning prompts toward the maintainers' fixes (that would score higher and prove nothing); adding the probe step three days before the feature freeze without a way to measure false alarms.
**Concept:** a verifier proves exactly what it checks and no more. Say what the check covers, test the shipped answers by a route the check does not use, and write down the gap.
