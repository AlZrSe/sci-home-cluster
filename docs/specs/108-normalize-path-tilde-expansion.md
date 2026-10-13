# Spec: `normalize_path()` Tilde Expansion Fix

> **Two grooming passes produced two specs for #108, independently.** They were
> merged by hand (conflict resolution, 2026-10-13) into this single document:
> §1–§8 below are from the pass that specified `os.path.expanduser()`, and §2.4,
> §3, §4.4, §5.2 and §7.2 are from the pass that specified `Path.expanduser()`
> **and** covered `is_within_syncthing_root()`'s error masking, which the first
> pass did not mention at all. Where the two disagreed on the tilde-expansion
> mechanism, **`os.path.expanduser()` won** — see §8.1. Nothing was dropped.

## Issue
**#108** — `normalize_path()` raises `TypeError: 'PosixPath' object is not subscriptable` when given a path containing `~` (e.g., `~/projects`), instead of expanding the tilde to the user's home directory.

**Labels**: `bug`, `phase-1`, `ready-for-grooming` → will become `groomed`

**Related**: #93 (agent skeleton — documents this bug as K9/D6), #54 (backend lint fix), #98 (shared package consolidation)

---

## 1. Verified Problem Statement

### 1.1 Symptoms
Calling `normalize_path("~/some/path")` raises:
```
TypeError: 'PosixPath' object is not subscriptable
  at shared/file_ops/path_utils.py:28  (line number may vary)
```

### 1.2 Reproduction
```python
from shared.file_ops.path_utils import normalize_path

# This raises TypeError instead of returning expanded Path
normalize_path("~/projects")
```

### 1.3 Expected Behaviour
- `normalize_path("~/projects")` → `Path("/home/user/projects")` (or equivalent on Windows)
- `normalize_path("~")` → `Path.home()`
- `normalize_path("~otheruser/path")` → expands to that user's home (best effort; may not work on all platforms)
- Absolute paths and relative paths without tilde continue to work unchanged

### 1.4 Current Workaround
Callers must manually expand tilde before calling `normalize_path`:
```python
# From agent/paths.py:46
normalize_path(Path(value).expanduser())
```
This violates the function's documented contract: *"Normalize a path to a Path object, resolving ~ and making absolute if needed."*

---

## 2. Root Cause

### 2.1 Defective Tilde-Expansion Logic (lines 23–28)
```python
# Expand user home directory (~)
if path_obj == Path("~"):
    path_obj = Path.home()
elif str(path_obj).startswith("~/"):
    path_str = str(path_obj)
    path_obj = Path.home() / path_str[2:]
```

**Three defects:**
1. **`str(path_obj).startswith("~/")` is platform-fragile** — on Windows `str(Path("~/x"))` is `"~\\x"`, so the check fails and the tilde is never expanded.
2. **`path_str[2:]` assumes `path_str` is a `str`** — but the reported `TypeError: 'PosixPath' object is not subscriptable` indicates `path_str` is sometimes a `Path` object. This suggests a type-confusion bug or a version-specific `pathlib` quirk where `str(Path(...))` returns a `Path` subclass (unlikely but observed).
3. **No support for `~user/path`** — only bare `~` and `~/` are handled.

### 2.2 Missing `expanduser()` Call
The standard library provides `os.path.expanduser()` (all versions) and `Path.expanduser()` (Python 3.11+). The function uses neither, instead implementing a brittle custom parser.

### 2.3 Why This Was Not Caught Earlier
- No unit tests for `normalize_path` exist in `backend/tests/unit/test_shared_file_ops.py`.
- The function is only exercised indirectly via `get_syncthing_root()` and downstream helpers, which are integration-tested with a concrete `SYNCTHING_ROOT` (no tilde).
- The agent skeleton (#93) discovered the bug when implementing `SYNCTHING_ROOT` resolution with tilde support.

### 2.4 Secondary bug: `is_within_syncthing_root()` masks the error

The same defect was invisible in production because its only caller swallowed it:

```python
def is_within_syncthing_root(path: Union[str, Path]) -> bool:
    syncthing_root = get_syncthing_root()
    if not syncthing_root:
        return False
    try:
        path_obj = normalize_path(path)
        return syncthing_root in path_obj.parents or path_obj == syncthing_root
    except Exception:          # <-- bare catch, no logging
        return False
```

Two problems:

1. **The bare `except Exception:` turns every `normalize_path()` failure into
   "not within root"** — the caller cannot distinguish a genuine
   `False` from a code bug. This is why the `TypeError` above was reported
   as "SYNCTHING_ROOT silently returns `None`" rather than as a crash.
2. **Nothing is logged**, so the error disappears completely.

Impact is not local — `backend/services/syncthing_service.py` and any future
caller inherits the masking.

---

## 3. Scope Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| Fix `normalize_path()` to correctly expand `~` and `~user/` on all platforms | Change function signature or return type |
| Add comprehensive unit tests for tilde expansion | Modify callers that already work around the bug (they will naturally benefit) |
| Ensure `Path.expanduser()` (3.11+) or `os.path.expanduser()` fallback is used | Fix `~user/` on Windows (platform limitation; document as known gap) |
| Preserve existing behaviour for absolute/relative paths without tilde | Address `Path.resolve()` symlink resolution semantics (separate concern) |
| Narrow `is_within_syncthing_root()`'s bare `except Exception:` to expected filesystem errors, and log them (§2.4) | Change `is_within_syncthing_root()`'s signature or return type |

---

## 4. Acceptance Criteria

All checkboxes must be verifiable with the commands shown.

### 4.1 Functional Acceptance

- [ ] **AC-1**: Bare tilde expands to home directory
  ```bash
  python -c "from shared.file_ops.path_utils import normalize_path; print(normalize_path('~'))"
  # Output must equal Path.home()
  ```

- [ ] **AC-2**: `~/path` expands to home directory + path
  ```bash
  python -c "from shared.file_ops.path_utils import normalize_path; print(normalize_path('~/projects'))"
  # Output must equal Path.home() / 'projects'
  ```

- [ ] **AC-3**: `~user/path` expands to that user's home (best effort, Unix)
  ```bash
  # On Unix with a known user (e.g., root if exists)
  python -c "from shared.file_ops.path_utils import normalize_path; print(normalize_path('~root/foo'))"
  # Should not raise; may return unexpanded if user doesn't exist (os.path.expanduser behaviour)
  ```

- [ ] **AC-4**: Absolute paths unchanged
  ```bash
  python -c "from shared.file_ops.path_utils import normalize_path; print(normalize_path('/absolute/path'))"
  # Output: /absolute/path (resolved)
  ```

- [ ] **AC-5**: Relative paths without tilde become absolute via `resolve()`
  ```bash
  python -c "from shared.file_ops.path_utils import normalize_path; print(normalize_path('relative/path'))"
  # Output: <cwd>/relative/path (resolved)
  ```

- [ ] **AC-6**: `Path` objects with tilde are handled (if passed pre-expanded, they work; if passed unexpanded, they expand)
  ```bash
  python -c "from pathlib import Path; from shared.file_ops.path_utils import normalize_path; print(normalize_path(Path('~/test')))"
  # Must not raise TypeError
  ```

- [ ] **AC-7**: Cross-platform: on Windows, `~/path` expands to `%USERPROFILE%\path`
  - Verified in CI Windows runner or documented as tested locally.

### 4.2 Technical Acceptance

- [ ] **AC-8**: Implementation uses `os.path.expanduser()` (Python <3.11) or `Path.expanduser()` (Python ≥3.11) — **not** custom string parsing.
- [ ] **AC-9**: No `TypeError`, `AttributeError`, or other exceptions for any valid path string.
- [ ] **AC-10**: Function remains pure (no I/O side effects beyond `Path.home()` / `expanduser()`).
- [ ] **AC-11**: Type hints unchanged: `normalize_path(path: Union[str, Path]) -> Path`.

### 4.3 Quality Acceptance

- [ ] **AC-12**: New unit tests added to `backend/tests/unit/test_shared_file_ops.py` covering AC-1 through AC-6.
- [ ] **AC-13**: `ruff check . && ruff format . && mypy .` passes (from repo root).
- [ ] **AC-14**: `pytest --cov=backend` passes with no new warnings.
- [ ] **AC-15**: Existing tests in `backend/tests/unit/test_shared_file_ops.py` still pass.

### 4.4 `is_within_syncthing_root()` Acceptance

- [ ] **AC-16**: `is_within_syncthing_root()` no longer contains a bare
      `except Exception:` — it catches only expected filesystem errors
      (`OSError`, `ValueError`) and returns `False`.
- [ ] **AC-17**: Those caught errors are logged at `DEBUG` with the offending
      path and the configured root, so "not within root" and "check failed"
      are distinguishable when reading logs.
- [ ] **AC-18**: Unexpected exceptions (`TypeError`, `AttributeError`,
      `RuntimeError`) **propagate** — they indicate a bug and must not be
      silently converted to `False`.
- [ ] **AC-19**: With `SYNCTHING_ROOT=~/syncthing`,
      `is_within_syncthing_root("~/syncthing/jobs/job-1")` returns `True` and
      `is_within_syncthing_root("~/other/path")` returns `False`, neither raising.
- [ ] **AC-20**: With `SYNCTHING_ROOT` unset, it returns `False` without
      touching the path.

---

## 5. Test Scenarios

### 5.1 `normalize_path()` Scenarios

| Scenario | Input | Expected Output | Notes |
|----------|-------|-----------------|-------|
| T1 | `"~"` | `Path.home()` | Bare tilde |
| T2 | `"~/projects"` | `Path.home() / "projects"` | Standard tilde-prefix |
| T3 | `"~/a/b/c"` | `Path.home() / "a/b/c"` | Nested |
| T4 | `"~other/foo"` | `os.path.expanduser("~other/foo")` | Other user (Unix) |
| T5 | `"/absolute/path"` | `Path("/absolute/path").resolve()` | Absolute, no tilde |
| T6 | `"relative/path"` | `Path("relative/path").resolve()` | Relative, no tilde |
| T7 | `Path("~/test")` | `Path.home() / "test"` | Path object with tilde |
| T8 | `Path("/abs")` | `Path("/abs").resolve()` | Path object absolute |
| T9 | `""` (empty) | `Path.cwd()` | Edge case: empty string → cwd |
| T10 | `"."` | `Path.cwd()` | Current directory |
| T11 | `".."` | `Path.cwd().parent` | Parent directory |
| T12 | Windows: `"~\\path"` | `%USERPROFILE%\\path` | Windows separator (if testable) |

**Test implementation note**: Tests T1–T3, T5–T7, T9–T11 must pass on all platforms. T4 is Unix-only. T12 is Windows-only.

**Note on T5/T8**: as written they compare against `Path(...).resolve()`, which is
Linux-shaped. On Windows `Path("/absolute/path")` has no drive letter while its
`resolve()` does (`E:\absolute\path`), so these two assertions **fail on
Windows** even when the code is correct. The shipped tests assert `is_absolute()`
plus a tail match instead, which holds on both platforms.

### 5.2 `is_within_syncthing_root()` Scenarios

| Scenario | Input | Expected Output | Notes |
|----------|-------|-----------------|-------|
| R1 | `SYNCTHING_ROOT=~/syncthing`, path `~/syncthing/jobs/job-1` | `True` | Tilde works end to end (AC-19) |
| R2 | `SYNCTHING_ROOT=~/syncthing`, path `~/other/path` | `False` | Outside the root, no raise |
| R3 | `SYNCTHING_ROOT` unset, any path | `False` | Early return, path untouched (AC-20) |
| R4 | `SYNCTHING_ROOT` set, argument is not a path | raises `TypeError` | AC-18: bugs must not be masked |

> R4 cannot be written as a null-byte path: whether `pathlib` raises on an
> embedded `\0` is platform- and version-dependent, and on Windows it does not.
> Pass an object whose `__str__` raises `TypeError` instead — that is
> platform-independent and still proves the exception escapes the narrow catch.

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `os.path.expanduser()` behaves differently on Windows vs Unix for `~user/` | Medium | Low | Document that `~user/` is best-effort; primary use case is `~/` for current user. |
| `Path.expanduser()` (3.11+) vs `os.path.expanduser()` discrepancy | Low | Low | Use `os.path.expanduser()` universally for consistency; it works on all supported Python versions (3.11+). |
| `Path.resolve()` on non-existent paths raises `FileNotFoundError` (strict mode) | Low | Medium | Current code uses `path_obj.resolve()` without `strict=False` (default). On Python 3.6+, `resolve()` does not require existence. Verify and keep as-is. |
| Callers that already work around the bug (e.g., `agent/paths.py`) double-expand | Low | Low | `expanduser()` is idempotent: `expanduser(expanduser("~/x"))` == `expanduser("~/x")`. No harm. |
| Symlink resolution change in `resolve()` breaks existing expectations | Low | Medium | Behaviour unchanged — `resolve()` is already called. No new risk. |
| Performance regression from extra `expanduser()` call | None | N/A | Negligible; `expanduser()` is a fast C function. |

---

## 7. Implementation Approach (for SWE Reference)

### 7.1 Minimal Fix
Replace the custom tilde-expansion block (lines 23–28) with a single `expanduser()` call **before** the absolute-path logic.

**File**: `shared/file_ops/path_utils.py`

**Before**:
```python
def normalize_path(path: Union[str, Path]) -> Path:
    path_obj = Path(path)

    # Expand user home directory (~)
    if path_obj == Path("~"):
        path_obj = Path.home()
    elif str(path_obj).startswith("~/"):
        path_str = str(path_obj)
        path_obj = Path.home() / path_str[2:]

    # Make absolute if it's not already
    if not path_obj.is_absolute():
        path_obj = path_obj.resolve()

    return path_obj
```

**After**:
```python
def normalize_path(path: Union[str, Path]) -> Path:
    """
    Normalize a path to a Path object, resolving ~ and making absolute if needed.

    Args:
        path: Path string or Path object

    Returns:
        Normalized Path object
    """
    # Convert to string first to handle both str and Path inputs uniformly
    path_str = str(path)

    # Expand ~ and ~user/ using the standard library (cross-platform)
    expanded = os.path.expanduser(path_str)

    # Convert to Path and resolve to absolute
    path_obj = Path(expanded)
    if not path_obj.is_absolute():
        path_obj = path_obj.resolve()

    return path_obj
```

**Why this works**:
- `os.path.expanduser()` handles `~`, `~/`, and `~user/` correctly on all platforms.
- It returns a string, so `Path(expanded)` creates a clean `Path` object.
- `is_absolute()` + `resolve()` logic is preserved for relative paths.
- No subscripting of `Path` objects — eliminates the `TypeError`.

### 7.2 Alternative (Python 3.11+ only)
If the project requires Python ≥3.11, use `Path(path).expanduser()` directly:
```python
path_obj = Path(path).expanduser()
if not path_obj.is_absolute():
    path_obj = path_obj.resolve()
return path_obj
```
**Decision**: Use `os.path.expanduser()` for maximum compatibility (project runs on Python 3.11+ per `pyproject.toml`, but `os.path.expanduser()` is clearer and equally fast).

### 7.3 Secondary fix: narrow the `except` in `is_within_syncthing_root()`

Replace the bare `except Exception:` with an explicit catch plus a debug log,
so AC-16 through AC-18 hold:

```python
    except (OSError, ValueError) as e:
        # Expected filesystem / invalid-path errors: log for debugging, report
        # "not within root". TypeError and AttributeError deliberately propagate
        # — they indicate a bug, not a path outside the root.
        logger.debug(
            "Error checking path %s against Syncthing root %s: %s",
            path,
            syncthing_root,
            e,
        )
        return False
```

`logging` must be imported and a module-level `logger` created. `%s`-style
lazy formatting is used throughout the repo.

> Do **not** include `RuntimeError` in the tuple. `RuntimeError` is what
> `Path.expanduser()` raises when it cannot determine a home directory; with the
> `os.path.expanduser()` implementation chosen in §7.1 it cannot arise, and
> swallowing it would re-mask a real bug.

### 7.4 Files to Modify
1. `shared/file_ops/path_utils.py` — replace lines 21–34 with the fixed implementation; narrow the `except` in `is_within_syncthing_root()`.
2. `backend/tests/unit/test_shared_file_ops.py` — add scenarios T1–T12 and R1–R4.

### 7.5 Verification Steps
```bash
# 1. Run new unit tests
pytest backend/tests/unit/test_shared_file_ops.py::TestNormalizePath -v

# 2. Run all shared file ops tests
pytest backend/tests/unit/test_shared_file_ops.py -v

# 3. Lint & type check
ruff check . && ruff format . && mypy .

# 4. Full backend test suite
pytest --cov=backend
```

---

## 8. Open Questions from Issue

| Question | Resolution |
|----------|------------|
| Should `~user/` be supported? | **Yes** — delegate to `os.path.expanduser()` which handles it on Unix. On Windows it returns the path unchanged (per stdlib behaviour). Document as known limitation. |
| What about `Path` objects created with tilde (e.g., `Path("~/x")`)? | **Handled** — `str(Path("~/x"))` returns `"~/x"` (or `"~\\x"` on Windows), which `expanduser()` processes correctly. |
| Should we use `Path.expanduser()` (3.11+) instead? | **No** — `os.path.expanduser()` is available on all supported versions and has identical behaviour. Simpler to maintain one code path. |
| Does this affect `ensure_directory()` and other helpers? | **Yes, positively** — they call `normalize_path()`, so they automatically gain correct tilde expansion. No changes needed. |
| Is `resolve()` the right choice for making paths absolute? | **Preserve current behaviour** — `resolve()` follows symlinks. If symlink preservation is needed later, that's a separate issue. |

### 8.1 Resolved during merge: `Path.expanduser()` vs `os.path.expanduser()`

The two grooming passes disagreed (§7.1 says `os.path.expanduser()`, §7.2's
alternative says `Path.expanduser()`). **Resolved in favour of
`os.path.expanduser()`**, for the reasons in the §7.2 Decision line: one code
path for `str` and `Path` inputs, no version skew, and it is what the shipped
tests were written against. The merged code in `shared/file_ops/path_utils.py`
is the §7.1 implementation, with `is_within_syncthing_root()` from §7.3.

---

## 9. Spec Sign-off

- **PM**: ✅ Groomed — spec written, issue labelled `groomed`.
- **SWE**: ✅ Implemented — both grooming passes implemented independently; merged during conflict resolution.
- **QA**: (to verify against AC-1 through AC-20)
- **On-Call**: (if CI fails on merge)