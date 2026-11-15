# Agent First-Run Default Config — Specification

**Issue:** #172  
**Status:** `groomed`  
**Author:** PM (agent-team workflow)  
**Date:** 2026-11-15

---

## 1. Problem Statement

On a fresh machine / first run, the worker agent (and the integrated app entry point) expects configuration to come from environment variables and/or a `.env` file at the repository root. When that file is absent and required variables are not set, startup fails outright with no guidance about what to do next. There is no graceful path that:

1. Detects the missing `.env` / required configuration
2. Prompts the operator to create one (with a pointer to `.env.example`)
3. If the operator declines or cannot (non-interactive context), falls back to a **safe, documented set of defaults for optional settings** while **still failing fast for required settings with no safe default** (`SYNCTHING_ROOT`, `NODE_ID`)

This makes first-run onboarding harder than necessary, especially for operators running the prebuilt binary.

---

## 2. User Stories

| ID | Story |
|----|-------|
| US-1 | As a new operator, when I run `run-agent` for the first time without a `.env`, I am prompted to create one from `.env.example` so I can configure the required values. |
| US-2 | As an operator in a non-interactive environment (CI, container, systemd), when I run `run-agent` without a `.env`, I get a clear error message telling me exactly which required variables are missing and where to set them, without blocking on a prompt. |
| US-3 | As an operator, when optional settings fall back to defaults, I see a loud `WARNING` log line naming every defaulted value and where to override it, so I know what is implicit vs. explicit. |
| US-4 | As an operator, if I explicitly set a variable via environment or CLI flag, it is never silently overridden by a default or a `.env` value. |
| US-5 | As an operator, the first-run behavior is documented in both `agent/README.md` and the root `README.md` so I can find it regardless of where I start reading. |

---

## 3. Acceptance Criteria

### 3.1 Detection & Prompt (Interactive)

- [ ] **AC-1.1** On startup, the agent detects whether a `.env` file exists at the **repository root** (repo-root-anchored, not CWD-dependent — per issue #34/#58).
- [ ] **AC-1.2** The agent detects whether the required variables `NODE_ID` and `SYNCTHING_ROOT` are available from any source (CLI flag > environment variable > `.env` > default).
- [ ] **AC-1.3** If running in an **interactive TTY** (`sys.stdin.isatty() == True`) and either `.env` is missing or required variables are unset, the agent prints a **single prompt** to stderr:
  ```
  No .env file found at <repo-root>/.env and required configuration is missing.
  Create one now from .env.example? [y/N]:
  ```
- [ ] **AC-1.4** If the operator confirms (`y`/`Y`), the agent copies `.env.example` to `.env` at the repository root, prints the path, and **re-checks** configuration.
- [ ] **AC-1.5** If the operator declines (`n`/`N`/Enter) or input times out (5 seconds), the agent proceeds to the default/fallback logic (AC-2).

### 3.2 Non-Interactive / Service / Container

- [ ] **AC-2.1** If **not** in an interactive TTY (`sys.stdin.isatty() == False`), the agent **skips the prompt entirely** and logs a single `WARNING`:
  ```
  Running in non-interactive mode; skipping .env creation prompt. Set NODE_ID and SYNCTHING_ROOT via environment or CLI flags.
  ```
- [ ] **AC-2.2** The agent then proceeds to validation (AC-3).

### 3.3 Validation & Defaults

- [ ] **AC-3.1** **Required variables with no safe default (`NODE_ID`, `SYNCTHING_ROOT`)** — if still unset after the prompt/re-check — **fail fast** with the existing actionable error messages (no change to current behavior).
- [ ] **AC-3.2** **Optional variables** (`LOG_LEVEL`, `SHELL`, `SHUTDOWN_GRACE_S`, `FOLDER_WATCH_INTERVAL_S`, `FOLDER_RETRY_MAX_S`, `WATCHER_LIVENESS_INTERVAL_S`, `HEARTBEAT_INTERVAL_S`, `HEARTBEAT_JITTER_S`, `AGENT_STATE_DIR`) — if unset — **use their documented defaults** and emit a **single consolidated `WARNING`** naming every defaulted value:
  ```
  WARNING: Using default configuration for the following settings (override via CLI, env, or nodes/agent.toml):
    LOG_LEVEL=INFO (default; override with --log-level or AGENT_LOG_LEVEL)
    SHELL=auto (default; override with AGENT_SHELL)
    SHUTDOWN_GRACE_S=30.0 (default; override with AGENT_SHUTDOWN_GRACE_S)
    FOLDER_WATCH_INTERVAL_S=30.0 (default; override with AGENT_FOLDER_WATCH_INTERVAL_S)
    FOLDER_RETRY_MAX_S=60.0 (default; override with AGENT_FOLDER_RETRY_MAX_S)
    WATCHER_LIVENESS_INTERVAL_S=60.0 (default; override with AGENT_WATCHER_LIVENESS_INTERVAL_S)
    HEARTBEAT_INTERVAL_S=22.0 (default; override with AGENT_HEARTBEAT_INTERVAL_S)
    HEARTBEAT_JITTER_S=2.0 (default; override with AGENT_HEARTBEAT_JITTER_S)
    AGENT_STATE_DIR=<SYNCTHING_ROOT>/.agent/<NODE_ID> (default; override with AGENT_STATE_DIR)
  ```
- [ ] **AC-3.3** The warning is emitted **once per process start**, after logging is configured, using the agent's logger (not `print`/`stderr`).
- [ ] **AC-3.4** Explicit values from CLI flags, environment variables, or `nodes/agent.toml` **never trigger the warning** for that setting.
- [ ] **AC-3.5** The warning includes the **precedence order** (CLI > env > TOML > default) so operators know how to override.

### 3.4 Frozen App Entry Point (`scripts/run_app_entry.py`)

- [ ] **AC-4.1** The same first-run detection and prompt logic applies to the frozen app entry point where relevant (i.e., when backend settings require `SYNCTHING_ROOT` or other agent-relevant variables).
- [ ] **AC-4.2** The frozen app already has `_apply_frozen_defaults()` for `SHC_STATE_DIR`, `SHC_DATA_DIR`, etc. — the new logic complements, not replaces, this.

### 3.5 Documentation

- [ ] **AC-5.1** `agent/README.md` documents the first-run flow, the prompt, the defaulted optional settings, and the required settings that still fail fast.
- [ ] **AC-5.2** Root `README.md` §"Worker Agent" and §"Environment Variables" are updated with the first-run behavior.
- [ ] **AC-5.3** The `--help` output for `run-agent` mentions the first-run behavior (one line).

### 3.6 Tests

- [ ] **AC-6.1** Unit test: detection of missing `.env` at repo root (repo-root-anchored).
- [ ] **AC-6.2** Unit test: interactive prompt is shown when TTY, skipped when not TTY.
- [ ] **AC-6.3** Unit test: prompt creates `.env` from `.env.example` and re-checks.
- [ ] **AC-6.4** Unit test: required vars (`NODE_ID`, `SYNCTHING_ROOT`) still fail fast with existing messages when no safe default.
- [ ] **AC-6.5** Unit test: optional vars fall back to defaults and emit consolidated WARNING.
- [ ] **AC-6.6** Unit test: explicit CLI/env/TOML values suppress the warning for that setting.
- [ ] **AC-6.7** Integration test: end-to-end first run in non-interactive mode (CI-like).

---

## 4. Technical Approach / Design Decisions

### 4.1 Where the Logic Lives

The first-run logic is a **startup concern**, not a configuration-layer concern. It belongs in the **entry points**:

| Entry Point | File | Function |
|-------------|------|----------|
| Worker agent (installed script) | `agent/run_agent.py` | `main()` |
| Frozen app binary | `scripts/run_app_entry.py` | `main()` |

The `build_settings()` function in `agent/config.py` remains **pure** — it only merges layers and validates. It does not prompt, detect `.env`, or log warnings about defaults. This keeps it testable and reusable (e.g., by tests that inject `environ`).

### 4.2 Repo-Root-Anchored `.env` Detection

Per issue #34/#58, all paths must be anchored to the repository root, not the CWD. The repository root is derived from the entry point's `__file__`:

```python
# In agent/run_agent.py
REPO_ROOT = Path(__file__).resolve().parents[1]  # agent/ -> repo root
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
```

This mirrors `docker/init_env.py` and `backend/core/config.py` (`_REPO_ROOT = Path(__file__).resolve().parents[2]`).

### 4.3 Interactive Detection

Use `sys.stdin.isatty()` — the standard way to detect an interactive terminal. This returns `False` for:
- Piped input (`echo y | run-agent`)
- systemd services
- Docker containers without `-it`
- CI runners (GitHub Actions, GitLab CI, etc.)

### 4.4 Prompt Implementation

- Single prompt, printed to `sys.stderr` (before logging is configured).
- 5-second timeout using `selectors` / `select.select` on Unix, `msvcrt` on Windows, or a simple `input()` with a thread timeout.
- On `y`/`Y`: copy `.env.example` → `.env` (using `shutil.copy2`), print success, re-run detection.
- On anything else: proceed to validation.

### 4.5 Default Warning Emission

The warning is emitted **after** `configure_logging()` so it uses the agent's structured logger with `[node=...]` context. This requires:

1. In `run_agent.main()`:
   - Run first-run detection + prompt **before** `build_settings()`
   - Call `build_settings()` (may raise `ConfigError` for required vars)
   - Call `configure_logging()`
   - **Then** emit the default warning for any optional settings that fell back to defaults

2. To know which settings were defaulted vs. explicit, `build_settings()` needs to return this information. Options:
   - **Option A:** Return a tuple `(settings, defaulted_fields: set[str])` from `build_settings()`
   - **Option B:** Add a `defaulted_fields` attribute to `AgentSettings` model
   - **Option C:** Re-derive in `main()` by checking what was in the merged dict vs. field defaults

**Decision: Option A** — minimal change, explicit, testable. Change `build_settings()` signature to:
```python
def build_settings(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    toml_path: Path | None = None,
) -> tuple[AgentSettings, set[str]]:  # returns (settings, defaulted_field_names)
```

The `defaulted_field_names` contains the **field names** (e.g., `"LOG_LEVEL"`, `"SHELL"`) that ended up using their model default because no layer provided a value.

### 4.6 Required Variables — No Safe Default

`NODE_ID` and `SYNCTHING_ROOT` have **no defaults in the model** (they are required fields). `build_settings()` already raises `ConfigError` with actionable messages if they are missing. This behavior **does not change** — the first-run flow simply ensures the operator has had a chance to create `.env` before this validation runs.

### 4.7 Frozen App Entry Point

`scripts/run_app_entry.py` already has `_apply_frozen_defaults()` which sets defaults for `SHC_STATE_DIR`, `SHC_DATA_DIR`, `SERVE_FRONTEND`, `FRONTEND_DIR`. The first-run logic for the frozen app should:

1. Run the same `.env` detection + prompt (repo-root-anchored from `__file__`)
2. Then call the existing `_apply_frozen_defaults()`
3. The backend `Settings` model handles its own defaults (including auto-generating `SECRET_KEY`, `SHARED_TOKEN`, etc. with WARNING logs)

The frozen app does **not** need the agent's optional-variable warning because the backend already logs its own generated secrets.

### 4.8 Precedence Reminder in Warning

The warning message includes the precedence order so operators know how to override:
```
Precedence: --flag > $ENV_VAR > nodes/agent.toml > default
```

---

## 5. Edge Cases & Safety Considerations

| Edge Case | Handling |
|-----------|----------|
| `.env.example` missing | Fail fast with clear error: "`.env.example not found at <path>; run from a complete checkout.`" (matches `docker/init_env.py`) |
| `.env` exists but is empty/malformed | Treat as "present but incomplete" — re-check required vars, prompt only if required vars still missing |
| Operator presses Ctrl+C during prompt | Exit cleanly with code 130 (SIGINT) |
| Input times out (5s) | Treat as decline, proceed to validation |
| `SYNCTHING_ROOT` points to non-existent path | Current behavior: `resolve_root()` expands `~`/relative but does not verify existence. Agent fails later when folder watcher starts. No change. |
| Multiple agents on same machine (different `NODE_ID`) | Each agent run is independent; `.env` is shared but `NODE_ID` comes from CLI/env. No conflict. |
| `nodes/agent.toml` sets a value that is also defaulted | TOML layer wins over default → no warning for that field |
| Running from a different working directory | Repo-root-anchored paths ensure `.env` is always found at the correct location |
| Frozen binary (PyInstaller) | `sys._MEIPASS` handling in `run_app_entry.py`; `REPO_ROOT` derivation works because `__file__` is the unpacked location |

---

## 6. Test Scenarios

### 6.1 Unit Tests (`agent/tests/test_first_run.py` — new file)

| Test | Description |
|------|-------------|
| `test_detects_missing_env_at_repo_root` | `ENV_FILE.exists() == False` triggers first-run flow |
| `test_detects_existing_env_at_repo_root` | `ENV_FILE.exists() == True` skips prompt |
| `test_prompt_shown_only_in_tty` | Mock `sys.stdin.isatty()` → `True`/`False` |
| `test_prompt_creates_env_from_example` | Simulate `y` input, verify copy and re-check |
| `test_prompt_declined_proceeds_to_validation` | Simulate `n` input, verify `ConfigError` for missing `NODE_ID` |
| `test_non_interactive_skips_prompt_logs_warning` | `isatty=False`, verify WARNING logged, then `ConfigError` |
| `test_required_vars_fail_fast_with_existing_messages` | `NODE_ID`/`SYNCTHING_ROOT` missing → `ConfigError` unchanged |
| `test_optional_vars_default_and_warn` | No optional vars set → `defaulted_fields` contains all optional field names |
| `test_explicit_cli_suppresses_warning` | `--log-level DEBUG` → `LOG_LEVEL` not in `defaulted_fields` |
| `test_explicit_env_suppresses_warning` | `AGENT_LOG_LEVEL=DEBUG` in `environ` → `LOG_LEVEL` not in `defaulted_fields` |
| `test_toml_suppresses_warning` | `nodes/agent.toml` sets `LOG_LEVEL=WARNING` → `LOG_LEVEL` not in `defaulted_fields` |
| `test_warning_emitted_once_after_logging_configured` | Integration-style: run `main()`, capture log output |

### 6.2 Integration Tests (`agent/tests/test_run_agent.py` — extend)

| Test | Description |
|------|-------------|
| `test_first_run_non_interactive_missing_required` | Subprocess `run-agent` without TTY, no env, expect exit 1 + error message |
| `test_first_run_interactive_auto_decline` | Subprocess with `echo n | run-agent`, expect exit 1 + error message |
| `test_first_run_with_env_file` | Create `.env` with required vars, run agent, expect success |

---

## 7. Implementation Plan (for SWE)

### Phase 1: Core Logic in `agent/config.py`
1. Modify `build_settings()` to return `(AgentSettings, set[str])` where the set contains field names that used model defaults.
2. Ensure the set only includes **optional** fields (not `NODE_ID`, `SYNCTHING_ROOT` which have no model default).

### Phase 2: Entry Point Logic in `agent/run_agent.py`
1. Add `REPO_ROOT`, `ENV_FILE`, `ENV_EXAMPLE` constants (repo-root-anchored).
2. Add `run_first_run_flow()` function:
   - Detect missing `.env` / missing required vars
   - Check `sys.stdin.isatty()`
   - Prompt if interactive, log warning if not
   - On `y`: copy `.env.example` → `.env`
   - Return whether to proceed
3. In `main()`:
   - Call `run_first_run_flow()` **before** `build_settings()`
   - Call `build_settings()` → get `(settings, defaulted_fields)`
   - Call `configure_logging()`
   - If `defaulted_fields`: emit consolidated `logger.warning()`

### Phase 3: Frozen App Entry Point in `scripts/run_app_entry.py`
1. Add same repo-root-anchored `REPO_ROOT`, `ENV_FILE`, `ENV_EXAMPLE`.
2. Add first-run detection + prompt before `_apply_frozen_defaults()`.
3. No default warning needed (backend handles its own).

### Phase 4: Documentation
1. Update `agent/README.md` — add "First Run" section.
2. Update root `README.md` — update "Worker Agent" and "Environment Variables" sections.
3. Add one line to `build_parser()` help text.

### Phase 5: Tests
1. Create `agent/tests/test_first_run.py` with unit tests.
2. Extend `agent/tests/test_run_agent.py` with integration tests.
3. Run full test suite (`pytest agent/tests/ --cov=agent`).

---

## 8. Related Files & References

| File | Role |
|------|------|
| `agent/config.py` | `build_settings()`, `AgentSettings`, config layers |
| `agent/run_agent.py` | Worker agent entry point (`main()`) |
| `scripts/run_app_entry.py` | Frozen app entry point (`main()`, `_apply_frozen_defaults()`) |
| `agent/paths.py` | `resolve_root()` — repo-root-anchored path resolution |
| `docker/init_env.py` | Reference: `.env` seeding, repo-root anchoring, TTY handling |
| `backend/core/config.py` | Reference: `Settings`, auto-generated secrets with WARNING logs |
| `agent/README.md` | Documentation target |
| `README.md` | Documentation target |
| `.env.example` | Template for `.env` creation |
| `agent/tests/test_config.py` | Test patterns for `build_settings()` |
| `agent/tests/test_run_agent.py` | Test patterns for entry point |

---

## 9. Out of Scope

- Auto-generating `NODE_ID` (hostname-based or random) — **rejected** due to impersonation risk (issue #93 Q1 Edge 2, NHD-3).
- Auto-discovering `SYNCTHING_ROOT` — **rejected** because a wrong path silently produces an empty cluster (worse than failing fast).
- Hot-reload of `.env` or `nodes/agent.toml` — explicitly not supported (README: "read once at startup").
- Backend server first-run — backend already auto-generates `SECRET_KEY`, `SHARED_TOKEN`, `AUTH_COOKIE_SECRET` with WARNING logs.
- Frontend first-run — separate repository (`rendering-replicate`).

---

## 10. Definition of Done

- [ ] All acceptance criteria (Section 3) verified by QA
- [ ] All tests pass (`pytest agent/tests/`, `pytest backend/tests/`, `cd frontend && npm test`)
- [ ] Lint/type gates pass (`ruff check . && ruff format --check . && mypy .`, `cd frontend && npm run lint && npm run typecheck`)
- [ ] Documentation updated and reviewed
- [ ] PM acceptance