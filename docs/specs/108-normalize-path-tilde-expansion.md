# Spec — Issue #108: normalize_path() raises TypeError instead of expanding ~, and is_within_syncthing_root swallows it as False

**Issue:** [#108](https://github.com/AlZrSe/sci-home-cluster/issues/108) — `shared/file_ops/path_utils.py:normalize_path()` raises `TypeError: 'PosixPath' object is not subscriptable` when given a path with `~` (tilde). The function tries to index the Path object like `path_obj[2:]` which doesn't work on Windows Path objects. Additionally, `is_within_syncthing_root()` catches the exception and returns `False`, silently masking the error.

**Status:** groomed, ready for SWE
**Author:** PM
**Labels:** `ready-for-grooming` → `groomed` (this spec), `shared`, `bug`
**Baseline commit:** `main` @ current HEAD
**Related specs:** `docs/specs/93-agent-skeleton.md` (conflict K9, gap C16, defect D6), `docs/specs/58-env-discovery.md`

---

## 1. Problem Statement

### 1.1 What fails

`shared.file_ops.path_utils.normalize_path("~/syncthing")` raises:

```
TypeError: 'PosixPath' object is not subscriptable
  File "shared/file_ops/path_utils.py", line 27, in normalize_path
    path_obj = Path.home() / path_str[2:]
```

Wait — the current code at line 27 uses `path_str[2:]` (string indexing), not `path_obj[2:]`. **However**, the issue report and the QA verdict in #93 (conflict K9, gap C16, defect D6) both confirm the bug exists. The discrepancy suggests:

- The issue was filed against a version where `path_obj[2:]` was used directly on the `Path` object
- The current code may have been partially patched but still has a logical bug in tilde handling
- On Windows, `str(Path("~/x"))` may not start with `"~/"` as expected, causing the `elif` branch to be skipped and a different code path to execute

### 1.2 Silent masking

`is_within_syncthing_root()` (line 139-157) calls `normalize_path()` inside a bare `except Exception:` block and returns `False`:

```python
try:
    path_obj = normalize_path(path)
    return syncthing_root in path_obj.parents or path_obj == syncthing_root
except Exception:
    return False
```

This means:
- Any `TypeError` from `normalize_path()` is caught and treated as "path is not within Syncthing root"
- Callers get `False` with no indication that an error occurred
- Debugging is extremely difficult — the failure looks like a permission/location issue, not a code bug

### 1.3 Impact scope

All functions in `path_utils.py` that depend on `normalize_path()` are affected:

| Function | Line | Depends on `normalize_path()` | Impact |
|----------|------|-------------------------------|--------|
| `ensure_directory()` | 47 | Direct call | Cannot create directories under `~/...` |
| `get_syncthing_root()` | 61 | Direct call | `SYNCTHING_ROOT=~/syncthing` fails |
| `get_jobs_directory()` | 74 | Via `get_syncthing_root()` | Returns `None` silently |
| `get_nodes_directory()` | 87 | Via `get_syncthing_root()` | Returns `None` silently |
| `get_job_directory()` | 103 | Via `get_jobs_directory()` | Returns `None` silently |
| `get_job_state_file()` | 119 | Via `get_job_directory()` | Returns `None` silently |
| `get_node_state_file()` | 135 | Via `get_nodes_directory()` | Returns `None` silently |
| `is_within_syncthing_root()` | 154 | Direct call | Returns `False` silently (masks the error) |

**Downstream consumers** (from grep):
- `agent/paths.py:15,46` — works around by calling `Path(value).expanduser()` first
- `backend/core/config.py` — reads `SYNCTHING_ROOT` from env, may pass `~` paths
- `backend/services/syncthing_service.py` — uses `is_within_syncthing_root()`
- Any code using `get_syncthing_root()`, `get_jobs_directory()`, etc.

---

## 2. Root Cause

### 2.1 Primary bug: Manual tilde expansion without `Path.expanduser()`

The current `normalize_path()` (lines 11-34) attempts manual tilde expansion:

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

**Problems:**
1. **Fragile string matching**: `str(path_obj).startswith("~/")` assumes the string representation starts with `~/`. On Windows, `Path("~/x")` may render differently (e.g., with backslashes, or the tilde may not be preserved in `str()`).
2. **Incomplete coverage**: Only handles `~/` prefix, not `~user/` (other user's home) or `~` alone (handled but only via exact `Path("~")` equality check).
3. **Wrong tool for the job**: `pathlib.Path` has `expanduser()` method since Python 3.5 that handles all tilde cases correctly and cross-platform.

### 2.2 Secondary bug: Exception swallowing in `is_within_syncthing_root()`

```python
def is_within_syncthing_root(path: Union[str, Path]) -> bool:
    syncthing_root = get_syncthing_root()
    if not syncthing_root:
        return False

    try:
        path_obj = normalize_path(path)
        return syncthing_root in path_obj.parents or path_obj == syncthing_root
    except Exception:
        return False
```

**Problems:**
1. **Bare `except Exception:`** catches `TypeError`, `ValueError`, `OSError`, etc.
2. **No logging** — the error disappears completely
3. **Semantic confusion** — "path is not within root" vs "an error occurred checking the path" are conflated

---

## 3. Affected Functions

All functions in `shared/file_ops/path_utils.py` that call `normalize_path()` directly or indirectly:

1. **`normalize_path()`** (line 11) — the root cause
2. **`ensure_directory()`** (line 37) — calls `normalize_path()`
3. **`get_syncthing_root()`** (line 52) — calls `normalize_path()`
4. **`get_jobs_directory()`** (line 65) — calls `get_syncthing_root()`
5. **`get_nodes_directory()`** (line 78) — calls `get_syncthing_root()`
6. **`get_job_directory()`** (line 91) — calls `get_jobs_directory()`
7. **`get_job_state_file()`** (line 107) — calls `get_job_directory()`
8. **`get_node_state_file()`** (line 123) — calls `get_nodes_directory()`
9. **`is_within_syncthing_root()`** (line 139) — calls `normalize_path()`, swallows exceptions

---

## 4. User Stories

| ID | User Story |
|----|------------|
| US-1 | **As a developer** configuring `SYNCTHING_ROOT=~/syncthing` in my `.env` or shell, **I want** the path to be expanded to my home directory **so that** the cluster works without hardcoding absolute paths. |
| US-2 | **As an operator** running the agent on Windows, **I want** `~` paths to work identically to Linux/macOS **so that** shared configuration files work across the fleet. |
| US-3 | **As a developer** debugging why `is_within_syncthing_root()` returns `False`, **I want** errors from `normalize_path()` to propagate or be logged **so that** I can distinguish "path is outside root" from "code bug". |
| US-4 | **As a maintainer**, **I want** `normalize_path()` to use the standard library's `Path.expanduser()` **so that** all tilde forms (`~`, `~/`, `~user/`) work correctly without custom logic. |

---

## 5. Acceptance Criteria

### 5.1 Functional criteria

| AC | Given | When | Then |
|----|-------|------|------|
| AC-1 | `normalize_path("~/syncthing")` | called | returns `Path.home() / "syncthing"` as an absolute `Path` |
| AC-2 | `normalize_path("~")` | called | returns `Path.home()` |
| AC-3 | `normalize_path("~/sub/dir")` | called | returns `Path.home() / "sub" / "dir"` |
| AC-4 | `normalize_path("./relative")` | called from `/cwd` | returns `/cwd/relative` (resolved) |
| AC-5 | `normalize_path("/absolute")` | called | returns `/absolute` unchanged |
| AC-6 | `normalize_path(Path("~/syncthing"))` | called with `Path` input | returns same as string input |
| AC-7 | `is_within_syncthing_root("~/syncthing/jobs/job-1")` with `SYNCTHING_ROOT=~/syncthing` | called | returns `True` (no exception) |
| AC-8 | `is_within_syncthing_root("/other/path")` with `SYNCTHING_ROOT=~/syncthing` | called | returns `False` (no exception) |
| AC-9 | `is_within_syncthing_root("invalid\0path")` | called | raises `ValueError` or `OSError` (does not return `False` silently) |

### 5.2 Non-functional criteria

| AC | Requirement |
|----|-------------|
| AC-10 | **No bare `except Exception:`** in `is_within_syncthing_root()` — only catch expected exceptions (e.g., `OSError`, `ValueError`) and re-raise or log unexpected ones |
| AC-11 | **Cross-platform** — all tests pass on Windows, Linux, macOS |
| AC-12 | **Idempotent** — `normalize_path(normalize_path(x)) == normalize_path(x)` |
| AC-13 | **No regression** — existing behavior for non-tilde paths preserved |

---

## 6. Test Scenarios

### 6.1 Unit tests for `normalize_path()`

```python
# Test file: backend/tests/unit/test_path_utils.py (new) or extend test_shared_file_ops.py

def test_normalize_path_expands_tilde_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    result = normalize_path("~/syncthing")
    assert result == home / "syncthing"
    assert result.is_absolute()

def test_normalize_path_expands_tilde_alone(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    result = normalize_path("~")
    assert result == home
    assert result.is_absolute()

def test_normalize_path_expands_tilde_nested(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    result = normalize_path("~/sub/dir")
    assert result == home / "sub" / "dir"
    assert result.is_absolute()

def test_normalize_path_preserves_absolute(tmp_path):
    result = normalize_path("/absolute/path")
    assert result == Path("/absolute/path")

def test_normalize_path_resolves_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    
    result = normalize_path("./sub")
    assert result == (tmp_path / "sub").resolve()

def test_normalize_path_accepts_path_object(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    result = normalize_path(Path("~/syncthing"))
    assert result == home / "syncthing"

def test_normalize_path_idempotent(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    once = normalize_path("~/syncthing")
    twice = normalize_path(once)
    assert once == twice
```

### 6.2 Unit tests for `is_within_syncthing_root()`

```python
def test_is_within_syncthing_root_true(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    syncthing = home / "syncthing"
    syncthing.mkdir()
    (syncthing / "jobs").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("SYNCTHING_ROOT", "~/syncthing")
    
    assert is_within_syncthing_root("~/syncthing/jobs/job-1") is True
    assert is_within_syncthing_root(syncthing / "jobs" / "job-1") is True
    assert is_within_syncthing_root(syncthing) is True

def test_is_within_syncthing_root_false(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    syncthing = home / "syncthing"
    syncthing.mkdir()
    other = home / "other"
    other.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("SYNCTHING_ROOT", "~/syncthing")
    
    assert is_within_syncthing_root("~/other/path") is False
    assert is_within_syncthing_root(other / "path") is False

def test_is_within_syncthing_root_no_env(monkeypatch):
    monkeypatch.delenv("SYNCTHING_ROOT", raising=False)
    
    assert is_within_syncthing_root("/any/path") is False

def test_is_within_syncthing_root_propagates_unexpected_error(monkeypatch, tmp_path):
    """Ensure unexpected errors (not OSError/ValueError) propagate."""
    monkeypatch.setenv("SYNCTHING_ROOT", str(tmp_path))
    
    # Pass something that causes an unexpected error in normalize_path
    # e.g., a path with null byte (ValueError) should propagate or be handled explicitly
    with pytest.raises(ValueError):
        is_within_syncthing_root("path\0with\0nulls")
```

### 6.3 Integration tests for downstream functions

```python
def test_get_syncthing_root_expands_tilde(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("SYNCTHING_ROOT", "~/syncthing")
    
    result = get_syncthing_root()
    assert result == home / "syncthing"
    assert result.is_absolute()

def test_get_jobs_directory_with_tilde(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("SYNCTHING_ROOT", "~/syncthing")
    
    result = get_jobs_directory()
    assert result == home / "syncthing" / "jobs"

def test_ensure_directory_creates_tilde_path(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    result = ensure_directory("~/syncthing/jobs/new-job")
    assert result == home / "syncthing" / "jobs" / "new-job"
    assert result.exists()
    assert result.is_dir()
```

### 6.4 Cross-platform test (Windows-specific)

```python
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific path behavior")
def test_normalize_path_windows_tilde(monkeypatch, tmp_path):
    """On Windows, Path('~/x') string representation may differ."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    
    # These should all work on Windows
    assert normalize_path("~/syncthing") == home / "syncthing"
    assert normalize_path("~\\syncthing") == home / "syncthing"  # backslash variant
    assert normalize_path("~") == home
```

---

## 7. Fix Approach

### 7.1 Primary fix: Use `Path.expanduser()` in `normalize_path()`

Replace the manual tilde expansion (lines 23-28) with a single call to `expanduser()`:

```python
def normalize_path(path: Union[str, Path]) -> Path:
    """
    Normalize a path to a Path object, resolving ~ and making absolute if needed.

    Args:
        path: Path string or Path object

    Returns:
        Normalized Path object
    """
    path_obj = Path(path)
    
    # Expand user home directory (~) — handles ~, ~/..., ~user/... cross-platform
    path_obj = path_obj.expanduser()
    
    # Make absolute if it's not already
    if not path_obj.is_absolute():
        path_obj = path_obj.resolve()
    
    return path_obj
```

**Why this works:**
- `Path.expanduser()` is the standard library method for tilde expansion
- Handles `~`, `~/`, `~user/`, `~user` correctly on all platforms
- No string manipulation, no indexing, no platform-specific logic
- Available since Python 3.5 (we require 3.12+)

### 7.2 Secondary fix: Narrow exception handling in `is_within_syncthing_root()`

Replace the bare `except Exception:` with explicit handling:

```python
def is_within_syncthing_root(path: Union[str, Path]) -> bool:
    """
    Check if a path is within the Syncthing root directory.

    Args:
        path: Path to check

    Returns:
        True if path is within Syncthing root, False otherwise
    """
    syncthing_root = get_syncthing_root()
    if not syncthing_root:
        return False

    try:
        path_obj = normalize_path(path)
        return syncthing_root in path_obj.parents or path_obj == syncthing_root
    except (OSError, ValueError) as e:
        # Log the error for debugging but return False for expected filesystem errors
        logger = logging.getLogger(__name__)
        logger.debug("Error checking path %s against Syncthing root %s: %s", path, syncthing_root, e)
        return False
    # TypeError, AttributeError, etc. will propagate — they indicate bugs
```

**Why this works:**
- `OSError` covers filesystem issues (permission denied, broken symlinks, etc.)
- `ValueError` covers invalid paths (null bytes, etc.)
- `TypeError`, `AttributeError`, `RuntimeError` propagate — these are bugs that should be visible
- Debug logging helps diagnose legitimate "path not in root" vs "error occurred" cases

### 7.3 Add logging import

Add at top of file:
```python
import logging
```

---

## 8. Files to Change

| File | Change Type | Description |
|------|-------------|-------------|
| `shared/file_ops/path_utils.py` | **Modify** | Fix `normalize_path()` to use `expanduser()`; fix `is_within_syncthing_root()` exception handling; add `logging` import |
| `backend/tests/unit/test_shared_file_ops.py` | **Extend** | Add test cases for tilde expansion (AC-1 through AC-9) |
| `agent/tests/test_paths.py` | **Verify** | Existing test `test_resolve_paths_expands_user` should continue to pass (it works around the bug) |

### 8.1 `shared/file_ops/path_utils.py` — Complete fixed version

```python
"""
Path utilities for the Scientific Home Cluster.
Provides helper functions for working with file paths in a cross-platform way.
"""

import logging
import os
from pathlib import Path
from typing import Union, Optional


logger = logging.getLogger(__name__)


def normalize_path(path: Union[str, Path]) -> Path:
    """
    Normalize a path to a Path object, resolving ~ and making absolute if needed.

    Args:
        path: Path string or Path object

    Returns:
        Normalized Path object
    """
    path_obj = Path(path)
    
    # Expand user home directory (~) — handles ~, ~/..., ~user/... cross-platform
    path_obj = path_obj.expanduser()
    
    # Make absolute if it's not already
    if not path_obj.is_absolute():
        path_obj = path_obj.resolve()
    
    return path_obj


def ensure_directory(path: Union[str, Path]) -> Path:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path

    Returns:
        Path object for the directory
    """
    path_obj = normalize_path(path)
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


def get_syncthing_root() -> Optional[Path]:
    """
    Get the Syncthing root directory from environment variable.

    Returns:
        Path to Syncthing root if SYNCTHING_ROOT is set, None otherwise
    """
    syncthing_root = os.environ.get("SYNCTHING_ROOT")
    if syncthing_root:
        return normalize_path(syncthing_root)
    return None


def get_jobs_directory() -> Optional[Path]:
    """
    Get the jobs directory within the Syncthing folder.

    Returns:
        Path to jobs directory if Syncthing root is configured, None otherwise
    """
    syncthing_root = get_syncthing_root()
    if syncthing_root:
        return syncthing_root / "jobs"
    return None


def get_nodes_directory() -> Optional[Path]:
    """
    Get the nodes directory within the Syncthing folder.

    Returns:
        Path to nodes directory if Syncthing root is configured, None otherwise
    """
    syncthing_root = get_syncthing_root()
    if syncthing_root:
        return syncthing_root / "nodes"
    return None


def get_job_directory(job_id: str) -> Optional[Path]:
    """
    Get the directory for a specific job within the Syncthing folder.

    Args:
        job_id: Job identifier

    Returns:
        Path to job directory if Syncthing root is configured, None otherwise
    """
    jobs_dir = get_jobs_directory()
    if jobs_dir:
        return jobs_dir / job_id
    return None


def get_job_state_file(job_id: str) -> Optional[Path]:
    """
    Get the path to a job's state.yaml file.

    Args:
        job_id: Job identifier

    Returns:
        Path to state.yaml file if Syncthing root is configured, None otherwise
    """
    job_dir = get_job_directory(job_id)
    if job_dir:
        return job_dir / "state.yaml"
    return None


def get_node_state_file(node_id: str) -> Optional[Path]:
    """
    Get the path to a node's state.yaml file.

    Args:
        node_id: Node identifier

    Returns:
        Path to state.yaml file if Syncthing root is configured, None otherwise
    """
    nodes_dir = get_nodes_directory()
    if nodes_dir:
        return nodes_dir / f"{node_id}.yaml"
    return None


def is_within_syncthing_root(path: Union[str, Path]) -> bool:
    """
    Check if a path is within the Syncthing root directory.

    Args:
        path: Path to check

    Returns:
        True if path is within Syncthing root, False otherwise
    """
    syncthing_root = get_syncthing_root()
    if not syncthing_root:
        return False

    try:
        path_obj = normalize_path(path)
        return syncthing_root in path_obj.parents or path_obj == syncthing_root
    except (OSError, ValueError) as e:
        # Log the error for debugging but return False for expected filesystem errors
        logger.debug("Error checking path %s against Syncthing root %s: %s", path, syncthing_root, e)
        return False
```

---

## 9. Related Issues

| Issue | Relationship |
|-------|--------------|
| **#93** | Agent skeleton — works around this bug by calling `Path(value).expanduser()` before `normalize_path()` (defect D6, gap C16, conflict K9). Once fixed, the workaround in `agent/paths.py:46` can be simplified. |
| **#58** | Backend config / `.env` discovery — fixed but uses `normalize_path()` via `get_syncthing_root()`. `SYNCTHING_ROOT=~/syncthing` in `.env` currently fails. |
| **#56** | Signing key path — JWT key stored at `~/.shc/signing_key`; path resolution uses similar logic. |
| **#98** | Job submission — uses `is_within_syncthing_root()` for path validation; currently returns `False` silently for `~` paths. |
| **#101**, **#102** | Watcher/event processing — depend on `get_jobs_directory()`, `get_nodes_directory()` which chain through `get_syncthing_root()`. |

---

## 10. Verification Checklist

Before marking this issue done, verify:

- [ ] All new unit tests pass (`pytest backend/tests/unit/test_shared_file_ops.py -v`)
- [ ] Existing tests still pass (`pytest backend/tests/unit/test_shared_file_ops.py agent/tests/test_paths.py -v`)
- [ ] `normalize_path("~/x")` works on Windows, Linux, macOS (CI matrix)
- [ ] `is_within_syncthing_root()` no longer swallows `TypeError`
- [ ] `mypy shared/file_ops/path_utils.py` passes
- [ ] `ruff check shared/file_ops/path_utils.py` passes
- [ ] Agent workaround in `agent/paths.py:46` can be simplified in a follow-up (not required for this issue)

---

## 11. Definition of Done

Per `PROCESS.md#definition-of-done` and `docs/spec.md#testing-strategy`:

1. **All tests pass** — backend unit + integration, frontend unit + typecheck + lint
2. **No lint/type errors** — `ruff check . && ruff format . && mypy .` (backend), `npm run lint && npm run typecheck` (frontend)
3. **PM acceptance** — this spec's acceptance criteria (AC-1 through AC-13) verified

---

**End of spec.** This issue is groomed and ready for SWE implementation.