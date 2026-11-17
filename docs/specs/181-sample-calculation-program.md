# Specification: Sample Calculation Program for Agent Testing

**Issue**: #181 — "Create sample calculation program and profile a ~1 minute CPU/GPU workload for agent testing"
**Labels**: `agent`, `enhancement`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-11-17
**Status**: Groomed
**Baseline**: `main` (post #178)

**This file is the authoritative input for the SWE cycle.** It is self-sufficient:
§3 carries the decisions, §6 carries the code shapes, §9 is the acceptance
checklist. No code is implemented by this spec.

---

## 0. Baseline, as re-measured by PM

| Gate | On `main` |
|---|---|
| `ruff check . && ruff format --check . && mypy .` | expected clean |
| `pytest --cov=backend` (incl. `agent/tests`) | expected green; compose suite skipped unless `SHC_COMPOSE_TESTS=1` |
| `npm run typecheck` / `npm run lint` (frontend) | unaffected — no frontend change |

The changes in this spec must not move any of those needles. No new runtime Python
dependencies are introduced; the sample program uses only the standard library plus
optional `numpy`/`torch` when present. The `syncthing/` directory stays gitignored
and is never committed.

---

## 1. Verified Problem Statement

### 1.1 No representative workload exists

The repository has no self-contained program that exercises the worker agent's
full job path (submission → claim → execution → log capture → terminal state).
Ad-hoc shell commands (`echo`, `sleep`) are used in tests, which do not validate
long-running handling, progress reporting, cancellation, GPU/CPU placement, or
deterministic output verification.

### 1.2 Runtime is not predictable

Without a known workload whose wall-clock time is stable, it is impossible to
validate timeout/cancellation behaviour or to compare node throughput. A sample
that finishes in ~1 minute on both CPU and GPU gives a stable yardstick.

### 1.3 Distribution is not defined

Jobs reference a `command` string and a `working_dir`. There is no canonical
location for shared executables. The Syncthing shared folder already syncs
`nodes/`, `jobs/`, and `acks/`; a `bin/` subfolder is the natural place for a
single canonical copy of a runnable that every node can execute without a
per-node install.

### 1.4 The child environment cannot see `SYNCTHING_ROOT`

`agent/executor.py:INTERNAL_ENV_VARS` strips `SYNCTHING_ROOT` (and every
`*_TOKEN`/`*_KEY`) from the child environment. A program placed in
`SYNCTHING_ROOT/bin/` therefore **cannot** discover its own location via
`os.environ["SYNCTHING_ROOT"]`. Any design that relies on that variable in the
child will fail at runtime. The program must be located by a relative path
resolved against the job's working directory (§6.5).

### 1.5 GPU compute libraries are optional

`pyproject.toml` declares no `numpy`, `torch`, `cupy`, or `numba`. A node may or
may not have them. The program must run everywhere (at least in smoke mode) and
use accelerated backends when available, without making any of them a hard
dependency.

---

## 2. Goals / Non-Goals

### Goals

- Provide one self-contained sample calculation program that runs on CPU and,
  when available, on GPU (CUDA).
- Provide deterministic, documented parameter presets that yield ~1 minute
  wall-clock on CPU and on GPU (±20%).
- Provide a calibration mode so presets can be re-derived on any node.
- Define the `bin/` distribution layout in the Syncthing shared folder and a
  job payload that references it.
- Keep core scheduling/job semantics untouched.

### Non-Goals

- No changes to backend scheduling, claim logic, or job state machine.
- No new required Python dependencies.
- No automatic installation of the program onto nodes by the agent (operator
  distributes it; §6.4).
- No GUI or frontend changes.
- No PyInstaller build of the sample (it stays a Python script + thin
  wrappers).

---

## 3. User Stories

| ID | Story |
|----|-------|
| US-1 | As a developer testing the agent, I run a job whose command is the sample program with `--preset cpu-1min`, and the job completes in ~60 s with a deterministic checksum in its log. |
| US-2 | As an operator on a GPU node, I run the same program with `--preset gpu-1min` and it uses the GPU and finishes in ~60 s. |
| US-3 | As a developer on a machine without torch/numpy, I run `--preset smoke` and the program still runs and exits 0. |
| US-4 | As an operator, I copy the `samples/` contents into `SYNCTHING_ROOT/bin/` once and every node can execute them without further installation. |
| US-5 | As a developer, I run `--calibrate` and get a suggested iteration count for a 60 s run on the current machine/backend. |
| US-6 | As QA, I submit the provided job YAML and verify the agent executes it end-to-end and reports `COMPLETED`. |

---

## 4. Functional Requirements

- **FR-1** The program accepts `--device {auto,cpu,cuda}`, `--backend
  {auto,torch,numpy,purepython}`, `--size N`, `--iters N`, `--seed N`,
  `--preset {smoke,cpu-1min,gpu-1min}`, `--progress-every N`, `--output PATH`,
  `--calibrate`, `--calibrate-seconds S`, and `--json`.
- **FR-2** Backend selection: `auto` prefers `torch` (with CUDA if `--device
  cuda` or `auto` and a GPU is available), then `numpy`, then `purepython`.
  Requesting an unavailable backend exits with code 2.
- **FR-3** The workload is a deterministic repeated square matrix
  multiplication: fixed seeded `A`, `B` of shape `(size, size)`; for each of
  `iters` iterations compute `C = A @ B` (without feeding `C` back, to avoid
  overflow) and accumulate `checksum += C.sum()`. The final checksum is printed.
- **FR-4** Progress is emitted to **stderr** every `--progress-every` iterations
  (default: 10% of `iters`, minimum 1).
- **FR-5** The final result is emitted to **stdout** as a single `RESULT ...`
  line, and, when `--output PATH` is given, the same data is written as JSON to
  that path (relative paths resolve against the current working directory).
- **FR-6** `--calibrate` runs the workload for `--calibrate-seconds` (default 5)
  on the selected backend/size, measures iterations/second, and prints a
  suggested `--iters` for a 60 s run (JSON when `--json`).
- **FR-7** Presets override `--size`/`--iters` unless those are explicitly
  given on the command line. Precedence: explicit flags > preset > backend
  default.
- **FR-8** The program is a single file `sample_calc.py` with a
  `#!/usr/bin/env python3` shebang, plus thin wrappers `sample_calc` (POSIX)
  and `sample_calc.cmd` (Windows) that invoke it with the local Python.
- **FR-9** No use of `SYNCTHING_ROOT` or any cluster-internal environment
  variable. Location is discovered via `argv[0]`/cwd only.

---

## 5. Non-Functional Requirements

- **NFR-1 Runtime**: `cpu-1min` preset finishes in 60 s ±20% on a typical
  CPU-only node for its backend. `gpu-1min` finishes in 60 s ±20% on a
  GPU-capable node.
- **NFR-2 Determinism**: identical `(backend, device, size, iters, seed)`
  produce a checksum that matches to at least 6 significant figures across runs
  on the same backend.
- **NFR-3 Exit codes**: 0 success, 1 invalid arguments, 2 backend unavailable,
  3 computation error, 130 interrupted (`KeyboardInterrupt`).
- **NFR-4 Dependencies**: standard library only is required; `numpy`/`torch`
  are optional and detected at runtime.
- **NFR-5 Output**: final result line is parseable by a simple regex; JSON
  output is valid UTF-8.
- **NFR-6 Concurrency**: the program uses only its own process; it does not
  spawn children or touch cluster state files.

---

## 6. Design

### 6.1 Algorithm and backend choice

Repeated dense matrix multiplication is chosen because:

- It is compute-bound and scales predictably with `size` and `iters`.
- It exercises CPU BLAS and, on GPU, CUDA/cuBLAS, which is exactly what
  agent placement tests need to distinguish.
- It is trivially deterministic given a seed.
- It needs no input files and produces a single scalar, which keeps log capture
  and verification simple.

Backend priority and measured per-iteration cost (PM, Windows 11, CPU only):

| Backend | Size | s/iter (measured) | Preset iters (target 60 s) | Notes |
|---|---|---|---|---|
| `torch` (CPU) | 2048 | 0.0508 | 1200 | MKL/oneDNN; 200 iters = 10.17 s |
| `numpy` | 2048 | 0.1209 | 500 | OpenBLAS; 20 iters = 2.42 s |
| `purepython` | 128 | 0.1304 | 460 | triple loop; 1 iter = 0.13 s |
| `torch` (CUDA) | 4096 | *to be profiled* | *initial 2000* | no CUDA toolkit on PM host; SWE profiles |

The GPU preset starts at `size=4096, iters=2000` and **must** be re-profiled on a
GPU node; the calibration mode (FR-6) exists precisely so this is reproducible.
SWE records the final numbers in `samples/README.md`.

### 6.2 CLI

```
sample_calc.py
  [--device {auto,cpu,cuda}]          default: auto
  [--backend {auto,torch,numpy,purepython}] default: auto
  [--size N]                          default: backend preset
  [--iters N]                         default: backend preset
  [--seed N]                          default: 42
  [--preset {smoke,cpu-1min,gpu-1min}] default: smoke
  [--progress-every N]                default: max(1, iters // 10)
  [--output PATH]                     default: none
  [--calibrate]                       run calibration instead of full workload
  [--calibrate-seconds S]             default: 5
  [--json]                            JSON output
```

Preset sizes:

| Preset | torch CPU | numpy | purepython | torch CUDA |
|---|---|---|---|---|
| `smoke` | size=64, iters=2 | size=64, iters=2 | size=32, iters=2 | size=64, iters=2 |
| `cpu-1min` | size=2048, iters=1200 | size=2048, iters=500 | size=128, iters=460 | n/a |
| `gpu-1min` | n/a | n/a | n/a | size=4096, iters=*profiled* |

### 6.3 Output format

Stdout (default):
```
RESULT backend=torch device=cpu size=2048 iters=1200 seed=42 checksum=1.234567890123456e+12 elapsed_seconds=61.23 iters_per_second=19.60
```

Stderr progress:
```
PROGRESS iters=120/1200 elapsed=6.12s eta=55.11s
```

JSON (`--json` or `--output`):
```json
{
  "backend": "torch",
  "device": "cpu",
  "size": 2048,
  "iters": 1200,
  "seed": 42,
  "checksum": 1.234567890123456e+12,
  "elapsed_seconds": 61.23,
  "iters_per_second": 19.60,
  "exit_code": 0
}
```

Calibration output (`--calibrate --json`):
```json
{
  "backend": "torch",
  "device": "cpu",
  "size": 2048,
  "calibrate_seconds": 5.0,
  "iters_completed": 98,
  "iters_per_second": 19.6,
  "suggested_iters_for_60s": 1176
}
```

### 6.4 File location and `bin/` distribution

Source of truth in the repository: `samples/`.

```
samples/
  sample_calc.py          # the program (shebang, executable)
  sample_calc            # POSIX wrapper
  sample_calc.cmd        # Windows wrapper
  README.md              # usage, presets, profiling table
  job-sample-calc-cpu.yaml
  job-sample-calc-gpu.yaml
```

Runtime location (Syncthing shared folder):
```
<SYNCTHING_ROOT>/
  bin/
    sample_calc.py
    sample_calc
    sample_calc.cmd
    README.txt
```

Distribution procedure (documented, operator-driven):

1. Create `<SYNCTHING_ROOT>/bin/` if absent.
2. Copy the contents of `samples/` (excluding `job-*.yaml`) into `bin/`.
3. On POSIX, `chmod +x bin/sample_calc bin/sample_calc.py`.
4. Syncthing syncs `bin/` to every node. No further install.

The agent does **not** create or modify `bin/`. A convenience helper
`scripts/install_sample_calc.py --syncthing-root PATH` may automate step 2–3;
it is optional and idempotent (overwrites, never deletes unrelated files).

`bin/` is **not** added to `.stignore`, so it syncs like `nodes/` and `jobs/`.

### 6.5 How the agent references the program

Because `SYNCTHING_ROOT` is stripped from the child environment, the job uses a
working directory of `.` (the Syncthing root) and a relative command path:

```yaml
name: sample-calc-cpu
command: "python bin/sample_calc.py --preset cpu-1min --seed 42 --progress-every 100"
working_dir: "."
env: {}
resources:
  gpus: 0
  cpus: 4
  memory_gb: 8
  vram_gb: 0
paths:
  input: null
  output: null
```

`resolve_working_dir(".", SYNCTHING_ROOT)` returns `SYNCTHING_ROOT` (relative
path, resolved against the root — never against the agent's CWD), so the
shell's cwd is the Syncthing root and `bin/sample_calc.py` resolves correctly.
The GPU variant sets `gpus: 1` and `--preset gpu-1min`.

Notes:

- The command uses `python`, which is on `PATH` on every node that runs the
  agent (the agent itself is Python).
- Forward slashes are accepted by `cmd.exe`/PowerShell and by POSIX shells, so
  one command string works on both.
- If an operator prefers the wrapper, `bin/sample_calc` (POSIX) or
  `bin/sample_calc.cmd` (Windows) can be used instead; the command string then
  becomes platform-specific.

### 6.6 Determinism

- Seeds: `torch.manual_seed(seed)` and `torch.use_deterministic_algorithms(True)`
  where available; `numpy.random.default_rng(seed)`; `random.Random(seed)` for
  the pure-Python backend.
- `A` and `B` are generated once and never overwritten; each iteration computes
  `C = A @ B` from the originals, so values cannot grow and the checksum is
  stable.
- BLAS may introduce last-bit differences across libraries; the documented
  tolerance is 6 significant figures, not bit-exact.

### 6.7 Error handling

| Condition | Exit code | stderr message prefix |
|---|---|---|
| Unknown/invalid argument | 1 | `error: ...` (argparse) |
| Requested backend unavailable | 2 | `error: backend 'X' is not available (...)` |
| Requested cuda but no GPU | 2 | `error: CUDA is not available on this machine` |
| Exception during computation | 3 | `error: computation failed: ...` |
| `KeyboardInterrupt` | 130 | `interrupted` |
| Invalid `--size`/`--iters` (≤0) | 1 | `error: size/iters must be positive` |

The program never writes partial output files: `--output` is written only after
a successful run (atomic temp + rename). On failure the output path is left
untouched (or removed if partially written).

---

## 7. Data Flow / Execution Flow

```
Operator copies samples/ -> SYNCTHING_ROOT/bin/  (once, synced by Syncthing)
        |
Job YAML submitted with working_dir="." and command="python bin/sample_calc.py ..."
        |
Agent resolves working_dir -> SYNCTHING_ROOT (relative, not CWD)
        |
Agent builds child env (strips SYNCTHING_ROOT, keeps PATH)
        |
Shell launches: python bin/sample_calc.py ...  (cwd = SYNCTHING_ROOT)
        |
Program detects backend, seeds RNG, runs iters, prints progress (stderr)
and RESULT (stdout); optionally writes JSON output
        |
Agent captures stdout/stderr to jobs/<id>/logs/, writes terminal state
        |
Job status -> COMPLETED (exit 0) or FAILED with process_failed:exit_code=...
```

---

## 8. Test Scenarios

| # | Scenario | Backend | Expected |
|---|---|---|---|
| T1 | `--preset smoke` | auto | exit 0, RESULT line present, < 5 s |
| T2 | `--preset cpu-1min` | torch CPU | exit 0, ~60 s ±20% |
| T3 | `--preset cpu-1min` | numpy | exit 0, ~60 s ±20% |
| T4 | `--preset cpu-1min` | purepython | exit 0, ~60 s ±20% |
| T5 | `--preset gpu-1min --device cuda` | torch CUDA | exit 0, ~60 s ±20%, GPU utilised |
| T6 | `--backend torch --device cuda` on CPU-only node | torch | exit 2, "CUDA is not available" |
| T7 | `--backend numpy` where numpy absent | — | exit 2 |
| T8 | `--size 0` | any | exit 1 |
| T9 | Determinism: two runs same params | any | checksums match to 6 sig figs |
| T10 | `--calibrate --json` | any | valid JSON with `suggested_iters_for_60s` |
| T11 | `--output result.json` | any | file written, valid JSON, matches stdout |
| T12 | KeyboardInterrupt (SIGINT) | any | exit 130 |
| T13 | Job end-to-end via agent | agent | COMPLETED, log contains RESULT |
| T14 | Lint/type gates | — | ruff/mypy clean on touched files |

---

## 9. Acceptance Criteria

### AC-1: Standalone CPU run
**Given** a machine with Python 3.11+  
**When** `python samples/sample_calc.py --preset cpu-1min` is run  
**Then** it exits 0 and prints a `RESULT ...` line within 60 s ±20%.

### AC-2: GPU run
**Given** a machine with a CUDA-capable GPU and torch with CUDA  
**When** `python sample_calc.py --preset gpu-1min --device cuda` is run  
**Then** it exits 0, uses the GPU, and finishes within 60 s ±20%.

### AC-3: Smoke on minimal environment
**Given** an environment with only the standard library  
**When** `python sample_calc.py --preset smoke` is run  
**Then** it exits 0 using the `purepython` backend.

### AC-4: Determinism
**Given** two runs with identical parameters  
**When** their checksums are compared  
**Then** they agree to at least 6 significant figures.

### AC-5: Backend unavailable
**Given** a requested backend that is not installed (or CUDA on a CPU-only node)  
**When** the program is run  
**Then** it exits with code 2 and a clear error on stderr.

### AC-6: `bin/` distribution
**Given** the contents of `samples/` (excluding `job-*.yaml`) are copied to
`SYNCTHING_ROOT/bin/`  
**When** Syncthing syncs  
**Then** every node has an identical `bin/` tree and the program runs from there.

### AC-7: Agent job reference
**Given** a job submitted with `working_dir: "."` and
`command: "python bin/sample_calc.py --preset cpu-1min"`  
**When** the agent executes it  
**Then** the job reaches `COMPLETED` and its log contains the `RESULT` line.

### AC-8: Profiling recorded
**Given** the implementation is complete  
**When** `samples/README.md` is read  
**Then** it contains the measured profiling table with final preset parameters
for each backend, including the GPU node's measured values.

### AC-9: No regression
**Given** the full test suite and lint/type gates  
**When** run  
**Then** all pass with no new failures.

---

## 10. Implementation Notes for SWE

### Files to create

- `samples/sample_calc.py` — the program (argparse, backend detection, workload,
  progress, output, calibration, exit codes).
- `samples/sample_calc` — POSIX wrapper:
  ```sh
  #!/bin/sh
  exec python3 "$(dirname "$0")/sample_calc.py" "$@"
  ```
- `samples/sample_calc.cmd` — Windows wrapper:
  ```bat
  @echo off
  python "%~dp0sample_calc.py" %*
  ```
- `samples/README.md` — usage, presets, profiling table (fill measured GPU
  values), distribution steps.
- `samples/job-sample-calc-cpu.yaml` and `samples/job-sample-calc-gpu.yaml` —
  job payloads per §6.5.
- `scripts/install_sample_calc.py` — optional idempotent installer copying
  `samples/*` (excluding `job-*.yaml`) into `SYNCTHING_ROOT/bin/` and setting
  the executable bit on POSIX. Accepts `--syncthing-root PATH`.
- `agent/tests/test_sample_calc.py` — subprocess-based tests for T1, T6, T7,
  T8, T9, T10, T11, T12 (skip GPU test when no CUDA). Mark slow tests with
  `@pytest.mark.slow`.

### Files to modify

- `README.md` — add a "Sample workload" subsection under the worker-agent
  section pointing to `samples/` and `SYNCTHING_ROOT/bin/`.
- `agent/README.md` — mention `bin/` distribution and the job payload example.
- `Makefile` — add `install-sample` target invoking the installer.

### Implementation guidance

- Use `argparse` with `exit_on_error=False` so invalid arguments raise
  `SystemExit(1)` controllably; catch and print `error: ...` to stderr.
- Backend detection:
  - `torch`: `import torch`; CUDA available iff `torch.cuda.is_available()`.
  - `numpy`: `import numpy`.
  - `purepython`: always available.
- Keep the workload function backend-agnostic where possible: each backend has
  a `run(size, iters, seed) -> float` returning the checksum.
- Progress: write to `sys.stderr`, flush each line.
- Output: build the result dict, print the `RESULT` line, then if `--output`,
  write atomically (temp file in same dir, `os.replace`).
- Calibration: run a loop with a 0.2 s warm-up discarded, then count iterations
  until `calibrate_seconds` elapses; report `suggested_iters_for_60s = round(
  iters_per_second * 60)`.
- Do not import `shared` or `agent` — the program must run standalone on a
  node that only has the file.
- Add type hints; keep ruff/mypy clean (the file is not in a package, but tests
  and CI may lint `samples/`).

### Tests (minimum)

- T1 smoke run exits 0 and stdout matches `^RESULT `.
- T8 invalid size exits 1.
- T6/T7 unavailable backend exits 2.
- T9 determinism: two smoke runs, checksums equal as strings.
- T10 calibration JSON parses and contains `suggested_iters_for_60s > 0`.
- T11 output file written and matches stdout checksum.
- T13 (integration, opt-in via `SHC_AGENT_TESTS=1`) runs the agent end-to-end;
  may be deferred to QA.

---

## 11. Risks / Assumptions

- **R1** GPU profiling cannot be performed on the PM host (no CUDA toolkit /
  no torch-CUDA build). The `gpu-1min` preset is seeded with `size=4096,
  iters=2000` and **must** be re-profiled by SWE/QA on a GPU node; the
  calibration mode exists to make this reproducible. Mitigation: AC-8 requires
  the final table in `samples/README.md`.
- **R2** Preset runtimes depend on BLAS thread count and CPU model; ±20% is
  the accepted tolerance. Operators can override with explicit `--size`/`--iters`.
- **R3** `bin/` is synced by Syncthing but is not version-pinned; concurrent
  edits from multiple nodes could conflict. Mitigation: treat `bin/` as
  read-only distributed artifacts updated by one operator at a time; document
  this.
- **R4** The program relies on `python` being on `PATH` in the child
  environment. This holds for any node running the agent, but a node where only
  `python3` exists needs the POSIX wrapper or an adjusted command string.
- **R5** Pure-Python fallback is single-threaded and slow; its 60 s preset is
  long but correct. It is intended for smoke/functional testing, not
  performance benchmarking.
- **R6** No regression in existing tests is expected because no existing file
  is modified except documentation and an optional installer script.

---

## 12. Open Questions — Resolved

- **Q1 — Where should the source live?** `samples/` at repository root. It is
  not a package and is not imported by the agent/backend.
- **Q2 — Compiled or script?** Script + thin wrappers. PyInstaller is out of
  scope; the agent already requires Python on the node.
- **Q3 — How does the program find `SYNCTHING_ROOT`?** It does not. The job
  uses `working_dir: "."` so the shell's cwd is the Syncthing root, and the
  command uses the relative path `bin/sample_calc.py`.
- **Q4 — Should the agent auto-install it?** No. Distribution is operator-driven
  via Syncthing, consistent with `nodes/` and the file-only transport decision.
- **Q5 — GPU library?** `torch` with CUDA when available; no `cupy`/`numba`
  dependency added. `GPUtil` is not used (it only reports device info, not
  compute).
