# Specification: Make the lint and type gates green (Issue #54)

> **This supersedes the `593c4dd` version of this spec.** That version described
> the pre-#54 state (41 ruff errors, 139 mypy errors, 4 files needing format) and
> drove commit `593c4dd`, which fixed the bulk of it. The residual is 7 items, all
> in `shared/file_ops/`. The counts in the old document are stale and must not be
> used as a baseline.

## Issue summary

Issue #54: *"Backend ruff and mypy are red on main, so a CI gate added under #26
would land red."* PR #116 (#114) landed meanwhile and is unrelated. The gates are
**still red** — this issue makes them exit 0 so #26 can add a gate that lands green.

## Ground truth on `main` @ `216093d`

Measured on Linux, Python 3.13.5, ruff 0.5.0, mypy 1.0.0.

```
$ .venv/bin/python -m ruff check .
shared/file_ops/locking.py:43:5: C901 `file_lock` is too complex (14 > 10)
shared/file_ops/locking.py:128:5: C901 `shared_lock` is too complex (14 > 10)
shared/file_ops/yaml_utils.py:50:89: E501 Line too long (91 > 88)
shared/file_ops/yaml_utils.py:112:89: E501 Line too long (90 > 88)
shared/file_ops/yaml_utils.py:130:24: F401 [*] `typing.Callable` imported but unused
Found 5 errors.

$ .venv/bin/python -m ruff format --check .
Would reformat: shared/file_ops/yaml_utils.py

$ .venv/bin/python -m mypy .
shared/file_ops/yaml_utils.py:7: error: Library stubs not installed for "yaml"  [import]
Found 1 error in 1 file (checked 73 source files)
```

**7 items: 5 ruff + 1 ruff-format + 1 mypy.** All in `shared/file_ops/`.

Test baseline: `pytest` (no args) → **691 passed, 29 skipped**, exit 0.
`pytest backend/tests -q` → **521 passed, 29 skipped**.

## Resolution plan, per item

### 1–2. C901 `file_lock` / `shared_lock` complexity 14 > 10 → **refactor, do not suppress**

`shared/file_ops/locking.py` is 209 lines. `file_lock` (L43–124) and
`shared_lock` (L128–209) are ~82-line near-copies that differ only in the lock
mode passed to portalocker (`LOCK_EX` vs `LOCK_SH`). The complexity is
**duplication, not inherent difficulty**.

Suppressing C901 would leave the duplication in place *and* hide the marker on
exactly the code issue #71 just touched. So: extract the shared core.

**Intended shape** — one private core that takes the lock flags, plus two thin
public wrappers that keep their existing signatures, docstrings and error
messages:

```python
def _portalocker_flags(shared: bool) -> int: ...      # LOCK_SH or LOCK_EX | LOCK_NB
def _acquire(f, shared, timeout) -> bool: ...          # the retry loop, platform branches
def _release(f) -> None: ...                          # the release branches
def _lock(file_path, mode, timeout, *, shared, label) -> Generator: ...   # the core
@contextmanager
def file_lock(file_path, mode="r", timeout=10.0) -> Generator: ...        # calls _lock(shared=False)
@contextmanager
def shared_lock(file_path, mode="r", timeout=10.0) -> Generator: ...      # calls _lock(shared=True)
```

`label` carries `"shared lock"` vs `"lock"` so both existing `TimeoutError`
messages and both `logger.warning` texts stay byte-identical. A prototype of
this shape reached **complexity 5** (limit 10, so 2× headroom) and took the file
from 209 to ~130 lines, with all three gates green.

#### HARD CONSTRAINT — the `lock_acquired` guard must survive

A first prototype of this refactor **dropped the `lock_acquired` flag** (L71,
L100, L112). That flag is load-bearing: without it, a thread whose acquire loop
timed out would reach `finally` and call `portalocker.unlock(f)` on a descriptor
it never locked — which on POSIX **releases another thread's lock**. The guard
must be preserved exactly:

```python
lock_acquired = False
try:
    ...
    if not lock_acquired:
        f.close()
        raise TimeoutError(...)
    yield f
finally:
    try:
        if lock_acquired:      # <-- MUST remain
            _release(f)
    except Exception as e:
        logger.warning(...)
    finally:
        f.close()
```

QA/verification must prove this, not assume it (see AC-5).

### 3–4. E501 line-too-long (2×) → mechanical

`yaml_utils.py:50` (91 chars) and `:112` (90 chars). Split the offending
expressions. Do not silence E501.

### 5. F401 unused `typing.Callable` (1×) → mechanical

`yaml_utils.py:10` imports `Callable` and `yaml_utils.py:130` re-imports it
*inside* `update_yaml`. The top-level import is the unused one (F401). Delete it
from line 10. Note the local re-import at L130 is then redundant but is **not**
flagged; removing it too is fine and tidier, but is not required.

### 6. `ruff format --check` → run `ruff format` on `yaml_utils.py`

Mechanical. Must not reformat the rest of the repo into a diff (verify with
`git status` that only the intended files changed).

### 7. mypy `Library stubs not installed for "yaml"` → **environment, not code**

`types-PyYAML==6.0.12.20260906` is **already pinned** at `pyproject.toml:45` in
the `dev` extra (added by PRs #74/#84). The local `.venv` predates it —
`pip list` shows only `PyYAML 5.3.1`.

**The fix is to build the venv correctly, not to change source or config.**
Verified: `pip install -e ".[dev]"` completes cleanly (pip 26.2.1, Python 3.13.5)
and in such a venv mypy is green with **zero** source or config changes.

Do **not** add a `[[tool.mypy-overrides]]` for `yaml`. `ignore_missing_imports =
true` is already set globally *and the error still fires* — a further override
would be dead configuration that hides a real missing dependency.

## Scope

**In scope:** the 7 items above, and regression tests for the locking refactor.

**Explicitly out of scope:**
- Issue **#107** (dev-install / environment). Its central claim — that
  `pip install -e ".[dev]"` fails — is **no longer true**; all three of its
  defects were fixed by PRs #81, #80, #78. #107 should be closed separately.
- Issue **#26** (CI workflow). This issue only makes the gates green so #26 can
  land a green gate.
- The pre-existing `[annotation-unchecked]` mypy **notes** under `backend/` —
  they are informational and do not affect the exit code.
- Windows behaviour. This refactor must stay platform-neutral but cannot be
  executed against Windows here (see Risks).

## Acceptance criteria

- [ ] **AC-1 (headline).** In a **fresh venv** built with
      `python -m venv venv && pip install -e ".[dev]"`, all three of
      `ruff check .`, `ruff format --check .`, `mypy .` **exit 0**.
      Running them in the pre-existing `.venv` does **not** satisfy this — that
      venv is stale and is exactly the trap issue #107 describes.
- [ ] **AC-2.** `ruff check .` reports `All checks passed!`.
- [ ] **AC-3.** `ruff format --check .` reports all files already formatted.
- [ ] **AC-4.** `mypy .` reports `Success: no issues found`.
- [ ] **AC-5.** The `lock_acquired` guard is preserved: a thread that times out
      acquiring a lock **must not** release a lock it does not hold. Proven by a
      test, not by reading the diff (see test scenarios).
- [ ] **AC-6.** `file_lock` and `shared_lock` keep their **public signatures,
      docstrings, and exact `TimeoutError` / `logger.warning` message text** —
      callers (`backend/services/syncthing_service.py`, the CLI, and the tests)
      depend on them. Grep for callers and confirm none broke.
- [ ] **AC-7.** `shared_lock` still requests a *shared* lock and `file_lock` an
      *exclusive* one. A test must observe the actual flag reaching portalocker
      (mock/spy), not infer it from the function name.
- [ ] **AC-8.** `pytest` (no args) still reports **691 passed, 29 skipped**, exit 0.
      Any delta must be explained.
- [ ] **AC-9.** `pytest backend/tests -q` still reports **521 passed, 29 skipped**.
- [ ] **AC-10.** `ruff check agent/` still passes; `mypy agent/` still succeeds —
      the #114 fix must not regress.
- [ ] **AC-11.** The only files changed are `shared/file_ops/locking.py`,
      `shared/file_ops/yaml_utils.py`, any **new** test file, and this spec.
      No drive-by reformatting elsewhere (`git diff --stat` proof).

## Test scenarios

1. **Contention timeout does not steal another thread's lock** (AC-5). Hold an
   exclusive `file_lock` on a path in thread A; in thread B attempt
   `file_lock` on the same path with a short timeout and assert it raises
   `TimeoutError`. Then assert thread A's lock is **still held** — re-acquiring
   in a third party must still time out, and the file must still be exclusively
   locked. This is the test that fails if `lock_acquired` is dropped.
2. **Lock mode is correct** (AC-7). Spy on `portalocker.lock` and assert
   `file_lock` passes a flag containing `LOCK_EX` and `shared_lock` one
   containing `LOCK_SH`.
3. **Messages preserved** (AC-6). Assert `file_lock` raises `TimeoutError`
   whose text contains `"Could not acquire lock on"` and `shared_lock` one
   containing `"Could not acquire shared lock on"`.
4. **Existing locking tests still pass** — there is already
   `backend/tests/unit/test_shared_file_ops.py::test_lock_timeout` and
   `test_portalocker_retry_on_contention`. Do not delete or weaken them.

## Risks

- **R1 — dropping `lock_acquired`.** The single highest risk. Mitigated by AC-5
  and test scenario 1. Issue #71 recently modified this file, so re-read it
  before refactoring.
- **R2 — behaviour drift in the platform branches.** The `msvcrt` / `fcntl`
  fallbacks are unreachable on Linux when portalocker is installed. Preserve
  them verbatim; do not "simplify" them away, even though they are untestable
  here.
- **R3 — Windows unverified.** The refactor must be platform-neutral by
  construction. It cannot be executed against Windows from this environment.
  Say so explicitly in the PR rather than claiming cross-platform verification.
- **R4 — `BaseLockException` import.** `locking.py` L17 imports
  `portalocker.exceptions.BaseLockException` inside a `try:` and sets it to
  `None` on `ImportError` (L22). The `except (OSError, IOError,
  BaseLockException)` tuple at L95/L180 therefore contains `None` when
  portalocker is absent. Do not change this; it is pre-existing and out of
  scope, but be aware a refactor that "tidies" it could alter behaviour.
- **R5 — the stale `.venv`.** Verifying AC-1 in the existing `.venv` proves
  nothing. Build a fresh venv, or at minimum `pip install -e ".[dev]"` into it
  and confirm `types-PyYAML` is present.

## Open questions

1. Should #107 be closed as fixed? Its premise no longer holds. (Recommend yes;
   PM decision.)
2. Should the `.venv` in the repo be deleted as part of #26, to stop it being a
   trap? (Out of scope here.)