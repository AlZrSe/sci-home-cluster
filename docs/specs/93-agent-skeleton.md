# Spec — Issue #93: the worker agent skeleton (config, logging, lifecycle, graceful shutdown)

**Issue:** [#93](https://github.com/AlZrSe/sci-home-cluster/issues/93) — *agent/run_agent.py parses two flags and returns: no process, no config, no log, no shutdown path*
**Status:** groomed, ready for SWE
**Author:** PM
**Baseline commit:** `0acb57b` (`fix: keep the test session on one event loop so stranded aiosqlite work cannot outlive it (#57)`)
**Labels:** `in-progress` (correct — the work is not landed), `phase-2`, `agent`

**This file is the authoritative input for the SWE cycle.** It is self-sufficient. Do not go read the
issue thread; §0 explains why you should not need to.

---

## 0. Why this file exists, and what it replaces

An implementation of this issue was written, QA-verified, and then **lost**. Commit `42f5089` on a
branch named `agent` is not in any local branch, any remote, this repository's reflog, or GitHub
(`gh api .../commits/42f5089` → HTTP 422). No PR was ever opened. `main` still carries the 26-line
stub.

**The root cause of that loss is that the spec lived only in issue comments.** Five comments, roughly
50,000 characters, held the design, the human's decisions, and the QA verdict. When the workspace
went, so did everything that was not in the repository.

This file consolidates all five sources into one document so that cannot happen twice:

| # | Source | Role | Where it landed |
|---|---|---|---|
| S1 | Issue body | Problem, evidence, original approach and ACs | §1, §4, §7 |
| S2 | Comment 1 (`Part of the agent backlog`) | Binding #92 context: the agent is **file-only**; the loop is a watcher, not a poll; watcher needs a failure mode | §1, §5.6, §7 |
| S3 | Comment 2 (`Revised under the file-only decision`) | Drops credentials, adds the folder watcher, adds liveness, amends the ACs | §3.2, §5.6, §7 |
| S4 | PM spec comment | The design: settings, precedence, logging, supervisor, watcher, lifecycle, exit codes; AC-1…AC-18; ~70 named tests; risks R1–R11 | §5–§9, §14 |
| S5 | Orchestrator environment comment | The working interpreter and the first measured baseline | §10 (superseded — see §10.3) |
| S6 | **Decision record** | **The human's Q1–Q5 + coverage answers. These are decisions, not suggestions.** | §3 |
| S7 | QA verdict (commit `42f5089`) | Defects **D1–D6**, four reported deviations with verdicts, mutation-testing findings, ~70 named tests | §8, §9 |
| S8 | Orchestrator loss comment | The loss itself; corrected environment baseline | §0, §10 |

### 0.1 Order of authority

Where sources disagree, this document resolves in this order:

1. **The decision record (S6).** It is the human's answer to an explicit question. It overrides the
   spec's assumption, and it overrides the issue body.
2. **A QA finding that corrects a claim about the code or about a library (S7).** QA measured these;
   the spec inferred them. Measured beats inferred.
3. **The spec (S4).** Everywhere S6 and S7 are silent.
4. **The issue body (S1).** Wherever it has not already been corrected in §4.

**Where the sources leave something genuinely open, this spec says `NEEDS-HUMAN-DECISION` and gives
the options. It does not choose.** See §15.

---

## 1. Problem and evidence

### 1.1 The problem

`agent/run_agent.py` is 26 lines that parse two arguments, print two strings, and return. There is no
process, no loop, no configuration handling, no logging, and no shutdown path.

```python
def main():
    parser = argparse.ArgumentParser(description="Scientific Home Cluster Worker Agent")
    parser.add_argument("--node-id", required=True, help="Unique identifier for this node")
    parser.add_argument("--syncthing-root", required=True, help="Path to Syncthing shared folder")
    args = parser.parse_args()

    print(f"Starting agent for node {args.node_id}")
    print(f"Syncthing root: {args.syncthing_root}")
    # TODO: Implement agent logic
```

Before any duty from the rest of the agent backlog can be implemented, the agent needs a skeleton
worth putting duties into: something that can be configured, logs, runs a supervised loop, starts and
stops cleanly, and is testable without a real cluster.

### 1.2 Evidence

- `agent/run_agent.py:22` — the only TODO in the file, immediately after the two prints. It is the
  entirety of the agent.
- `agent/` contains exactly two files: `__init__.py` (0 bytes) and `run_agent.py`. There is no
  `agent/config.py`, no `agent/loop.py`, nothing to unit test.
- `agent` is packaged and installed as a console script: `pyproject.toml:117` includes `agent*`,
  `:122` registers `run-agent = "agent.run_agent:main"`. So `pip install -e .` already puts a command
  on `PATH` that does nothing.
- **No logging anywhere in the agent.** The only `print` calls are the two above. This connects to the
  open issue #68 ("Logging is configured only in `backend/main.py`: every other entrypoint emits no
  INFO records at import") — whatever #68 decides for the backend needs to be applied here too, or
  the agent will be the second entrypoint with no logging.
- **Configuration is duplicated and inconsistent.** `run_agent.py:16-17` takes `--syncthing-root` as a
  required flag, `backend/core/config.py:248` reads `SYNCTHING_ROOT` from the environment with default
  `/tmp/syncthing`, and `shared/file_ops/path_utils.py:58` reads the same variable directly from
  `os.environ`. The agent has no settings object at all, so it cannot honour `LOG_LEVEL`
  (`config.py:326`) or any other environment the backend already established.
- **No shutdown handling.** `sys.exit(main())` at `:26` with no signal handling. A worker agent that
  is `SIGTERM`-ed mid-job leaves an orphaned child process — which is precisely the situation the
  restart-recovery issue (#103) has to clean up. It is cheaper to get right at the skeleton level.
- **No tests.** `backend/tests/` covers the backend only; there is no `agent/tests/`.

### 1.3 Binding context: the agent is file-only

From #92 (closed, decision recorded) and the two #93 comments. **This is not negotiable in this issue:**

- The agent **never opens a network connection to the backend** — no HTTP, no WebSocket, no JWT, no
  `SHARED_TOKEN`.
- **#94 is retired.** The agent's configuration surface is **`node_id`, the Syncthing root, and its own
  operational knobs**, enumerated in §5.2. **There is no secret in the agent's environment for a
  submitted command to inherit.** #98's environment policy shrinks to "parent environment +
  `spec.env`" and stops being a security boundary.

---

## 2. Conflicts between the sources, and how each was resolved

Every conflict found while consolidating, and its resolution. Authority per §0.1.

| # | Conflict | Sources | Resolution |
|---|---|---|---|
| K1 | **Python version.** S4 says `.venv` is 3.13.9. S5 says 3.13.5. Both say the pinned SQLAlchemy aborts at import and `pip install -e ".[dev]"` "cannot resolve at all". S8 says **3.12.7**, dev install resolves cleanly, 531 tests collected, and the "unresolvable" claim is Python-3.13-specific. | S4, S5, S8 | **3.12.7.** The S4/S5 claims are about a broken 3.13 venv, not about the project. See §10.2 for the two claims in S5 that are *also* stale for an independent reason. |
| K2 | **Collected test count.** S8's loss comment says 550. The later measurement says **531**. | S8 vs. PM instruction | **531.** 550 is superseded. |
| K3 | **`mypy .` error count.** S4 says 138 in 27 files. S5 and S7 say 139 in 28. | S4 vs S5/S7 | **139 in 28 files** (the later, measured number). But see §10.4: both were measured on an environment that no longer exists. The gate is now a **delta**, not an absolute. |
| K4 | **Whole-suite `pytest` baseline.** S7 (3.11.17 hand-built env): 13 failed / 492 passed / 29 skipped. S8 (`main` @ `e0d3856`, 3.12.7): 435 passed / 30 skipped / **85 errors**. | S7 vs S8 | **Neither is a gate.** They are different interpreters and different trees. The gate is §10.4: *the failure/error set must be identical to `main` measured in the same interpreter.* S8's 85 errors are additionally stale — `main` has moved to `0acb57b`, which is the #57 fix for exactly those aiosqlite/event-loop errors. |
| K5 | **`NODE_ID` sources.** S4's settings table: `NODE_ID` sources = *CLI → env → TOML*. S6: **`NODE_ID` must not come from the shared file at all**; its presence in the TOML is an *error*, not a silently-ignored extra. | S4 vs S6 | **S6 wins.** Direct contradiction. `NODE_ID` is **CLI flag > env var only**. A `NODE_ID` key in `agent.toml` is a hard error (exit 1). See §3.1 Edge 2. |
| K6 | **`NODE_ID` env var name.** S6-Q2 says "`AGENT_`-prefixed for **everything else**", which would suggest `AGENT_NODE_ID`. S6-Q1 says "`NODE_ID` is per-machine by nature and stays CLI flag > env var only", and S7 quotes the live error message: *"Pass `--node-id` or set the **`NODE_ID` environment variable** on this machine instead."* | S6-Q2 vs S6-Q1/S7 | **`NODE_ID`, unprefixed.** Two independent statements in the decision record name it. **Do not "fix" this to `AGENT_NODE_ID`** — a backend/frontend/agent convention question, not a bug. The asymmetry is deliberate: `SYNCTHING_ROOT` and `NODE_ID` are the two names with meaning outside the agent. |
| K7 | **Coverage `source` widening.** S4 made it *"conditional on a baseline being obtainable"* and recommended landing the required change only if none could be had. S6 made it **mandatory**: `source = ["backend", "agent"]`. | S4 vs S6 | **S6 wins — mandatory.** See §11. **But S6 was written before any numbers existed, and the numbers S7 later measured create a consequence S6 did not consider. That consequence is `NEEDS-HUMAN-DECISION` NHD-1 (§15).** |
| K8 | **`NodeIdFilter` on the root logger.** S4 §Logging step 4 says install the filter on the **root** logger "so records from `shared.file_ops.yaml_utils` … also carry the node id". S7 measured this to be **mechanically ineffective**: `logging.Logger.callHandlers` walks the handler chain and never calls a `Logger.filter`; filters only run on the emitting logger. | S4 vs S7 | **S7 wins.** A root-logger filter cannot annotate records emitted by `agent.loop` or `shared.*`. The mechanism must be a **record factory** (process-global, covers everything) **plus a handler-level `NodeIdFilter`** (the backup for when a library replaces the factory). See §5.4. |
| K9 | **`~` expansion via `normalize_path`.** S4 says `SYNCTHING_ROOT` is "Resolved via `normalize_path` so `~` works". S7 measured: `normalize_path('~/x')` raises `TypeError: 'PosixPath' object is not subscriptable` at `shared/file_ops/path_utils.py:27`. | S4 vs S7 | **S7 wins.** `~` must be expanded with `pathlib.Path.expanduser()` **before** calling `normalize_path`. Verified correct on 3.12.7 by inspection of `path_utils.py:24-27`. The underlying `shared/` bug stays open (D6). |
| K10 | **`TaskSupervisor.spawn` (late task activation).** S4's design registers all three tasks in `run()`. The lost implementation added `spawn`, which the spec does not describe. S7 judged it *"a real fix for a real hole, not scope creep"*: without it, a folder appearing **after** startup starts the watcher but never registers `watcher-liveness`, so a watcher that then dies goes undetected — precisely the outcome S3 calls "the worst outcome of this whole backlog". | S4 vs S7 | **S7 wins — include it.** §5.5 specifies `spawn` with S7's constraints (`add_task` still refuses after `start`; `spawn` refuses before `start`; no-op on a running name). |
| K11 | **Folder write-probe.** S4's `_check_folder` implies probing. The lost implementation dropped the write probe in favour of `os.access`. S7 measured 7 permission shapes, **0 disagreements**, and judged the self-heal does not depend on it. | S4 vs S7 | **S7 wins — use `os.access`, no write probe.** R11's argument (a probe on every 30 s tick would fan ~4 replicated events per tick per node into the directory the backend also watches, for no new information) is sound and is to be written into the method's docstring rather than left implicit. Two disagreement shapes (read-only mount, `chattr +i`) are **not constructible** and are recorded in §8/D5 notes and §14/R12. |
| K12 | **AC-5 scope — record vs. rendered line.** S4's AC-5 is worded about the *record* carrying `node_id`. S7's D2: the shipped process emits the attribute on every line but **does not print it**, so the README's promise and **US-2** were not delivered. | S4 vs S7 | **S7 wins, and AC-5 is strengthened.** AC-5 now requires both: the record carries `node_id`, *and* a captured log line rendered through the installed handler contains it. This is not a new AC — it is US-2 ("every line says which node it came from") becoming testable. See §7/AC-5. |
| K13 | **Stale line citations.** S4 cites `config.py:168/246/251-255/264`, `syncthing_service.py:27-105/58/132-137`, `README.md:202-210`, `pyproject.toml:83`. | S4 vs `main` @ `0acb57b` | **§4 carries the re-verified citations.** Note one substantive change: `config.py:333` is now `env_file=_REPO_ROOT / ".env"` — **#58 landed**, so the CWD-relative `.env` bug the spec warned about no longer exists. That removes one argument for excluding `.env`; it does not change the Q1 outcome, which was decided on other grounds. |
| K14 | **`#107` scope.** S5 filed #107 as "the documented dev install is unresolvable and the pinned pytest pair cannot collect a single test". S8 says that is 3.13-specific. Independently: `pyproject.toml:44` is now `types-python-jose==3.3.4.20240106` (#73, `2ee4470`) and `:35` is `pytest-asyncio==0.23.8` (#69, `f41282c`). | S5, S8, `main` | **Both of #107's named defects are already fixed on `main`.** §10.2. Do not block #93 on #107. |
| K15 | **`NODE_ID` default.** S4: no default, required. S6-Q2: `SYNCTHING_ROOT` has no default; says nothing about `NODE_ID`'s default. | S4 vs S6 | **No conflict — S4 stands.** `NODE_ID` is required with no default. A guessed node id impersonates a node, and per #92 the agent has no identity to check. |

---

## 3. Decisions (Q1–Q5 + coverage) — **human decisions**

> **These are decisions, not suggestions.** The spec left six items as `NEEDS-HUMAN-DECISION`; the
> human has answered all of them. Where an answer diverges from what the spec assumed, the divergence
> and its consequences are stated explicitly.

| # | Question | **Decision** |
|---|---|---|
| **Q1** | Config file at all, and where? | **TOML, at `<SYNCTHING_ROOT>/nodes/agent.toml`** |
| **Q2** | Env var naming | **Shared `SYNCTHING_ROOT`; `AGENT_`-prefixed for everything else** |
| **Q3** | Console script name | **Keep `run-agent`** |
| **Q4** | Lifecycle depth | **Ordered shutdown + grace period + explicit no-op seams** |
| **Q5** | Watcher process | **In-process `watchdog.Observer` + liveness probe** |
| — | Coverage `source` | **Widen to `["backend", "agent"]`** |

### 3.1 Q1 — TOML in the Syncthing `nodes/` folder — **DIVERGES FROM THE SPEC**

The human chose an option that **was not on the spec's list**. It is a good one: the config file
**replicates with the folder**, so one edit configures the whole fleet. That is a property no
`--config` flag, no `.env` and no XDG path can offer on a cluster whose defining feature is that the
nodes talk by replicating a folder.

**The two sharp edges, and how they are handled.**

**Edge 1 — is `nodes/agent.toml` visible to the backend watcher?** No, and this is verified rather than
assumed. `SyncthingEventHandler._is_relevant_file` (`syncthing_service.py:53-62`) returns `False` for
any suffix that is not `.yaml`:

```python
# Only process .yaml files (case-insensitive)
if path_obj.suffix.lower() != ".yaml":
    return False
```

`agent.toml` therefore never reaches `_process_file`'s routing (`:108-124`), which is the only place a
`nodes/<id>` path is turned into a node registration. The `.toml` suffix is what makes this safe, and
it is **load-bearing**: **the file must be named `*.toml`, never `*.yaml`**, or it would be routed to
`_process_node_file` with `node_id = "agent.toml".replace(".yaml", "")` → `"agent.toml"` → a phantom
node. Re-verified on `main`:

```
_is_relevant_file('agent.toml') = False
_is_relevant_file('agent.yaml') = True
_is_relevant_file('agent.YAML') = True
_process_file('<root>/nodes/agent.yaml') -> routes to _process_node_file  # a phantom node
```

The agent's own watcher carries the same `.yaml`-only filter, so it does not re-enter on the config
file either.

> **The `.toml` filename constraint is MANDATORY and must be pinned by a named test.**
> `test_config_file_toml_suffix_is_ignored_by_the_backend_watcher` — see §9, VM-2. This was
> *recommended* in the spec; the decision record made it load-bearing, and QA mutation-tested it by
> renaming the constant to `agent.yaml`, stripping the literal-name assertion, and confirming the
> test still went red against the backend's **live** filter.

**Edge 2 — `NODE_ID` must not come from the shared file.** Every node reads the *same* replicated
file. If `NODE_ID` were readable from it, every agent on the fleet would come up claiming to be the
same node. `NODE_ID` is per-machine by nature and stays **CLI flag > env var only**. This must be
enforced **in code, not just documented**: a `NODE_ID` key present in the TOML is an **error**, not a
silently-ignored extra.

**Other consequences to implement.**

- **Location is derived, never configured.** `build_settings` computes
  `toml_path = <SYNCTHING_ROOT>/nodes/agent.toml` from the **resolved** root. There is **no
  `--config` flag** and **no CWD-relative search**. This keeps #58's bug class out and means the same
  command behaves identically from any directory — which is also what `AGENTS.md:21` and the #104
  systemd unit need.
  - ⚠ **Resolve the root *before* deriving the path.** The lost implementation got this wrong
    (defect **D1**, §8). It is the single most likely way to reproduce D1.
- **Absent file is normal.** An absent `agent.toml` is not an error: every value falls through to env
  var, then CLI flag, then field default. Only a file that exists and is **malformed** is an error,
  and it must name the file's absolute path.
- **Unknown keys are rejected.** A typo in a shared file that replicated to six nodes must fail loudly
  on all six, not be ignored six times.
- **Single file, flat keys.** No `[agent.<node_id>]` per-node sections in this issue. A node
  overriding one setting means editing a file every other node also reads. Per-node override is
  **explicitly deferred** (see §13) — recorded as a follow-up, not half-built.
- **No hot reload.** The file is read once, at startup. Editing it requires restarting the agent.
  This must be stated plainly in the `README.md` update — an operator who edits the file and sees
  nothing happen will otherwise conclude the agent is ignoring it.

> **R1 is now LIVE.** The spec called the pydantic-settings precedence trap "the single most likely
> silent defect in this issue" and made `test_env_overrides_toml_file` conditional on choosing TOML.
> TOML is chosen.
>
> **`test_env_overrides_toml_file` is now MANDATORY, not optional** — and per §9/VM-1 it must be
> extended beyond the spec's version to cover `~` and relative roots, because the spec's version would
> not have caught D1.

### 3.2 Q2 — Shared `SYNCTHING_ROOT`, `AGENT_` for the rest

Accepted as recommended. Consequences the SWE must implement:

- `SYNCTHING_ROOT` is the single unprefixed name, because it is the only one with code outside the
  settings model reading it (`path_utils.py:58`), and because **#98 needs
  `is_within_syncthing_root()`**, which reads `os.environ["SYNCTHING_ROOT"]` directly.
- Every other setting is `AGENT_`-prefixed: `AGENT_LOG_LEVEL`, `AGENT_SHUTDOWN_GRACE_S`,
  `AGENT_FOLDER_WATCH_INTERVAL_S`, `AGENT_FOLDER_RETRY_MAX_S`, `AGENT_WATCHER_LIVENESS_INTERVAL_S`,
  `AGENT_STATE_DIR`. `LOG_LEVEL` is **no longer** shared, which retires the coupling the spec's option
  A flagged — a backend `LOG_LEVEL=DEBUG` can no longer silently debug the agent.
  - `NODE_ID` stays **unprefixed**. See conflict **K6**.
- **The `os.environ` side effect is now in scope.** `build_settings` must set
  `os.environ["SYNCTHING_ROOT"] = str(resolved_root)` **after** resolving, so `shared/file_ops`
  helpers work for #98. It must be a **documented, deliberate step**, not an accident. Precedence is
  unaffected: `os.environ` is mutated *from* the resolved value, **never read back in**.
- `SYNCTHING_ROOT` has **no default** — the agent must refuse to start rather than silently use
  `/tmp/syncthing` (`config.py:248`). One ERROR log, then a bounded retry, then idle-forever.
- `--syncthing-root` **is kept** as an optional flag (it wins over env, per §5.3) because `README.md`
  teaches it and a manual foreground run wants it.
- `NODE_ID` is **not** settable from the TOML at all (§3.1 Edge 2).

### 3.3 Q3–Q5 — as recommended; the spec's design sections stand

- **Q3**: `pyproject.toml:122` keeps `run-agent = "agent.run_agent:main"`. The `sci-run` vs `run-agent`
  naming inconsistency is **not** addressed here — cosmetic, and explicitly deferred (§13).
- **Q4**: exit codes `0/1/2/3/4` stand, and the two seams (`_hand_back_in_flight_job` for #103,
  `_write_final_state` for #100) must exist and be called in the recorded order.
- **Q5**: in-process observer, `critical=True` liveness task, exit `3` on `WatcherDead`. The
  limitation stands and must be documented: **liveness is a local thread/fd check and cannot detect a
  Syncthing *peer* dropping while the folder stays present and writable.** That gap belongs in #104's
  documentation, not in a claim in this issue.

---

## 4. Corrections to the issue body

Every citation in the #93 body and its comments was re-checked against `main` @ `0acb57b`.

### 4.1 Verified correct

| Citation | Status |
|---|---|
| `agent/run_agent.py:22` — the only TODO | **correct** (`# TODO: Implement agent logic`) |
| `agent/` is exactly `__init__.py` (0 bytes) + `run_agent.py` | **correct** |
| `pyproject.toml:117` includes `agent*` | **correct** |
| `pyproject.toml:122` `run-agent = "agent.run_agent:main"` | **correct** |
| `backend/core/config.py:248` `SYNCTHING_ROOT`, default `/tmp/syncthing` | **correct** (spec cited `:168` — drifted) |
| `backend/core/config.py:326` `LOG_LEVEL: str = "INFO"` | **correct** (spec cited `:246` — drifted) |
| `backend/core/config.py:344` `settings = Settings()` | **correct** (spec cited `:264` — drifted) |
| `shared/file_ops/path_utils.py:58` reads `SYNCTHING_ROOT` from `os.environ` | **correct, exact** |
| `shared/file_ops/yaml_utils.py` atomic temp-file + `os.replace` | **correct** |
| `syncthing_service.py:24` class declaration; `_process_file` at `:108-124` | **correct** (spec cited `:27-105` — see C1) |
| `syncthing_service.py:177-178` `observer.schedule(jobs_dir)` / `(nodes_dir)` | **correct, exact** |
| `syncthing_service.py:53-62` `_is_relevant_file`: `.yaml`-only, skip dotfiles and `.tmp` | **correct, exact** |
| `watchdog==4.0.0` (`pyproject.toml:28`) is already a hard dependency | **correct** — importing it from `agent/` adds nothing |
| `tomllib` is stdlib on 3.11+ and already used at `backend/core/config.py:9` | **correct** — TOML needs no new dependency |
| `AGENTS.md:21` documents `python agent/run_agent.py --node-id node-01` | **correct, and it fails today** (see C12) |
| `README.md` "Worker Agent" section | **correct** — now at `:209-217`, not `:202-210` |

### 4.2 Stale or wrong — flag these in the PR description

| # | Claim | Reality |
|---|---|---|
| **C1** | comment: `SyncthingEventHandler` at `syncthing_service.py:27-105` | The class is declared at **`syncthing_service.py:24`**. `:27` is `__init__`, not the class header. |
| **C2** | comment: `syncthing_service.py:60` skips `.tmp` | Line **60** is the *comment* `# Ignore temporary files`. The guard is at **`:61`**. |
| **C3** | comment: `syncthing_service.py:132-137` for the path shapes | Routing is **`:108-124`**: the jobs branch is `115-118` and the nodes branch is `119-121`. |
| **C4** | body: "`sys.exit(main())` at `:26` with no signal handling … leaves an orphaned child process" | The line number is right. The *mechanism* is wrong today: the agent spawns **no** child process, so there is nothing to orphan. The argument is **prospective** — #98 adds the child, and shutdown ordering is much cheaper now than under a live process. Restated so nobody "verifies" it and finds nothing. |
| **C5** | body: "the agent … cannot honour `LOG_LEVEL` (`config.py:246`)" | It can — it reads the same env var by name. The actual gap is that the agent has **no settings object at all**, so there is nowhere for a value to be honoured. This changes the fix (one settings model) from what the body implies (special-casing `LOG_LEVEL`). Note the name is now **`AGENT_LOG_LEVEL`** per §3.2. |
| **C6** | body AC: "`pytest` discovers the new tests and the coverage number … does not regress" | **Trivially satisfiable by doing nothing.** `testpaths = ["backend/tests"]` (`pyproject.toml:85`) means `agent/tests/` is never collected. As written the AC would pass with the tests unwritten. Rewritten as AC-13. |
| **C7** | body: "Reuse whatever #68 lands; if #68 has not landed, do not invent a second scheme — coordinate." | **Unresolvable as written and would block the issue indefinitely**: #68 is open and scoped to the *backend*. **Reinterpreted, not dropped** — the agent owns `configure_logging(level, node_id)` built on stdlib `logging`, using the same `basicConfig` mechanism as `backend/main.py:25`, with the node id attached by a **record factory + handler-level filter** (§5.4, corrected per **K8**) so it holds for records emitted from `shared.*` too. The contract established is *"each entrypoint owns a `configure_logging(level, context)` function"*, which is precisely what #68 would unify. **Zero backend edits; #68 stays unlanded.** |
| **C14** | `asyncio_mode = "auto"` (`pyproject.toml:84`) is global, and `--strict-markers` (`:89`) allows only `unit`, `integration`, `slow`, `websocket`. | `agent/tests/` needs no `asyncio` marker and may use **only** `unit` and `integration`. **No new marker.** |
| **C15** | `mypy` `exclude` (`pyproject.toml:67`) does not mention `agent`, and `[tool.ruff]` has no `exclude`. Verified: `ruff check agent/` → *All checks passed!*, `mypy agent/` → *Success: no issues found in 2 source files*. | The body's AC "confirm whether `agent/` is in scope" **is answered: `agent/` is already in scope for both, and already clean.** No `mypy-overrides` block is needed and **none should be added** — adding one would be a new exclusion, the opposite of the AC's intent. |

### 4.3 Gaps the body and comments do not mention

| # | Gap | Consequence for this spec |
|---|---|---|
| **C8** | `shared/file_ops/path_utils.py:58` and **everything built on it** — `get_syncthing_root`, `get_jobs_directory`, `get_nodes_directory`, `get_job_directory`, `get_job_state_file`, `get_node_state_file`, `is_within_syncthing_root` — read `os.environ["SYNCTHING_ROOT"]` directly, **with no injection point and no default**. A settings object alone is not enough to use them. | The strongest argument in Q2, and it is stated nowhere in the issue. Consequence: `AgentPaths` is computed from the **settings value**, never from `get_jobs_directory()`. The helpers are used for their side-effect-free siblings only (`ensure_directory`, `normalize_path`, `read_yaml`, `write_yaml`). Plus the deliberate one-way `os.environ` publication (§3.2), which is the other half of making them usable at all. |
| **C9** | **No `node_id` validation exists anywhere in the repo** (a grep for a node-id pattern returns nothing), while `get_node_state_file` (`path_utils.py:122-135`) interpolates it straight into a filename. `NODE_ID="../other"` is a path traversal. | `AgentSettings` validates `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`. Stated plainly: this closes **traversal, not impersonation** (R4). |
| **C10** | `backend/main.py:25` is `logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))` — a bad `LOG_LEVEL` raises `AttributeError` at startup. | Copying that line verbatim into the agent reproduces a crash-on-typo. The agent validates the level against a legal set and raises `ValueError` **naming the legal values**. |
| **C11** | `logging.basicConfig` is a **no-op when the root logger already has handlers** — which is true under `pytest`, and is also true in any process that installed a handler before calling `configure_logging`. | `configure_logging()` must set `root.setLevel(...)` explicitly, must be safe to call twice, and **must be the only thing that installs a handler on the root logger** (this is exactly how defect **D2** happened — §8). |
| **C12** | `AGENTS.md:21` documents the agent as `python agent/run_agent.py --node-id node-01`. **That command fails today** — `--syncthing-root` is `required=True` (`run_agent.py:15-17`), so argparse exits 2. The repo's own agent instructions are already wrong. | Fix in this PR alongside the `README.md` update. Cheap, and it is the file an agent reads first. |
| **C13** | `[tool.setuptools.packages.find] include = ["agent*"]` will pick up `agent.tests` as an installed package, exactly as it already picks up `backend.tests`. | Inert under the documented `pip install -e .`, but a wheel build copies the test tree into `site-packages`. Add `"agent.tests"` to `exclude`. Made **mandatory** by the decision record (§11, edit 3). |
| **C16** | *(new)* `shared/file_ops/path_utils.py:24-27` raises `TypeError` for any `~/…` because `path_obj[2:]` indexes a `Path`. | `~` support in the agent must go through `Path.expanduser()` **first**. This is the shared defect behind **D1**, **D4** and **D6**. The `shared/` fix stays out of scope (D6); the agent must not depend on it. |

---

## 5. Design

### 5.1 Module layout

```
agent/
├── __init__.py            (unchanged, 0 bytes)
├── run_agent.py           shim: parse args → build_settings → configure_logging → asyncio.run
├── config.py              AgentSettings, build_settings(), EXIT_* constants
├── logging_config.py      configure_logging(level, node_id), NodeIdFilter, LOG_FORMAT
├── paths.py               AgentPaths (frozen dataclass), resolve_paths(root, node_id, state_dir)
├── watcher.py             FolderWatcher, FolderEvent, SelfWriteLedger, WatcherDead
├── supervisor.py          TaskSupervisor, SupervisorTask, TaskFailure
├── loop.py                Agent
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_config.py
    ├── test_logging_config.py
    ├── test_paths.py
    ├── test_watcher.py
    ├── test_supervisor.py
    ├── test_loop.py
    ├── test_run_agent.py
    └── test_no_cluster_required.py
```

Six modules is the right size: `config`/`logging_config`/`paths` are independently consumable by
#95–#102, `watcher`/`supervisor` are the two mechanisms that must be provably correct, and `loop` is
only wiring. `paths.py` is ~30 lines and is kept separate because #97, #101 and #102 all need the
folder layout and none of them should re-derive it.

**Deliberate deviation from the backend:** `agent/config.py` must **not** define a module-level
`settings = AgentSettings()` — which is what `backend/core/config.py:344` does and is why the
backend's settings object is untestable in isolation. `run_agent.py` calls `build_settings(...)` and
passes the result down. **There is exactly one construction site.**

### 5.2 Settings — every field, with its precedence rule

```python
def build_settings(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,   # defaults to os.environ; injectable
    toml_path: Path | None = None,              # None → derive from the RESOLVED root
) -> AgentSettings: ...
```

All three inputs are injected explicitly. **No module reads `os.environ` or `Path.cwd()` at import
time.** This is the single mechanism that makes #58's bug class structurally impossible here, and it
is what lets every shutdown test use `SHUTDOWN_GRACE_S=0.1` instead of 30.

| Field | Env var | TOML key | Type | Required | Default | Also settable by CLI | Notes |
|---|---|---|---|---|---|---|---|
| `NODE_ID` | `NODE_ID` | **forbidden** | `str` | **yes** | — | `--node-id` | Validated `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` (closes **C9**). **No TOML source at all** (§3.1 Edge 2). No default: a guessed node id impersonates a node. |
| `SYNCTHING_ROOT` | `SYNCTHING_ROOT` | unreachable¹ | `Path` | **yes** | — | `--syncthing-root` | **No `/tmp/syncthing` default**, deliberately unlike `config.py:248`. An agent that silently syncs somewhere wrong is worse than one that refuses to start. Resolve with **`expanduser()` then `normalize_path()`** (**K9**). |
| `LOG_LEVEL` | `AGENT_LOG_LEVEL` | `AGENT_LOG_LEVEL` | `str` | no | `"INFO"` | `--log-level` | Same default as `config.py:326`, **new name** per §3.2. Validated against `{"CRITICAL","FATAL","ERROR","WARNING","WARN","INFO","DEBUG","NOTSET"}`; unknown raises `ValueError` listing them (**C10**), never `AttributeError`. |
| `SHUTDOWN_GRACE_S` | `AGENT_SHUTDOWN_GRACE_S` | `AGENT_SHUTDOWN_GRACE_S` | `float` | no | `30.0` | — | How long `Agent.shutdown()` waits for in-flight tasks after the stop event. Must be `> 0`. |
| `FOLDER_WATCH_INTERVAL_S` | `AGENT_FOLDER_WATCH_INTERVAL_S` | `AGENT_FOLDER_WATCH_INTERVAL_S` | `float` | no | `30.0` | — | Tick of the `folder-watch` task: are `<root>/jobs` and `<root>/nodes` present and writable? |
| `FOLDER_RETRY_MAX_S` | `AGENT_FOLDER_RETRY_MAX_S` | `AGENT_FOLDER_RETRY_MAX_S` | `float` | no | `60.0` | — | Bound on the **initial** wait for the folder. After it expires the agent is idle-but-alive forever — never a crash-loop. |
| `WATCHER_LIVENESS_INTERVAL_S` | `AGENT_WATCHER_LIVENESS_INTERVAL_S` | `AGENT_WATCHER_LIVENESS_INTERVAL_S` | `float` | no | `60.0` | — | Tick of the `watcher-liveness` task. |
| `AGENT_STATE_DIR` | `AGENT_STATE_DIR` | `AGENT_STATE_DIR` | `Path` | no | `<SYNCTHING_ROOT>/.agent/<NODE_ID>` | — | The agent's own durable state (#103). Under the root so it replicates — which is what makes #103 possible — but **not** under `jobs/` or `nodes/`, the only directories either watcher observes. `.`-prefixed and not `.yaml`, so `syncthing_service.py:60` would ignore it even if it were. |

¹ **`SYNCTHING_ROOT` in the TOML is a documented no-op.** The root is what *locates* the file, so the
file cannot supply the root. QA verified this is genuinely unreachable: `build_settings` raises before
the file is read when no root is otherwise available, and env/CLI always outrank it. Accept a key of
that name as an unknown key (§3.1 "unknown keys are rejected") **or** as a documented no-op —
whichever is implemented, it must be **loud**, not silently ignored. **NEEDS-HUMAN-DECISION NHD-3.**

**No credential field. None.** `SHARED_TOKEN`, `SECRET_KEY`, `JWT*` are absent by decision (#92).
`test_agent_settings_model_has_no_credential_field` asserts this structurally, so a future
implementer cannot quietly re-add one.

**No `LOG_FORMAT` *setting*.** The spec deliberately declined a format knob as speculative generality
until #68 picks one. That still holds: the format lives as a module constant **inside
`configure_logging()`**, which the spec already designated as "the single place it would go". It is
not a settings field and therefore not settable from the TOML. This is the fix shape for **D2**.

### 5.3 Precedence — and why it must be hand-merged

Highest to lowest:

1. CLI flag
2. environment variable
3. TOML config file
4. field default

**This cannot be expressed declaratively with the pinned stack, and getting it wrong is silent.**
Verified on **Python 3.12.7 / pydantic-settings 2.0.0**:

- `pydantic-settings==2.0.0` has **no TOML source at all** — `TomlConfigSettingsSource` does not
  exist and `toml_file` is not a key of `SettingsConfigDict.__annotations__`.
  Re-verified for this spec: `"TomlConfigSettingsSource" in dir(pydantic_settings)` → **`False`**.
- Its default source order is `(init_settings, env_settings, dotenv_settings, file_secret_settings)`,
  so **init kwargs outrank environment variables**.

Therefore `AgentSettings(**toml_dict)` — the obvious implementation — makes the **file beat the
environment**, exactly inverting the documented order, with no error and no warning. `build_settings`
must merge the layers itself into one flat dict and pass that as the single init-kwargs source:

```python
merged = {**from_toml,
          **{f: environ[f] for f in AgentSettings.model_fields if f in environ},
          **from_cli}
settings = AgentSettings(**merged)          # the ONLY construction site
```

To stop pydantic-settings re-reading the process environment behind the injected mapping, override
`settings_customise_sources` to return `(init_settings,)` only.
(Verified available on `pydantic-settings` 2.0.0 / Python 3.12.7: `BaseSettings` exposes
`settings_customise_sources`.)

**Then, and only then, publish the resolved root (§3.2):**

```python
os.environ["SYNCTHING_ROOT"] = str(resolved_root)   # deliberate, documented, one-way
```

`os.environ` is **mutated from** the resolved value and **never read back in**. Exactly one write
site. Pin with `test_published_root_is_not_read_back_in` and `test_build_settings_publishes_only_the_root`.

### 5.4 Logging setup — **corrected per K8**

`agent/logging_config.py`:

- `LOG_FORMAT` — a module constant, e.g. `"%(asctime)s %(levelname)s [node=%(node_id)s] %(name)s: %(message)s"`.
  **Not a settings field.**
- `class NodeIdFilter(logging.Filter)` — sets `record.node_id = self.node_id`, using
  `getattr(record, "node_id", "<unbound>")` on read so a record emitted before configuration is
  still formatted rather than raising.
- `configure_logging(level: str, node_id: str, *, stream=None) -> None` —
  1. resolve `level` via `getattr(logging, level.upper(), None)` against the legal set; `ValueError`
     listing them if unknown (**C10**);
  2. install a **`logging.setLogRecordFactory`** that stamps `node_id` on every record. This is the
     **only** mechanism that covers records emitted from `agent.*`, `shared.*`, `backend.*` and from
     a logger named anything at all;
  3. **unconditionally** `logging.getLogger().setLevel(resolved)` and install a handler using
     `LOG_FORMAT`, because `basicConfig` is a **no-op when the root logger already has handlers**
     (**C11**);
  4. install a `NodeIdFilter(node_id)` **on each handler as the backup path** for when a library
     replaces the record factory;
  5. be **idempotent**: replace a previously installed record factory / `NodeIdFilter` rather than
     stacking a second one.

> **Why this is corrected (K8).** The spec said to install the filter on the **root logger** "so
> records from `shared.file_ops.yaml_utils` also carry the node id". QA measured that this is
> **mechanically ineffective**: the stdlib's `Logger.callHandlers` walks `c.handlers` up the parent
> chain and calls `hdlr.handle(record)` — it never invokes a `Logger.filter`, which only happens on
> the emitting logger. A root filter therefore cannot annotate a record emitted by `agent.loop`.
> Record factory + handler-level filter is the correct mechanism. Both are needed: the factory is
> process-global and never uninstalled, so a library calling `setLogRecordFactory` would silently
> drop the guarantee — the handler filter is exactly the backup for that.

> **No backend change. #68 stays unlanded.** The contract established here is "each entrypoint owns a
> `configure_logging(level, context)` function", which is what #68 would unify.

### 5.5 The supervised loop's task contract

`agent/supervisor.py`:

```python
class TaskFailure(NamedTuple):
    task_name: str
    error: BaseException
    at: datetime

class SupervisorTask(NamedTuple):
    name: str
    coro_factory: Callable[[asyncio.Event], Coroutine[Any, Any, None]]
    interval_s: float
    critical: bool = False

class TaskSupervisor:
    def __init__(self, *, on_fatal: Callable[[str, BaseException], None],
                 on_shutdown: Callable[[], Awaitable[None]] | None = None) -> None
    def add_task(self, task: SupervisorTask) -> None      # before start(); raises if started
    def spawn(self, task: SupervisorTask) -> None         # after start(); raises if not started
    async def start(self) -> None
    async def shutdown(self, grace_s: float) -> list[TaskFailure]
    @property
    def failures(self) -> list[TaskFailure]
    @property
    def task_names(self) -> list[str]
```

`run_forever(name, coro_factory, interval_s, stop_event)` is the whole contract:

```
while not stop_event.is_set():
    try:
        await coro_factory(stop_event)          # one tick
    except asyncio.CancelledError:
        raise                                    # cancellation is not a failure
    except BaseException as exc:
        if critical: on_fatal(name, exc); return  # and the loop itself stops
        failures.append(TaskFailure(name, exc, utcnow())); logger.exception(...)
    await sleep_or_stop(interval_s, stop_event)
```

**What happens when one task raises — stated exactly:**

- **Non-critical task, any `BaseException`** → recorded in `supervisor.failures`, logged at **ERROR**
  via `logger.exception` (so the traceback is present), and the **task keeps running on its next
  tick**. Siblings are untouched — they are separate `asyncio.Task`s and nothing in `run_forever`
  propagates. This is AC-7.
- **Critical task, any `BaseException`** → routed to `on_fatal(name, exc)` and the task **returns**.
  `critical=True` exists for exactly one task in #93 (`watcher-liveness`), because a silent no-op
  agent is the worst outcome of the whole backlog and `probe_liveness` raising *is* the failure
  report. No new mechanism needed — the supervisor already has it.
- `asyncio.CancelledError` is re-raised in both cases and **never** recorded as a `TaskFailure`.
- `sleep_or_stop` awaits `stop_event.wait()` with a `timeout`, so shutdown is **immediate** and never
  waits out a 60 s sleep.

**`spawn` (K10 — carried forward from the QA verdict's finding).** `add_task` refuses after `start()`.
`spawn` is a separate, named method that registers and starts a task **after** `start()`. It must:

- refuse before `start()` (`test_spawn_before_start_raises`);
- no-op on an already-running name (`test_spawn_of_a_running_task_is_a_no_op`);
- leave the `add_task` guarantee intact (`test_add_task_after_start_raises` stays green);
- de-duplicate against the **running** set **and** the registered set, so a name collision cannot
  appear twice in `task_names` (the one nit QA raised in the lost implementation).

Tasks registered by `Agent.run()` in #93, all of which do I/O only against a local directory:

| name | body | interval | critical |
|---|---|---|---|
| `folder-watch` | `Agent._check_folder` — folder present + writable (`os.access`); **spawns the watcher tasks the first time it is ready** | `FOLDER_WATCH_INTERVAL_S` | no |
| `watcher-liveness` | `FolderWatcher.probe_liveness` — raises `WatcherDead` if `not observer.is_alive()` | `WATCHER_LIVENESS_INTERVAL_S` | **yes** |
| `event-drain` | `Agent._drain_events` — pops every queued `FolderEvent` and **logs** it. No parsing, no routing: that is #97/#101/#102. | `0.5` (fixed) | no |

`folder-watch` starting the watcher lazily is what turns "the bind mount dropped and came back" into
a self-heal rather than a restart. `Agent._ensure_watcher_started()` is guarded by a single
`self._watcher_started: bool`, logs the transition once, and is reachable **only** while that flag is
`False`, so it cannot double-register.

**When `_ensure_folder()` returns `False` at startup, `run()` registers `folder-watch` only.** The
other two are spawned by `folder-watch` when the folder first becomes ready — otherwise a watcher
started late would never get a liveness probe (**K10**). `test_no_liveness_task_without_a_folder`
pins this: `task_names == ["folder-watch"]`.

### 5.6 The watcher

`agent/watcher.py`:

```python
@dataclass(frozen=True)
class FolderEvent:
    kind: str            # "created" | "modified" | "deleted"
    path: Path
    observed_at: datetime

class WatcherDead(RuntimeError): ...

class SelfWriteLedger:
    MAX_REMEMBERED: Final[int] = 256
    def record(self, path: Path) -> None      # bounded, oldest-evicted
    def is_self_write(self, path: Path) -> bool
    def clear(self) -> None

class FolderWatcher:
    def __init__(self, paths: AgentPaths, *, ledger: SelfWriteLedger | None = None,
                 observer_factory: Callable[[], Observer] = Observer,
                 handler_class: type = _EventHandler) -> None
    def start(self) -> None
    def stop(self) -> None
    def is_alive(self) -> bool
    def probe_liveness(self) -> None            # raises WatcherDead
    def events(self) -> asyncio.Queue[FolderEvent]
```

- **Reuse, do not copy, `syncthing_service.py`.** The handler here is ~25 lines, not the backend's
  ~100. It is shorter because it drops `_process_file`/`_process_delete` routing (that is the
  backend's ingestion job), the 500 ms debounce (nothing to coalesce into), and the pending-future
  set (nothing is scheduled onto a loop). What it **keeps** is the two filters from
  `syncthing_service.py:53-62`: `.yaml` suffix only (case-insensitive), and skip dotfiles and `.tmp`.
  Those are **load-bearing** — they are what keeps the agent's own `write_yaml` temp files
  (`yaml_utils.py:88-99`, named `<name>.<rand>.tmp`) out of the event stream, **and** what keeps
  `nodes/agent.toml` invisible to both watchers (§3.1 Edge 1). Filters copied and cited; routing
  deliberately not copied.
- `observer_factory` and `handler_class` are injectable so tests substitute a fake observer and never
  open an inotify fd. This is the "no cluster" lever for the watcher.
- **Directory events are ignored** (`event.is_directory` → skip), matching `syncthing_service.py:42,46,50`.
- **`SelfWriteLedger` is belt-and-braces, and stated as such.** Syncthing generally suppresses echoes
  of local changes. The ledger exists for the case the comment names — the agent re-entering its own
  control loop on a self-written file — not because echo suppression is assumed broken. It is
  **in-memory and therefore not a durable guarantee**: after a restart the first event is always
  treated as observed. Recorded honestly rather than oversold.
- `probe_liveness()` **raises** instead of returning `False`, so it reuses the `critical=True` →
  `on_fatal` route rather than adding a second failure channel.
- **What liveness does and does not cover.** `observer.is_alive()` is a local thread/fd check. It
  detects a dead observer thread, a stopped emitter and a lost watch — the silent-no-op failure modes.
  It **cannot** detect a Syncthing *peer* dropping while the folder stays present and writable. That
  is out of scope and must not be claimed (R8, #104's documentation).

### 5.7 `Agent` and the shutdown sequence

```python
class Agent:
    def __init__(self, settings: AgentSettings, *, watcher: FolderWatcher | None = None,
                 stop: asyncio.Event | None = None) -> None
    async def run(self) -> int          # the process exit code
    async def _ensure_folder(self) -> bool
    async def _check_folder(self) -> None
    async def _activate_watcher_tasks(self) -> None
    async def _drain_events(self) -> None
    async def _hand_back_in_flight_job(self) -> None   # no-op seam for #103
    async def _write_final_state(self) -> None         # no-op seam for #100
    async def _shutdown_sequence(self) -> int
```

The `watcher` and `stop` constructor kwargs are the seam that makes every lifecycle test in-process:
inject a fake watcher, or a pre-set `stop` event to skip signal handling entirely.

`run()`:

1. `self._supervisor` created; `self._stop` resolved (injected or a fresh `asyncio.Event`).
2. **Signal handlers installed here, on the running loop**, via `loop.add_signal_handler(SIGTERM, …)` /
   `(SIGINT, …)`, both → `self._stop.set()`. They live in `run()` rather than `__init__` because
   `add_signal_handler` requires a running loop — which is also what lets a test send a real signal at
   a real `Agent.run()`.
   **Windows / non-main-thread fallback is mandatory:** if `add_signal_handler` raises
   `NotImplementedError`, `run()` logs a warning once and relies on the injectable `stop` event.
   Without this the suite cannot run on Windows (R7). Pin with
   `test_signal_handlers_fall_back_where_unsupported`.
3. `await self._ensure_folder()` — bounded wait, see below.
4. `await self._ensure_watcher_started()`; register the supervisor tasks; `await supervisor.start()`.
5. `await self._stop.wait()`.
6. `return await self._shutdown_sequence()`.

**`run_agent.py` has exactly one logging entry point.** `run_agent.py` is a shim: parse args →
`build_settings` → `configure_logging` → `asyncio.run(Agent(settings).run())` → return the int exit
code. It must **not** install a handler of its own before calling `configure_logging` — that is
exactly the defect **D2** (§8), and `main()` must not exist as a second logging path.

`_ensure_folder()` — the "no crash-loop" mechanism:

- Missing `jobs/`/`nodes/` are created via `ensure_directory` (`path_utils.py:36-48`).
- Writability is checked with `os.access` (**K11**) — **no write probe.** A write probe on every
  30 s tick would fan ~4 replicated file events per tick per node into the one directory the backend
  also watches, for no new information. This reasoning goes in the method's docstring, not left
  implicit.
- On failure: one **ERROR** log, then retry every `1 s` up to `FOLDER_RETRY_MAX_S`, **WARN** each retry
  with the remaining budget. After the budget: one **ERROR** ("giving up waiting for the Syncthing
  folder; staying alive and idle") and return `False`.
- Returning `False` is **not** fatal. `run()` registers `folder-watch` only; `folder-watch` starts the
  watcher the moment the folder appears (**K10**).

`_shutdown_sequence()` — ordered, every step logged, and the two seams that #103/#100 fill:

| # | Step | In #93 |
|---|---|---|
| 1 | `logger.info("shutdown: signalling %d task(s)", n)`; `stop_event.set()` — tasks stop writing anything new | real |
| 2 | `logger.info("shutdown: awaiting in-flight tasks (grace=%.1fs)")`; `await supervisor.shutdown(grace_s)` | real |
| 3 | `await self._hand_back_in_flight_job()` — **no-op**, `logger.debug("no in-flight job to hand back")` | **seam for #103** |
| 4 | `watcher.stop()` — `observer.stop()`, `observer.join()`, plus `cancel_pending()` for debounced work, mirroring `syncthing_service.py:89-94` | real |
| 5 | `await self._write_final_state()` — **no-op**, `logger.debug("no durable state to write")` | **seam for #100** |
| 6 | return the exit code | real |

### 5.8 Exit codes

New, defined here so #104's systemd unit inherits the table:

| Code | Meaning |
|---|---|
| `0` | clean shutdown via `SIGTERM`/`SIGINT` |
| `1` | unrecoverable configuration error (missing `NODE_ID`, bad `SYNCTHING_ROOT`, unknown `LOG_LEVEL`, malformed TOML, `NODE_ID` present in the TOML) |
| `2` | unexpected internal error |
| `3` | **watcher liveness lost** — fatal and logged |
| `4` | shutdown grace expired with tasks still running |
| — | **folder not ready is not an exit code.** The agent waits, logs, and idles. That is the "no crash-loop" requirement. |

---

## 6. User stories

- **US-1 (operator).** I install the package, run the agent on a node, and it starts, tells me its
  node id and which folder it is using, and stays up — *even when the Syncthing folder is not there
  yet*. I do not want systemd restarting it three times in ten seconds.
- **US-2 (operator).** I set `AGENT_LOG_LEVEL=DEBUG` and **every line says which node it came from**,
  so four nodes writing into one journal are still separable. *(This is the story **D2** broke; AC-5
  is worded to make it testable — K12.)*
- **US-3 (operator).** I `systemctl stop` the agent and it exits `0` after a short, ordered shutdown —
  no orphaned child, no half-written file, no surprise `SIGKILL` from `TimeoutStopSec`.
- **US-4 (operator).** I put my configuration in one file inside the Syncthing folder, so one edit
  configures the whole fleet — and my shell environment still wins, so I can override one value for a
  single run without editing the file.
- **US-5 (developer).** I can write a test for the agent with no Syncthing, no backend, no network and
  no `os.environ` monkeypatching.
- **US-6 (developer, paying it forward).** When #97 adds job discovery, I add one task to an existing
  supervisor instead of writing a process from scratch.
- **US-7 (operator).** If the agent silently stops seeing the folder, I find out from a log line and
  the process exits so systemd can act — not from a node that has claimed to be online and done nothing
  for a week.
- **US-8 (security reviewer).** The agent holds no credential, so arbitrary submitted commands cannot
  inherit one, and that stays true as the backlog grows.

---

## 7. Acceptance criteria — AC-1 … AC-18

Merged from the spec (S4) and the decision record (S6). Every AC maps to at least one named test,
except where marked *gate*. Named tests are enumerated in §9.

> **⚠ TWO criteria were upgraded from optional to MANDATORY by the decision record (S6), plus two
> more it introduced. Do not treat any of them as negotiable:**

| Criterion | Was | Now | Because |
|---|---|---|---|
| **§5.3 precedence** — env must beat the TOML file | conditional on Q1 = B | **MANDATORY** — `test_env_overrides_toml_file`, **extended** to `~` and relative roots | R1 is live. The spec called it "the single most likely silent defect in this issue". The spec's version of the test uses an absolute root only and **would not have caught D1**. |
| **`.toml` filename constraint** | recommended / assumed | **MANDATORY** — `test_config_file_toml_suffix_is_ignored_by_the_backend_watcher`, against the **backend's live** filter | The suffix is what stops `nodes/agent.yaml` becoming a phantom node (§3.1 Edge 1). |
| `os.environ` publication of the root | "a deliberate, documented step" | **pinned by a named test** — `test_build_settings_publishes_syncthing_root_to_os_environ` | S6 made the side effect in scope; QA pinned it two ways. |
| `NODE_ID` refused in the TOML | not contemplated | **AC-3**, with a named test | S6 Edge 2. Every node reads the same replicated file. |

- **AC-1** — Given `agent/run_agent.py`, When it is inspected, Then it contains no `print` call and no
  `TODO`, and `main()` delegates to `Agent` and returns its exit code.
  → `test_run_agent_module_has_no_print_and_no_todo`, `test_main_delegates_to_agent_run`,
  `test_main_returns_zero_on_clean_shutdown`, `test_main_returns_one_on_config_error`
- **AC-2** — Given conflicting CLI / env / TOML values, When `build_settings()` is called, Then
  precedence is **CLI > env > TOML > default**, and no resolution path reads `Path.cwd()`.
  → `test_cli_overrides_env`, **`test_env_overrides_toml_file`**, `test_cli_log_level_wins_over_everything`,
  `test_toml_used_when_env_unset`, `test_defaults_when_nothing_configured`,
  `test_absent_toml_is_not_an_error`, `test_unset_optional_flag_is_not_treated_as_a_value`,
  `test_build_settings_does_not_read_process_environment`, `test_derived_config_file_is_read_without_an_explicit_path`,
  **plus the D1 extension**: `test_config_file_is_found_for_a_tilde_root`,
  `test_config_file_is_found_for_a_relative_root`
- **AC-3** — Given a `NODE_ID` that is empty, contains `/`, or is `..`, When settings are built, Then
  construction fails with an error naming the field and the pattern. **And given a `NODE_ID` key in
  `nodes/agent.toml`, When settings are built, Then construction fails with an error naming the file
  and explaining that every node reads the same replicated file.**
  → `test_node_id_rejects_path_separators`, `test_missing_node_id_is_rejected_with_actionable_message`,
  `test_node_id_pattern_matches_documented_regex`, **`test_node_id_in_toml_is_rejected`**
- **AC-4** — *gate* — `ruff check agent/` and `mypy agent/` are clean, and `agent/` contributes **0**
  to `ruff check .` and to `mypy .` (see §10.4 for the baseline and how to re-measure).
- **AC-5** — Given `configure_logging(level, node_id)`, When records are emitted from `agent.*`
  **and** from `shared.file_ops.*` **and** from an unrelated third-party logger, Then (a) every
  `LogRecord` carries `node_id`; (b) **a line rendered through the installed handler contains the node
  id** — the operator-facing half of US-2, strengthened per **K12**; (c) an invalid level raises
  `ValueError` listing the legal levels; (d) calling it twice is safe.
  → `test_every_record_carries_node_id`, **`test_rendered_line_contains_the_node_id`**,
  `test_unrelated_third_party_logger_carries_node_id`, `test_handler_filter_stamps_a_record_with_a_replaced_factory`,
  `test_configure_logging_honours_level`, `test_unknown_log_level_is_rejected_listing_valid_levels`,
  `test_configure_logging_is_idempotent`, `test_record_without_node_id_does_not_raise`,
  `test_node_id_filter_replaces_previous_node_id`
- **AC-6** — Given a running `Agent`, When the process is sent `SIGTERM` and separately `SIGINT`, Then
  `run()` returns `0`, the shutdown steps ran **in the recorded order**, the process is still alive
  afterwards, and `hand_back_in_flight_job` ran before the watcher was stopped.
  → `test_sigterm_triggers_orderly_shutdown`, `test_sigint_triggers_orderly_shutdown`,
  `test_shutdown_sequence_runs_steps_in_order`, `test_hand_back_in_flight_job_precedes_watcher_stop`,
  `test_write_final_state_seam_is_called`, `test_shutdown_sets_stop_event_and_returns_immediately`,
  `test_shutdown_returns_failures_and_marks_grace_expiry`, `test_stop_is_safe_to_call_twice`,
  `test_shutdown_grace_expiry_returns_exit_code_4`, `test_signal_handlers_fall_back_where_unsupported`
- **AC-7** — Given two supervisor tasks where one always raises, When several ticks pass, Then the
  sibling's counter keeps climbing, the supervisor is still running, and exactly one `TaskFailure`
  per failure is recorded and logged at ERROR with a traceback. **`BaseException` subclasses too.**
  → `test_raising_task_does_not_stop_sibling_task`, `test_task_failure_is_recorded_and_logged`,
  `test_cancellation_is_not_a_failure`, `test_base_exception_in_task_is_isolated`,
  `test_raising_task_does_not_stop_the_agent_process`
- **AC-8** — Given a `critical=True` task whose body raises, When it ticks, Then `on_fatal` is called
  once with `(name, exc)` and `Agent.run()` returns exit code `3`.
  → `test_critical_task_failure_calls_on_fatal`, `test_non_critical_failure_does_not_call_on_fatal`,
  `test_liveness_task_is_the_only_critical_one`, `test_dead_watcher_returns_exit_code_3`
- **AC-9** — Given a Syncthing root that can never exist (its parent is a regular file), When the agent
  starts, Then it logs one ERROR, retries on a bounded schedule, gives up with a second ERROR, and
  **keeps waiting forever** — `run()` never returns, and no retry occurs after the budget.
  → `test_unwritable_root_logs_error_and_stays_alive`, `test_folder_retry_is_bounded_and_backs_off`,
  `test_no_retry_after_budget_exhausted`, `test_missing_subdirectories_are_created`,
  `test_watcher_start_failure_is_not_fatal`, `test_unwritable_folder_is_reported_not_written_to`
- **AC-10** — Given a path the agent has written itself, When the watcher sees an event for it, Then no
  `FolderEvent` is emitted; an event for any other path **is** emitted; and the ledger evicts beyond
  `MAX_REMEMBERED`.
  → `test_self_write_is_not_emitted_as_an_observed_event`,
  `test_event_for_other_path_is_still_emitted`, `test_ledger_is_bounded`, `test_clear_empties_the_ledger`,
  `test_tmp_and_dotfiles_are_ignored`, `test_non_yaml_suffix_is_ignored`,
  `test_yaml_suffix_is_case_insensitive`, `test_directory_events_are_ignored`,
  `test_all_three_event_kinds_are_reported`, `test_agent_filter_matches_the_backend_filter`,
  `test_write_yaml_through_a_real_watcher_is_not_re_entered` *(integration — the only test that opens
  a real inotify fd; `skipif` on platforms without it)*,
  **`test_real_watcher_reports_a_foreign_write`** *(the control that makes "zero events" mean
  something — without it a deaf watcher also produces zero)*
- **AC-11** — Given a watcher whose `is_alive()` flips to `False`, When the liveness interval elapses,
  Then the agent logs an ERROR naming the watcher and exits `3` rather than idling; a watcher that
  stays alive does not exit.
  → `test_probe_liveness_raises_when_observer_is_dead`,
  `test_probe_liveness_passes_when_observer_is_alive`,
  `test_probe_liveness_raises_when_never_started`, `test_dead_watcher_returns_exit_code_3`,
  `test_alive_watcher_does_not_exit`
- **AC-12** — Given a watcher with no folder behind it, When `start()` is called, Then the agent
  registers no liveness task (nothing to be alive) and starts the watcher lazily once `folder-watch`
  sees the folder — **and then registers the liveness task** (K10).
  → `test_no_liveness_task_without_a_folder`, `test_all_three_tasks_are_registered_when_ready`,
  `test_watcher_starts_lazily_once_folder_appears`,
  `test_late_appearing_folder_activates_the_rest`,
  `test_folder_watch_starts_the_watcher_after_the_folder_appears`,
  `test_activate_watcher_tasks_is_a_no_op_before_start`, `test_start_schedules_jobs_and_nodes`,
  `test_start_with_absent_folder_raises_a_clear_error`,
  `test_event_drain_logs_observed_events_without_parsing`
- **AC-13** — Given `agent/tests/`, When `pytest` runs with default options, Then it is collected:
  `pyproject.toml`'s `testpaths` lists `"agent/tests"`; `[tool.coverage.run].source` includes
  `"agent"`; and `agent.tests` is excluded from packaged distributions.
  → `test_pyproject_testpaths_include_agent_tests`, `test_pyproject_coverage_measures_the_agent`,
  `test_pyproject_excludes_the_agent_test_package_from_wheels`
- **AC-14** — Given the agent with `SHARED_TOKEN` and `SYNCTHING_ROOT` deleted from the environment,
  When a full startup/shutdown cycle runs, Then it exits `0`; and no field on `AgentSettings` matches
  `TOKEN|SECRET|PASSWORD|KEY|CREDENTIAL`.
  → **`test_agent_suite_passes_with_hostile_environment`** *(the spec named this
  `test_agent_operates_with_shared_token_unset`; QA renamed it to describe what it actually does —
  use the QA name)*, `test_agent_settings_model_has_no_credential_field`,
  `test_a_credential_in_the_environment_changes_nothing`
- **AC-15** — Given every module under `agent/`, When their ASTs are parsed, Then none imports
  `httpx`, `requests`, `urllib.request`, `socket`, `websockets`, `jose` or `jwt`. This is the
  mechanical enforcement of the #92 file-only decision.
  → `test_agent_imports_no_network_module`, `test_every_third_party_import_is_expected`
- **AC-16** — Given the agent test suite, When it is collected with a hostile environment
  (`SYNCTHING_ROOT` pointing at a nonexistent path, `SHARED_TOKEN` unset), Then every `agent.*` module
  imports and the suite passes with no cluster.
  → `test_every_agent_module_imports_without_cluster_environment`,
  `test_agent_suite_passes_with_hostile_environment`
- **AC-17** — *gate* — the coverage number reported by `pytest --cov` **before and after** the
  `pyproject.toml` change is recorded in the PR, with the interpreter version, **and the per-file
  coverage of every module under `agent/` is recorded**. **Mandatory artifact; a missing number is a
  failed AC.** See §10.3 and §15/NHD-1 for what "the number" now means.
- **AC-18** — Given the `README.md` "Worker Agent" section (`README.md:209-217`) and `AGENTS.md:21`,
  When they are read, Then the documented command is one that actually runs today. `AGENTS.md:21`
  currently omits a `required=True` flag and exits 2 (C12).
  → *gate* — a docs diff in the PR, cross-checked against `run-agent --help`, plus
  `test_run_agent_help_matches_the_documented_flags`,
  `test_documented_command_is_accepted_by_the_parser`, `test_agents_md_quick_reference_command_runs`,
  `test_readme_documents_the_worker_agent`, `test_help_output_exits_zero`,
  `test_no_config_flag_is_offered`

---

## 8. Carry-forward defects — D1…D6

> **These are defects recorded against a QA-verified implementation that no longer exists. D1, D4 and
> D6 are defects in the *design* — re-implementing that design as-is reproduces them, and §5 has been
> corrected to prevent each. D2, D3 and D5 were introduced by that implementation's deviations from
> §5; they are prevented by following §5, but they are also why AC-5 is worded as it is.**
>
> **Do not ship this issue until each of D1–D4 has a test or a design change that makes it
> unreachable.**

### D1 — **medium** — the config file is located from the *unresolved* root, so a `~` or relative `SYNCTHING_ROOT` silently skips the shared `agent.toml`

The lost implementation derived the path before resolution:

```python
path = config_file_path(Path(str(root)))   # root is RAW — "~" or a relative path
from_toml = toml_layer(path)               # is_file() fails → {} — SILENTLY
...
settings = AgentSettings(**merged)          # expansion/absolute happens in here
```

`root = from_cli.get("SYNCTHING_ROOT") or env.get("SYNCTHING_ROOT")` is the unexpanded string.
`is_file()` then fails on `~/syncthing/nodes/agent.toml`, `toml_layer` returns `{}` — **no error, no
warning**, which is the exact class the decision record warns about.

**Observed:** with a tilde root, the file **exists** at the resolved location, the derived path is
`~/syncthing/nodes/agent.toml`, the resolved root is correct, and `AGENT_LOG_LEVEL` came back `INFO`
where the file said `WARNING`. With a relative root, **the same command in the same environment gave a
different answer depending on the CWD.**

**Contradicted, in the shipped tree:** the agent's own docstrings ("the same command behaves
identically from any working directory"; "derives … from the resolved root"), the `README.md`
("the location is derived from the **resolved** root"), and the decision record ("the same command
behaves identically from any directory").

**Not caught by** `test_derived_config_file_is_read_without_an_explicit_path`, **which only uses an
absolute root.**

**Impact:** a node configured with `SYNCTHING_ROOT=~/syncthing` — a form the module explicitly
supports — runs on defaults while its siblings with absolute roots read the file.

**Fix shape (mandated by §5.2/§3.1):** resolve the root **first**
(`root = normalize_path(expanduser(str(root)))`), **then** derive
`toml_path = root / "nodes" / "agent.toml"`, **then** read the file. A missing file after that
resolution is a genuine absence, not a silent skip.

**Tests:** `test_config_file_is_found_for_a_tilde_root`, `test_config_file_is_found_for_a_relative_root`
— and the spec's original `test_env_overrides_toml_file` must additionally cover non-absolute roots,
or the R1 regression test remains blind to D1.

### D2 — **medium** — `LOG_FORMAT`, and therefore the visible node id, was never installed in the shipped process

`run_agent.main` called a `_bootstrap_logging()` **first**, which installed a plain `StreamHandler`
with `"%(levelname)s: %(message)s"`. `configure_logging`'s `logging.basicConfig(...)` was then the
documented C11 **no-op**. So the `%(node_id)s` field was dead in production and the README's promise
was not delivered.

```
handlers after _bootstrap_logging : ['StreamHandler']
handler formatter _fmt            : %(levelname)s: %(message)s
emitted line                      : 'INFO: a line an operator would read'
   contains '[node=node-shipped]'? False
control, fresh root logger        : '... [node=node-fresh] a line an operator would read'
```

The live `SIGTERM` run's log contained **no `[node=` anywhere**; the exit-4 run, which called
`configure_logging` directly, did.

**Contradicted:** `README.md` ("Every log line carries the node id, so four nodes writing into one
journal stay separable") and **US-2**. AC-5 as worded was about the *record*, which was met; the
operator-facing claim was not.

**Fix shape:** §5.4 already has it — `configure_logging` installs the handler **and** the
`LOG_FORMAT`, and `run_agent.py` has **exactly one** logging entry point. Either the bootstrap goes,
or it is folded into `configure_logging`. What must not happen again is a handler being installed
anywhere else first. AC-5 is strengthened (**K12**) so this cannot regress silently.

### D3 — **low** — `NodeIdFormatter` was unreachable dead code

Defined and exported, never installed: `configure_logging` passed `format=LOG_FORMAT` to
`basicConfig`, which builds a plain `logging.Formatter`. The `<unbound>` fallback protected only the
one test that constructed it by hand.

Harmless today, because the handler-level `NodeIdFilter` stamps the record anyway. But **carrying a
class whose reason for existing is not true is the thing a future reviewer will trust.**

**Fix shape:** §5.4 has no `NodeIdFormatter` — only `LOG_FORMAT`, `NodeIdFilter` and the record
factory, all three of which are wired. **Either wire a thing in or do not write it.** Do not port
this class.

### D4 — **low** — `agent/paths.py` documented a contract the function did not have

`resolve_paths`' docstring claimed "the Syncthing root; normalised, so `~` works and the result is
absolute". `resolve_paths("~/syncthing", "node-1")` raised
`TypeError: 'PosixPath' object is not subscriptable` (`path_utils.py:27`, **C16**). Production was safe
because settings pre-expand, but `resolve_paths` is public and exported, so the docstring was wrong.

**Fix shape:** one line — `expanduser()` inside `resolve_paths`, or correct the docstring. **Prefer
the former:** a public path helper that raises on `~` is a trap for #97, #101 and #102.
**Test:** `test_resolve_paths_expands_user`.

### D5 — **low** — `ruff format --check agent/` would rewrite 7 files

Not a gate (`ruff check` is clean), but `AGENTS.md`'s "Backend lint" is
`ruff check . && ruff format . && mypy .`, so the documented lint command is not a no-op on a branch
that adds new files. `main` already had 3 repo-wide, so this is not a new class of problem — but it
was 7 files' worth on top.

**Fix shape:** run `ruff format agent/` on the new code. Whether the branch must also be
`ruff format --check`-clean repo-wide is **NHD-2 (§15)**.

### D6 — **informational** — the `shared/` bug behind D1/D4 is still live

`shared/file_ops/path_utils.py:24-27` raises `TypeError` for any `~/…`, which also takes out
`ensure_directory("~/x")` and therefore `get_syncthing_root()` and the five helpers on top of it —
**i.e. #98 will hit it.** Correctly out of scope here; recorded so it is not lost.

**Consequence for this issue:** the agent must not depend on it. Use `Path.expanduser()` before
`normalize_path()`, everywhere, including inside `resolve_paths` (D4).

### 8.1 How the QA verdict's own evidence was classified

The orchestrator's loss comment states all five "are defects in the **design**". That is accurate for
**D1, D4 and D6** (which is why §5 was corrected for them) and **overstated for D2, D3 and D5**, which
were introduced by deviations from §5 rather than by §5 itself. The distinction matters to the SWE:
D1/D4/D6 are traps in the design that must be actively avoided; D2/D3/D5 are traps in a particular
implementation that must simply not be repeated.

---

## 9. Carry-forward verification techniques → named tests

QA ran **mutation testing** against the lost implementation. Those mutations are the strongest
evidence in the whole thread, and four of them must be re-run. **Use the names below** so the
re-run is comparable.

### 9.1 The four mandatory mutation tests — re-run all of them

| ID | Technique | How to reproduce the mutation | Named test | Result in QA |
|---|---|---|---|---|
| **VM-1** | **Precedence inversion, both shapes** | (a) Merge TOML last: `merged = {**env_layer(env), **from_cli, **from_toml}`. (b) The *faithful* pydantic-settings inversion: restore `settings_customise_sources` to `(init_settings, env_settings)` **and** pass the TOML layer as init kwargs — literally R1. | `test_env_overrides_toml_file` (+ `test_cli_log_level_wins_over_everything`) | **Caught in both shapes.** (a) 2 failed / 178 passed. (b) `test_env_overrides_toml_file` among 10 failures. *R1 is genuinely shut.* **Extend this test to a `~` root and a relative root — it is currently blind to D1.** |
| **VM-2** | **`.toml` → `.yaml` rename, proven against the backend's live `_is_relevant_file`** | Rename `CONFIG_FILE_NAME` to `agent.yaml`. QA **removed the literal-name assertion and the suffix assertion first**, so the only thing left was the test against the backend's live filter — and it still went red. | `test_config_file_toml_suffix_is_ignored_by_the_backend_watcher` (asserts `SyncthingEventHandler._is_relevant_file(str(<nodes>/agent.toml)) is False`) | **Caught:** `assert True is False`. Directly evaluating the backend's real method: `agent.toml` → `False`, `agent.yaml` → `True`, `agent.YAML` → `True`, and `_process_file('<root>/nodes/agent.yaml')` routes to `_process_node_file` → a phantom node. |
| **VM-3** | **`NODE_ID` in the TOML refused** | — | `test_node_id_in_toml_is_rejected` | **Refused, exit 1, live:** `ERROR: agent configuration error: /…/nodes/agent.toml: NODE_ID must not appear in the shared config file. Every node reads the same replicated file, so a NODE_ID here would make every agent claim the same identity. Pass --node-id or set the NODE_ID environment variable on this machine instead.` With `NODE_ID` *only* in the shared file, the agent refuses rather than claiming that identity. |
| **VM-4** | **One-way `os.environ` write** | — | `test_build_settings_publishes_syncthing_root_to_os_environ`, `test_published_root_is_not_read_back_in`, `test_build_settings_publishes_only_the_root` | **One write site, after `AgentSettings(**merged)` has resolved everything;** one read in the whole package, and it is not a precedence input. Exactly one key added to `os.environ`. |

### 9.2 Other techniques QA ran that must stay

| Technique | Named test / check |
|---|---|
| Both `SIGTERM` and `SIGINT` sent to a **real process** (`run-agent` + `kill`) | `test_sigterm_triggers_orderly_shutdown`, `test_sigint_triggers_orderly_shutdown`; plus a real subprocess run from a directory **other than the repo root** |
| All **five exit codes reached in real processes** | `0` (SIGTERM), `0` (SIGINT), `1` (config), `2` (internal), `3` (killed observer), `4` (stubborn task). **Exit 4 is reachable only by injecting a task that ignores the stop event** — no shipped task does — so it is a test-subclass exercise, not a live-process one. |
| Observer killed from outside the real process | `test_dead_watcher_returns_exit_code_3` → `ERROR: critical task 'watcher-liveness' failed`, exit **3** |
| Watcher forced to raise on every start | `test_watcher_start_failure_is_not_fatal` → 6 recorded `folder-watch` failures, process alive, exit `0` on `SIGTERM` |
| `os.access` vs a real write, **7 permission shapes** | 0 disagreements. Two shapes are **not constructible** without privileges (read-only mount needs root; `chattr +i` needs `CAP_LINUX_IMMUTABLE`). `access()` returns `EROFS` on a read-only mount, but **ENOSPC/quota would be missed**. Neither is live in #93 — the agent writes nothing into `jobs/`/`nodes/` yet (R11). The first real write, #95, is the true test. |
| Third-party import surface of the shipped modules | `test_every_third_party_import_is_expected` → exactly `pydantic`, `pydantic_settings`, `watchdog` |
| Full-suite stability | 5 consecutive `agent/tests` runs, reversed file order, each file in isolation, and a hostile shell environment — **zero variance**. Re-do this. |

### 9.3 The carried-forward named tests (≈70)

QA grepped `def <name>` across `agent/tests/` and confirmed **all 70 of the spec's names present,
one renamed** (`test_agent_operates_with_shared_token_unset` → `test_agent_suite_passes_with_hostile_environment`).
QA then added more. Reproduce these names so the re-run is comparable.

**`agent/tests/test_config.py`**
`test_defaults_when_nothing_configured` · `test_missing_node_id_is_rejected_with_actionable_message` ·
`test_node_id_from_env` · `test_cli_overrides_env` · `test_cli_log_level_wins_over_everything` ·
`test_unset_optional_flag_is_not_treated_as_a_value` · `test_toml_used_when_env_unset` ·
**`test_env_overrides_toml_file`** · `test_derived_config_file_is_read_without_an_explicit_path` ·
**`test_config_file_is_found_for_a_tilde_root`** · **`test_config_file_is_found_for_a_relative_root`** ·
**`test_config_file_toml_suffix_is_ignored_by_the_backend_watcher`** ·
`test_absent_toml_is_not_an_error` · `test_malformed_toml_raises_with_the_path` ·
`test_unknown_toml_key_is_rejected` · **`test_node_id_in_toml_is_rejected`** ·
`test_node_id_rejects_path_separators` · `test_node_id_pattern_matches_documented_regex` ·
`test_unknown_log_level_is_rejected_listing_valid_levels` · `test_syncthing_root_has_no_default` ·
`test_syncthing_root_expands_user` · `test_negative_intervals_rejected` ·
`test_build_settings_does_not_read_process_environment` ·
`test_agent_settings_model_has_no_credential_field` ·
`test_a_credential_in_the_environment_changes_nothing` ·
**`test_build_settings_publishes_syncthing_root_to_os_environ`** ·
**`test_build_settings_publishes_only_the_root`** · **`test_published_root_is_not_read_back_in`**

**`agent/tests/test_logging_config.py`**
`test_every_record_carries_node_id` · **`test_rendered_line_contains_the_node_id`** ·
**`test_unrelated_third_party_logger_carries_node_id`** ·
**`test_handler_filter_stamps_a_record_with_a_replaced_factory`** ·
`test_configure_logging_honours_level` · `test_unknown_log_level_is_rejected_listing_valid_levels` ·
`test_configure_logging_is_idempotent` · `test_record_without_node_id_does_not_raise` ·
`test_node_id_filter_replaces_previous_node_id`

**`agent/tests/test_paths.py`**
`test_paths_resolve_jobs_nodes_and_state_dir` · `test_state_dir_is_outside_jobs_and_nodes` ·
`test_node_file_matches_shared_convention` · `test_paths_do_not_use_os_environ` ·
`test_paths_are_absolute_and_normalized` · **`test_resolve_paths_expands_user`** *(D4)*

**`agent/tests/test_supervisor.py`**
`test_raising_task_does_not_stop_sibling_task` · `test_task_failure_is_recorded_and_logged` ·
`test_cancellation_is_not_a_failure` · `test_critical_task_failure_calls_on_fatal` ·
`test_non_critical_failure_does_not_call_on_fatal` · `test_liveness_task_is_the_only_critical_one` ·
`test_shutdown_sets_stop_event_and_returns_immediately` ·
`test_shutdown_returns_failures_and_marks_grace_expiry` · `test_add_task_after_start_raises` ·
**`test_spawn_before_start_raises`** · **`test_spawn_of_a_running_task_is_a_no_op`** ·
**`test_spawn_does_not_duplicate_a_registered_name`** *(QA's one nit, K10)* ·
`test_base_exception_in_task_is_isolated`

**`agent/tests/test_watcher.py`**
`test_self_write_is_not_emitted_as_an_observed_event` · `test_event_for_other_path_is_still_emitted` ·
`test_tmp_and_dotfiles_are_ignored` *(includes `agent.toml`)* · `test_non_yaml_suffix_is_ignored` ·
`test_yaml_suffix_is_case_insensitive` · `test_directory_events_are_ignored` ·
`test_all_three_event_kinds_are_reported` · `test_ledger_is_bounded` · `test_clear_empties_the_ledger` ·
`test_probe_liveness_raises_when_observer_is_dead` · `test_probe_liveness_passes_when_observer_is_alive` ·
`test_probe_liveness_raises_when_never_started` · `test_start_schedules_jobs_and_nodes` ·
`test_start_with_absent_folder_raises_a_clear_error` · `test_stop_is_safe_to_call_twice` ·
`test_watcher_start_failure_is_not_fatal` · `test_unwritable_folder_is_reported_not_written_to` ·
`test_agent_filter_matches_the_backend_filter` *(compares the agent's `_is_relevant_file` against the
backend's **live** one on 7 names — this closes **R5** properly)* ·
`test_write_yaml_through_a_real_watcher_is_not_re_entered` *(integration — the only test that touches
inotify; `skipif` on platforms without it)* ·
**`test_real_watcher_reports_a_foreign_write`** *(the control)*

**`agent/tests/test_loop.py`**
`test_sigterm_triggers_orderly_shutdown` · `test_sigint_triggers_orderly_shutdown` ·
`test_signal_handlers_fall_back_where_unsupported` · `test_shutdown_sequence_runs_steps_in_order` ·
`test_hand_back_in_flight_job_precedes_watcher_stop` · `test_write_final_state_seam_is_called` ·
`test_raising_task_does_not_stop_the_agent_process` · `test_dead_watcher_returns_exit_code_3` ·
`test_alive_watcher_does_not_exit` · `test_unwritable_root_logs_error_and_stays_alive` ·
`test_folder_retry_is_bounded_and_backs_off` · `test_no_retry_after_budget_exhausted` ·
`test_missing_subdirectories_are_created` · `test_watcher_starts_lazily_once_folder_appears` ·
`test_no_liveness_task_without_a_folder` · `test_all_three_tasks_are_registered_when_ready` ·
`test_late_appearing_folder_activates_the_rest` ·
`test_folder_watch_starts_the_watcher_after_the_folder_appears` ·
`test_activate_watcher_tasks_is_a_no_op_before_start` · `test_shutdown_grace_expiry_returns_exit_code_4` ·
`test_event_drain_logs_observed_events_without_parsing` *(the #97 boundary: events are logged, no routing)*

**`agent/tests/test_run_agent.py`**
`test_run_agent_module_has_no_print_and_no_todo` · `test_main_delegates_to_agent_run` ·
`test_main_returns_zero_on_clean_shutdown` · `test_main_returns_one_on_config_error` ·
`test_run_agent_help_matches_the_documented_flags` · `test_documented_command_is_accepted_by_the_parser` ·
`test_agents_md_quick_reference_command_runs` · `test_readme_documents_the_worker_agent` ·
`test_help_output_exits_zero` *(real `subprocess`)* · **`test_no_config_flag_is_offered`**

**`agent/tests/test_no_cluster_required.py`**
`test_pyproject_testpaths_include_agent_tests` · **`test_pyproject_coverage_measures_the_agent`** ·
**`test_pyproject_excludes_the_agent_test_package_from_wheels`** ·
`test_every_agent_module_imports_without_cluster_environment` ·
**`test_agent_suite_passes_with_hostile_environment`** ·
`test_agent_imports_no_network_module` · `test_every_third_party_import_is_expected`

---

## 10. Environment baseline — **corrected**

### 10.1 What to use

| | |
|---|---|
| **Interpreter** | **Python 3.12.7** (`requires-python = ">=3.11"`). Verified present on this machine. |
| **Install** | `pip install -e ".[dev]"` — **resolves cleanly on 3.12.7**, `types-python-jose==3.3.4.20240106` and all. |
| **Suite size** | **`pytest` collects 531 tests** with no collection error. |
| **Branch** | `task/93-agent-skeleton` — **and it must be pushed before any QA cycle** (§16). |

### 10.2 Three claims in the issue thread that are WRONG — do not repeat them

1. **"Python 3.13.9"** (spec) and **"3.13.5"** (environment comment) are both wrong for our purposes.
   The interpreter we have is **3.12.7**.
2. **"`pip install -e ".[dev]"` cannot resolve at all"** (environment comment, filed as **#107**) is
   **Python-3.13-specific** and does not hold in general. It was true of a `.venv` that predated the
   pin raises in #75/#78 and still had SQLAlchemy 2.0.0.
   **Both of #107's named defects are, in any case, already fixed on `main`:**
   - `types-python-jose` is `3.3.4.20240106` (`pyproject.toml:44`, fixed by **#73**, `2ee4470`) — the
     version the comment called nonexistent **is** the one now pinned.
   - `pytest-asyncio` is `0.23.8` (`pyproject.toml:35`, fixed by **#69**, `f41282c`), which is what
     fixes the pytest-8.0.0 / pytest-asyncio-0.23.0 `AttributeError: 'Package' object has no attribute
     'obj'` collection failure.
   **#107 should be re-scoped to the 3.13 case. Do not block #93 on it.**
3. **"The suite collects 550 tests."** It collects **531**.

> ⚠ **A trap that will bite you anyway.** The untracked `.venv-main-check/` in this working tree has
> `pytest==8.0.0` with **`pytest-asyncio==0.23.0`** — i.e. the *pre-#69* combination — and it
> **fails collection on `main` today** with exactly the #107 symptom:
> `AttributeError: 'Package' object has no attribute 'obj'`. That is a stale scratch venv, not the
> project. Do not "fix" `pyproject.toml`, and do not file it as a new break — reconcile the venv
> against `pip install -e ".[dev]"` and move on. (For reference, `backend/tests/` statically defines
> **484** `def test_` functions; parametrisers take that to 531 collected items.)

### 10.3 `frontend/` may be absent — and that is fine

`backend/tests/unit/test_frontend_alignment.py:36-44` calls `pytest.skip(..., allow_module_level=True)`
when `frontend/src/lib/settings.ts` is missing. **That is by design** — the comment says so
explicitly, because a silent pass would report "frontend and backend agree" while checking nothing,
"which is the exact failure mode this file exists to prevent". A missing submodule shows up as a
skip, never as a false green. **Do not count it as a failure, and do not try to fix it.**

### 10.4 The gates — **deltas to be re-measured, not absolute numbers**

**Every absolute number below was measured on an interpreter or a tree that no longer exists. None of
them is a gate. The gate is the delta.** The rule for each is given; re-measure on the branch and in
the PR, and report the delta.

| Gate | The rule | Last known value, and where it came from |
|---|---|---|
| `pytest` (whole suite) | **The failure/error set must be identical to `main` measured in the same interpreter.** Any new entry is a regression introduced here. | On a Python-3.11.17 hand-built env: **13 failed / 492 passed / 29 skipped**. On `main` @ `e0d3856` with 3.12.7: **435 passed / 30 skipped / 85 errors** (all 85 integration; the #57 symptoms — `sqlite3.OperationalError: database is locked` and `RuntimeError: Event loop is closed` from an aiosqlite worker thread). **The 3.12.7 numbers are stale: `main` has since moved to `0acb57b`, which *is* the #57 fix for exactly those errors.** **Not re-measured in this grooming** — re-measure before comparing. |
| `pytest agent/tests` | **All green**, zero variance. The single real-inotify test may `skipif` on platforms without it; it must **not** be counted as a failure. | **180 passed, 0 skipped** on Linux, across 5 consecutive runs, reversed order, per-file isolation, and a hostile shell environment. **Not re-measured.** |
| `pytest --cov` | **Before and after numbers recorded, with the interpreter version** (AC-17), **plus the per-file coverage of every `agent/` module.** | See §10.5 — and note **NHD-1**. |
| `ruff check agent/` | clean | clean (2 files) |
| `ruff check .` | `agent/` contributes **0**; the total does not rise | **44 errors** |
| `mypy agent/` | clean | clean (2 files) |
| `mypy .` | `agent/` contributes **0**; the total does not rise | **139 errors in 28 files** (the spec's "138 in 27" was off by one file) |
| `ruff format --check agent/` | **NHD-2 — undecided** | **7 files would be reformatted**; `main` already had 3 repo-wide |

### 10.5 What the coverage numbers mean — and the trap

QA measured, on the same command, both trees (**these are the numbers AC-17 wants, from the lost
commit**):

| `pytest --cov-report=term` | `main` | lost commit `42f5089` |
|---|---|---|
| `pytest --cov` (pyproject `source = ["backend","agent"]`) | **79.38 %** — 2013 stmts, 415 missed → **`fail_under` FAILS** | **84.32 %** — 2627 stmts, 412 missed → `fail_under` **passes** |
| `pytest --cov=backend --cov=agent` | **20.17 %** — 6595 stmts, 5265 missed | **27.03 %** — 7200 stmts, 5254 missed |
| `pytest --cov=backend` | **20.19 %** — 6586 stmts, 5256 missed (reproduces the orchestrator's 20 % baseline exactly) | **20.22 %** — 6586 stmts, 5254 missed |

The number **rises under every command**; it never regresses.

Two things to carry forward:

- **For #72:** `--cov=NAME` and `source=[…]` **do not measure the same thing** (2013 vs 6586
  statements on an identical tree). That is the confusion behind "20 % not 89 %", and this issue does
  not change it either way.
- **For NHD-1:** with `source = ["backend","agent"]`, the blended gate **passes only because the
  agent's well-covered code drags the backend's ~20 % up over 80**. See §15.

---

## 11. The three `pyproject.toml` edits — exact and copy-pasteable

**All three are required.** The decision record upgraded edits 2 and 3 from conditional/recommended
to mandatory. **No other `pyproject.toml` change is in scope** — in particular **do not** add a
`[[tool.mypy-overrides]]` block for `agent` (C15), and **do not** add a new pytest marker (C14).

```toml
[tool.pytest.ini_options]
testpaths = ["backend/tests", "agent/tests"]          # EDIT 1 — otherwise agent/tests is never collected (C6)

[tool.coverage.run]
source = ["backend", "agent"]                        # EDIT 2 — per the decision record
omit = ["backend/tests/*", "backend/alembic/*", "agent/tests/*"]

[tool.setuptools.packages.find]
exclude = ["test*", "docs*", "docker*", "frontend*", "stubs*", "node_modules*", "agent.tests"]   # EDIT 3 (C13)
```

**Edit 1** is on `pyproject.toml:85`. **Edit 2** is on `:98-99`. **Edit 3** is on `:118`.

> ⚠ **Do not describe the result as "the agent is 90 % covered" if it is not measured.** With
> `source = ["backend", "agent"]` the reported total is a **blend**, and the *agent's own* per-file
> number is the meaningful new one. AC-17 requires both.

---

## 12. Exact file changes

| Path | Change |
|---|---|
| `agent/run_agent.py` | Reduced from 26 lines to a ~40-line shim. Keeps `main()` as the `pyproject.toml:122` entry point. `parser.add_argument("--node-id", …)`, `--syncthing-root` (optional, wins over env), `--log-level`. **No `--config` flag** (S6). Calls `build_settings`, `configure_logging`, `asyncio.run(Agent(settings).run())`, returns the int exit code. **No `print`, no TODO, and exactly one logging entry point** (D2). |
| `agent/config.py` | **new.** `AgentSettings(BaseSettings)`, `build_settings(argv, *, environ, toml_path)`, hand-merged precedence, `expanduser` → `normalize_path` root resolution, one-way `os.environ` publication, `NODE_ID` validation + TOML-refusal, level validation, `EXIT_*` constants. **No module-level `settings = ...`.** |
| `agent/logging_config.py` | **new.** `LOG_FORMAT`, `NodeIdFilter`, `configure_logging(level, node_id, *, stream=None)`, record factory. **No `NodeIdFormatter`** (D3). |
| `agent/paths.py` | **new.** `AgentPaths` frozen dataclass (`root`, `jobs_dir`, `nodes_dir`, `state_dir`, `node_file`), `resolve_paths(root, node_id, state_dir=None)` — with `expanduser` (D4). |
| `agent/watcher.py` | **new.** `FolderEvent`, `SelfWriteLedger`, `WatcherDead`, `FolderWatcher`, private `_EventHandler`. |
| `agent/supervisor.py` | **new.** `TaskFailure`, `SupervisorTask`, `TaskSupervisor` (including `spawn`, K10). |
| `agent/loop.py` | **new.** `Agent` with `run`, `_ensure_folder`, `_check_folder`, `_activate_watcher_tasks`, `_drain_events`, `_hand_back_in_flight_job`, `_write_final_state`, `_shutdown_sequence`. |
| `agent/tests/__init__.py` | **new** — matches `backend/tests/__init__.py`; makes `agent.tests` importable by mypy. |
| `agent/tests/conftest.py` | **new.** Fixtures: `agent_settings`, `tmp_paths` (tmp `AgentPaths`), `fake_observer`, `scripted_stop`, **plus an `os.environ` restore fixture** for the deliberate one-way publication (§3.2), without which tests leak `SYNCTHING_ROOT` into each other. |
| `agent/tests/test_*.py` | **new**, 8 files, per §9.3. |
| `pyproject.toml` | The three edits in §11. |
| `README.md:209-217` | Replace the `cd agent && python run_agent.py` block with the installed `run-agent` invocation and the real flag set. State that `nodes/agent.toml` is read **once**, at startup (**no hot reload**) — S6. |
| `AGENTS.md:21` | Fix the command — it omits a `required=True` flag and exits 2 today (C12). |
| `docs/specs/93-agent-skeleton.md` | **This file.** |

**Nothing under `backend/`, `shared/`, `cli/`, `docker/` or `frontend/` is in scope.** The one
`shared/` defect this issue encounters (D6, C16) is **worked around in `agent/`, not fixed in
`shared/`** — that fix belongs with #54/#98.

---

## 13. Out of scope — deferred, deliberately

| Deferred | Why, and where it goes |
|---|---|
| Any `backend/` or `frontend/` edit | Orchestrator constraint on this cycle. Two consequences are handled here rather than dropped: logging (C7) and the `.tmp`/dotfile filter duplication (R5). |
| Issue #68 (logging config) | Stays **unlanded**. The agent defines its own `configure_logging()`; it does not wait on #68 and does not require #68 to change. |
| Issue #58 (`.env` discovery was CWD-relative) | **Landed** (`e0d3856`). The agent resolves **zero** files relative to CWD regardless, and `test_build_settings_does_not_read_process_environment` fails if it ever does. |
| Issue #72 (coverage reports 20 % not 89 %) | Known confound, **not settled by this issue**. `--cov=NAME` and `source=[…]` measure different things (§10.5). The before/after numbers are a mandatory PR artifact (AC-17). |
| Issue #54 (backend ruff/mypy red on `main`) | Stays unlanded. "Does not regress" means exactly the §10.4 deltas. |
| Issue #107 ("dev install unresolvable") | **Re-scope to the 3.13 case** — both named defects are already fixed (§10.2). |
| Writing `nodes/<node_id>.yaml` | #95. The agent gets the `AgentPaths.node_file` field and nothing more. |
| Heartbeat / sampling | #96, #102. |
| Parsing `state.yaml`, claiming a job | #97. The watcher **observes and enqueues**; it does not parse or route. |
| Launching a process | #98. **This is why "hand back the in-flight job" is a no-op seam** (Q4). |
| Cancellation | #99, blocked on #106. |
| Terminal state write | #100. The "final state write" shutdown step is a logged no-op. |
| Orphan reconciliation, durable claim recovery | #103. The shutdown ordering and the two seams exist so #103 plugs in rather than rewrites. |
| `agent/README.md`, systemd unit, install docs | #104. The exit-code table (§5.8) is written here so #104 inherits it. |
| Promoting `_is_relevant_file` into `shared/file_ops/` | Would fix a real duplication this issue creates, but it is a `shared/`+`backend/` refactor and belongs with #54. Recorded as **R5**, not silently dropped. `test_agent_filter_matches_the_backend_filter` is the tripwire in the meantime. |
| **Per-node override in `agent.toml`** (`[agent.<node_id>]` sections) | **Explicitly deferred by the decision record.** A node overriding one setting means editing a file every other node also reads. Single file, flat keys, this issue. Recorded as a follow-up, not half-built. |
| **Hot reload of `agent.toml`** | **Explicitly decided: none.** Read once, at startup. |
| Renaming `run-agent` → `shc-agent`, or fixing the `sci-run` vs `run-agent` inconsistency | **Explicitly deferred by the decision record** (Q3). Cosmetic. File a follow-up if wanted; do not change it silently. |
| Fixing `normalize_path`'s `~/…` `TypeError` | D6 / C16. It is `shared/`, it belongs with #54/#98, and #98 **will** hit it. |
| Job poll interval tuning / metrics sampling rate | Not reachable without #102 measurements. |

---

## 14. Risks and consequences

- **R1 — pydantic-settings 2.0.0 cannot express the documented precedence.** Verified: no
  `TomlConfigSettingsSource`, no `toml_file` key, and `init_settings` outranks `env_settings`. The
  obvious `AgentSettings(**toml_dict)` makes the **file beat the environment**, silently, with no
  warning. **This is the single most likely silent defect in this issue, and TOML is chosen, so it is
  live.** Mitigation: `build_settings` is the only construction site, `settings_customise_sources`
  returns `(init_settings,)`, and **`test_env_overrides_toml_file` is mandatory and must be
  mutation-re-run** (§9.1/VM-1).
- **R2 — the `SYNCTHING_ROOT` coupling is a real, stateful coupling.** `path_utils.py:58` reads
  `os.environ` directly. `AgentPaths` is computed from the **settings value**, never from
  `get_jobs_directory()`, so the agent's path model stays testable and CWD-independent. The one-way
  `os.environ` publication is deliberate and documented — and anything else reading it (including a
  job subprocess under #98, where "parent environment + `spec.env`" is the stated policy) will see it
  too. **A test fixture must restore `os.environ`, or this leaks across the suite.**
- **R3 — the coverage number is a blend and the gate is coupled to the agent.** See **NHD-1**.
- **R4 — `NODE_ID` validation closes traversal, not impersonation.** Per #92 the agent has no
  identity and the only control is Syncthing folder ACLs (#104). **A well-formed `node_id` is easier
  to impersonate than a malformed one.** State this plainly wherever the validator is introduced so
  no reviewer reads it as a security control.
- **R5 — a real duplication is created by this issue.** The `.yaml`-only / dotfile-and-`.tmp` filter
  will exist in both `backend/services/syncthing_service.py:53-62` and `agent/watcher.py`. If one
  gains a case the other does not, **the agent observes a different file set than the backend.**
  Mitigation: `test_agent_filter_matches_the_backend_filter` compares the two implementations against
  each other on every change. The proper fix — promoting it into `shared/file_ops/` — is deferred to
  #54.
- **R6 — `logging.basicConfig` is a no-op under an existing handler.** True under `pytest`, and true
  in any process that installed a handler first — **which is exactly how D2 happened.**
  `configure_logging` sets `root.setLevel` explicitly, installs its own handler, and replaces rather
  than stacks its filter; `test_configure_logging_is_idempotent` and `test_rendered_line_contains_the_node_id`
  pin it.
- **R7 — `loop.add_signal_handler` is main-thread and POSIX only.** It raises `NotImplementedError` on
  Windows and off the main thread, so `Agent.run()` **must** fall back to the injectable `stop` event
  with one warning, or the suite cannot run there.
- **R8 — liveness is a local check and must not be oversold.** `observer.is_alive()` catches a dead
  observer thread, a stopped emitter and a lost watch. It **cannot** catch a Syncthing *peer* dropping
  while the folder stays present and writable. That gap is real, is out of scope, and belongs to
  #104's documentation rather than to a claim in this issue.
- **R9 — `SHUTDOWN_GRACE_S` defaults to 30 s and no test can afford that.** Every shutdown test sets
  it to 0.1–0.5 s, which is only possible because `build_settings` takes `environ` rather than
  reading it. This is the injection design paying for itself, not a test burden.
- **R10 — the exit-code contract is new and needs an owner.** `0/1/2/3/4` is defined here so #104
  inherits it; if it is not carried forward, `Restart=on-failure` will mis-handle code 3.
- **R11 — the agent's own state directory is a new replicated write surface.**
  `AGENT_STATE_DIR` defaults to `<root>/.agent/<node_id>`, **inside** the Syncthing folder, so #103
  gets the durable local record it needs — and every write there fans out to every device, per #92's
  fan-out cost. **It is empty in #93** (nothing is written to it yet), so the cost is deferred, not
  incurred; whoever fills it must inherit the frequency budget #102 has to measure. It is `.`-prefixed
  and not `.yaml`, so neither watcher sees it.
- **R12 — `os.access` is not a write probe, and two failure shapes are missed.**
  QA measured 7 constructible permission shapes with **0 disagreements**. Two more could not be
  constructed without privileges (a read-only mount needs root; `chattr +i` needs
  `CAP_LINUX_IMMUTABLE`). `access()` returns `EROFS` on a read-only mount on modern Linux, but
  **ENOSPC and quota exhaustion would be missed.** Neither is live in #93 — the agent writes nothing
  into `jobs/` or `nodes/` yet. **The first real write, #95, is the true test.**
- **R13 — the `nodes/agent.toml` placement puts a control-plane file inside a watched folder.**
  This is the human's decision (§3.1) and it is the right one for a cluster that communicates by
  replicating a folder, but it has a cost: the file **replicates to every node**, so a bad edit
  reaches the whole fleet. Mitigations are already decided — **unknown keys are rejected** and
  **malformed files name their absolute path**, so all six nodes fail loudly rather than six nodes
  silently ignoring a typo. Per-node override is deferred (§13).

---

## 15. RESOLVED DECISIONS (were NEEDS-HUMAN-DECISION)

The three items below were put to the human, who answered all three. **These are decisions, not
suggestions.** Everything else that looks open has been settled — see the "deliberately deferred"
table in §13, which exists so that a deferral is not mistaken for a gap.

| # | Question | Decision |
|---|---|---|
| NHD-1 | `fail_under` once `source` is widened | **B — per-package gates** |
| NHD-2 | Is `ruff format` a gate? | **A — yes, for the new files only** |
| NHD-3 | `SYNCTHING_ROOT` as a key in `agent.toml` | **A — dedicated error message** |

### NHD-1 — RESOLVED: B, per-package coverage gates

The human chose **B**. Consequences the SWE must implement:

- The blended `fail_under = 80` in `[tool.coverage.report]` **must not be relied on as the gate**. It
  passes only because the agent's ~100 %-covered code drags the backend's ~20 % over the line, and
  **removing the agent's tests would turn it red**. That is precisely the coupling B exists to remove.
- The **agent's** gate is `--cov=agent --cov-fail-under=80`, asserted as a real per-file number in
  AC-17. This is the meaningful new gate and it is the one this issue introduces.
- The **backend's** honest number is `--cov=backend` (~20 %, an artefact of **#72**'s unresolved
  `--cov=NAME` vs `source=[…]` confusion — recorded in §10.5). The backend's own gate is **#72's job**;
  do not invent one here, and do not lower or widen the blended `fail_under` in this issue to make the
  backend look covered.
- The widened `source = ["backend", "agent"]` still stands — the decision record made it mandatory and
  nothing here reverses it. What changes is which number is treated as a gate.

### NHD-2 — RESOLVED: A, `ruff format` is a gate for the new files only

`AGENTS.md` documents `ruff check . && ruff format . && mypy .` as the lint command, so leaving new
code that it rewrites is a permanent, self-inflicted irritation. Consequences:

- **All new `agent/` files must be `ruff format`-clean**: `ruff format --check agent/` → *All checks
  formatted!* This replaces the 7-unformatted-files state QA measured on the lost implementation.
- **The 3 pre-existing repo-wide `ruff format` offenders are NOT touched.** Fixing them would edit
  `backend/` and `shared/`, which this issue may not change. The repo-wide number therefore improves
  from 3 to 0 offenders *among the files this issue adds*, and the overall count is unchanged or
  improved — never worse.
- `AGENTS.md` should be corrected if it overstates the documented command; that is a one-line docs
  change if the wording is wrong, but do not change the lint command itself.

### NHD-3 — RESOLVED: A, a dedicated error message

**Reject `SYNCTHING_ROOT` in `agent.toml`, with its own message** rather than folding it into the
generic unknown-key rejection. The rationale is that a shared file replicates to every node in the
fleet, so an operator who puts it there gets the *same* message on every node — it has to be
self-explanatory. Consequences:

- A `SYNCTHING_ROOT` key in the file raises a configuration error whose message says, in substance,
  **"`SYNCTHING_ROOT` cannot be set in this file — it is what locates this file."** Alongside it,
  state where to set it (the `SYNCTHING_ROOT` environment variable, or `--syncthing-root`).
- This joins the `NODE_ID`-in-TOML rejection as a **second dedicated error**, so the shape is
  established twice and neither is the silent-ignore class.
- Named test required: `test_syncthing_root_in_toml_is_rejected_with_a_dedicated_message`, asserting
  the message names the key **and** explains that it is what locates the file.
- Option C (accept silently as a no-op) is **rejected outright**: it is the same silent-ignore shape as
  **D1**, and this design exists to eliminate that shape.

---

### The original framing, retained for provenance

The three questions as they were originally put, with their option tables, are preserved below so the
reasoning behind each decision stays auditable. **The decisions above supersede them.**

### NHD-1 (original question) — Does `fail_under = 80` stay a whole-suite number once `source` is widened?

**Why it is not already answered.** The decision record made `source = ["backend", "agent"]`
mandatory, and justified it as: *"The backend total will be a blend against a baseline of 20 %
(#72), so it cannot go red spuriously; the `agent` number is the meaningful new one. The `fail_under =
80` gate now genuinely applies to new code."* That reasoning was written **before any numbers
existed.** QA then measured them, and they say something the decision record did not anticipate:

> With `source = ["backend", "agent"]`, `main` alone measures **79.38 %** — *below* the gate — and the
> branch measures **84.32 %**, *above* it. **The gate passes only because the agent's ~100 %-covered
> code drags the backend's ~20 % over the line.**

So the new gate is not "the backend is covered"; it is "the backend plus the agent, blended, is
covered", and **removing the agent's tests would turn it red.** That is a defensible outcome, but it
is not what the decision record described, and nobody has chosen it.

| Option | What it means |
|---|---|
| **A. Leave it** — one blended `fail_under = 80`, as the decision record implies | Simplest, no extra `pyproject.toml` surface. But the gate silently couples backend health to agent coverage, and it hides the fact that the backend is at ~20 % under `--cov=backend`. |
| **B. Scope `fail_under` per package** — run `pytest --cov` and a second `pytest --cov=agent --cov-fail-under=80` | Each package gets an honest gate. Costs one extra command in CI and one more number in the PR. |
| **C. Keep the blend but lower it**, or move `fail_under` to `#72` | Recognises that the blend is an artefact of #72's unresolved `--cov=NAME` vs `source=[…]` confusion (§10.5). Moves the problem rather than solving it. |
| **D. Drop `fail_under` from the blend and make the per-file `agent/` number the AC-17 gate** | The decision record's stated intent taken literally. Loses any whole-suite coverage regression check. |

**PM recommendation: B.** It is the only option in which both the backend and the agent have an
honest gate, and it is the only one that does not make the project's coverage target a function of
which package happened to be well tested. **But this is a project-wide gate policy, not an
#93-shaped question, and the human should pick it.**

### NHD-2 (original question) — Is `ruff format` part of this issue's gate?

`AGENTS.md`'s documented lint command is `ruff check . && ruff format . && mypy .` — i.e. `ruff format`
**is** part of the documented workflow. But neither the spec's gate table nor the decision record's
restated gate table includes it, and QA measured (not gated) that `ruff format --check agent/` would
rewrite **7 of the new files**. `main` already had 3 repo-wide, so this is not a new class of problem.

| Option | What it means |
|---|---|
| **A. Yes — `ruff format agent/` runs, and the branch must be format-clean for the new files** | The documented lint command stops being a no-op on new code. Costs one line of CI. |
| **B. No — leave it out, as every existing gate table has** | Consistent with the status quo. The 7 files stay unformatted and the documented command rewrites them. |
| **C. Yes, and fix the 3 pre-existing ones too** | Out of scope — it would touch `backend/` and `shared/`, which this issue may not edit. |

**PM recommendation: A**, scoped to the new files only. It is cheap, and leaving new code that the
documented lint command rewrites is a small, permanent irritation. **The human should confirm whether
`ruff format` is a gate at all, or whether `AGENTS.md` overstates it.**

### NHD-3 (original question) — What happens if `SYNCTHING_ROOT` appears as a key in `agent.toml`?

**Why it is not already answered.** The decision record mandates that **unknown keys are rejected**,
and the settings table lists `SYNCTHING_ROOT` as a field with env + CLI sources. But the root is what
**locates** the file, so `SYNCTHING_ROOT` inside the file can never take effect — QA verified this is
genuinely unreachable (`build_settings` raises before the file is read when no root is otherwise
available, and env/CLI always outrank it). So the key is either a **rejected unknown key** or a
**documented accepted no-op**, and the sources do not say which.

| Option | What it means |
|---|---|
| **A. Reject it as an unknown key** — consistent, but the error message would be confusing ("unknown key `SYNCTHING_ROOT`" for a name that is obviously meaningful) | One rule, one code path. |
| **B. Accept it and warn that it is ignored** | More forgiving; but a warning that fires on a shared file reaches every node. |
| **C. Accept it silently as a documented no-op** | **Rejected here**: it is the silent-ignore class this whole design is trying to eliminate (the same shape as D1). |

**PM recommendation: A**, or a dedicated error message along the lines of *"`SYNCTHING_ROOT` cannot be
set in this file — it is what locates this file."* **The human should pick; it is a two-line
difference, but it is user-facing text that ships.**

---

## 16. Definition of Done — issue #93

`PROCESS.md#definition-of-done` plus the parts specific to this issue. **All of it.**

### Code

- [ ] Every acceptance criterion in §7 is implemented, and every one of them has at least one **named
      test** from §9.3 (or a documented reason why not).
- [ ] The three `pyproject.toml` edits in §11 are applied, verbatim, and nothing else in
      `pyproject.toml` changed.
- [ ] **No file outside §12 is modified.** In particular nothing under `backend/`, `shared/`, `cli/`,
      `docker/`, or `frontend/` — including no change to the `frontend/` gitlink.

### The carry-forward defects — the reason this spec exists

- [ ] **D1** is unreachable: the root is resolved *before* the config path is derived, and
      `test_config_file_is_found_for_a_tilde_root` + `test_config_file_is_found_for_a_relative_root`
      pass. `README.md`, the docstrings, and the decision record's "same command, same answer from any
      directory" are all now **true**.
- [ ] **D2** is unreachable: `run_agent.py` has **exactly one** logging entry point,
      `test_rendered_line_contains_the_node_id` passes against a real captured handler, and
      `test_configure_logging_is_idempotent` passes.
- [ ] **D3**: no dead class is exported. Either `NodeIdFormatter` is wired in or it does not exist.
- [ ] **D4**: `test_resolve_paths_expands_user` passes.
- [ ] **D5**: `ruff format --check agent/` is clean (or NHD-2 says otherwise, in writing).
- [ ] **D6** is *recorded*, not fixed, and the agent does not depend on it.

### The mutation re-run — §9.1, all four

- [ ] **VM-1** both inverted-precedence shapes go red (TOML-merged-last, and the faithful
      pydantic-settings `(init_settings, env_settings)` inversion). Report the failure output.
- [ ] **VM-2** renaming the config constant to `agent.yaml` goes red **against the backend's live
      `_is_relevant_file`**, with the literal-name and suffix assertions removed first.
- [ ] **VM-3** a `NODE_ID` key in the shared TOML exits **1** with the explanatory message.
- [ ] **VM-4** exactly one `os.environ` key is added, after resolution, and is never read back in.

### Gates (§10.4 — deltas, re-measured, reported in the PR)

- [ ] `pytest` — the failure/error set is **identical** to `main` in the same interpreter.
- [ ] `pytest agent/tests` — all green, **zero variance** across ≥5 consecutive runs, reversed file
      order, per-file isolation, and a hostile shell environment.
- [ ] `pytest --cov` — **before and after** numbers with the interpreter version (AC-17), **plus the
      per-file coverage of every `agent/` module**.
- [ ] `ruff check agent/` clean · `ruff check .` unchanged, `agent/` contributing **0** ·
      `mypy agent/` clean · `mypy .` unchanged, `agent/` contributing **0**.

### Process — the part that caused the last loss

- [ ] Work is on branch **`task/93-agent-skeleton`**, targeting `main`.
- [ ] **The branch is pushed.** A local branch that was never pushed is indistinguishable from work
      that was never done — that is exactly how commit `42f5089` disappeared. Push before requesting
      QA, not after.
- [ ] A **PR is opened and references #93** before the QA cycle begins.
- [ ] `README.md` and `AGENTS.md` are updated in the same PR (AC-18), and the documented commands have
      actually been **run from a directory other than the repo root**.
- [ ] The PR body carries the AC-17 numbers, the gate deltas, and the VM-1…VM-4 mutation results.
- [ ] Squash-merged to `main` after QA and PM acceptance; the issue moves `in-progress` → `needs-review`
      → `done`.

---

*Consolidated by PM from issue #93's body and five comments (the spec, the environment baseline, the
decision record, the QA verdict and the loss notice). Where those sources disagreed, §2 records the
conflict and its resolution. Where they left something open, §15 says so rather than guessing.*