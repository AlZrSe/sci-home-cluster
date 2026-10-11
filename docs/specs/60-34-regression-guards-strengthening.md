# Issue #60: Strengthen #34 Regression Guards

## Problem Statement

The regression tests added in issue #34 (database URL resolution CWD-independence) are weaker than the manual verification that accepted them. Two specific gaps exist:

1. **AC-3/AC-7 — No behavioural test of the migration path**: The alembic tests only check source text (AST parsing) but don't actually run `alembic upgrade head` from a temp CWD to verify the migration path works end-to-end.

2. **AC-4 — The import test cannot catch the `.shc` regression**: The `test_import_has_no_filesystem_side_effects` sets `SECRET_KEY` in the child process, which short-circuits `_resolve_secret_key()`, so key generation never runs. The test passes even if `DEFAULT_STATE_DIR` reverts to a relative `.shc` path.

---

## Gap 1: Alembic Migration Path Behavioural Test

### Current State

`backend/tests/unit/test_alembic_url.py` contains:
- `test_alembic_env_delegates_to_the_shared_resolver` — AST check that `env.py` calls `resolve_database_url()`, but would pass even if the result is ignored.
- `test_alembic_ini_carries_no_competing_relative_default` — String check on `alembic.ini`.
- `test_alembic_creates_the_parent_directory` — Unit test of the helper, not the CLI.

**Missing**: An actual subprocess test that runs `alembic upgrade head` from a temp CWD and verifies:
- The canonical database file (`data/scientific_home_cluster.db`) is created and migrated to head.
- The launch directory (temp CWD) stays empty — nothing written there.
- No stray `.db` files appear anywhere under the repo.

### Required Test

Add a new test in `backend/tests/unit/test_alembic_url.py` (or a new file `test_alembic_cli.py`):

```python
def test_alembic_upgrade_head_from_temp_cwd(tmp_path, monkeypatch):
    """
    AC-3/AC-7 behavioural: `alembic upgrade head` from any CWD migrates the
    canonical database and writes nothing to the launch directory.

    This is what QA validated by hand for #34 acceptance. The AST checks in
    test_alembic_env_delegates_to_the_shared_resolver are source-text guards
    only — they would pass if env.py called resolve_database_url() and ignored
    the result. This test pins the actual behaviour.
    """
    import subprocess
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parents[3]
    ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"
    CANONICAL_DB = REPO_ROOT / "data" / "scientific_home_cluster.db"

    # Clean slate: remove any existing canonical database and WAL files
    for suffix in ("", "-wal", "-shm"):
        (CANONICAL_DB.with_suffix(suffix)).unlink(missing_ok=True)

    # Ensure data/ directory exists (it's created lazily by the resolver)
    CANONICAL_DB.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    # Use a temp CWD that is NOT the repo root
    launch_dir = tmp_path / "launch-from-here"
    launch_dir.mkdir()

    # Run alembic upgrade head from the temp CWD
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        cwd=str(launch_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Alembic failed: {result.stderr}"

    # 1. Canonical database exists and is at head
    assert CANONICAL_DB.exists(), "Canonical database was not created"
    # Verify alembic_version table is at head
    import sqlite3
    with sqlite3.connect(CANONICAL_DB) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert version is not None, "alembic_version table missing"
    # Head revision as of #34 is '0003' (or whatever the latest is)
    # We just check it's not empty/missing; the exact value is in versions/
    assert version[0] != "", "alembic_version not set"

    # 2. Launch directory stayed empty (no relative default regression)
    assert list(launch_dir.iterdir()) == [], (
        f"Launch directory {launch_dir} was not empty: {list(launch_dir.iterdir())}"
    )

    # 3. No stray .db files anywhere under repo (except the canonical one)
    stray_dbs = list(REPO_ROOT.rglob("*.db"))
    stray_dbs = [p for p in stray_dbs if p != CANONICAL_DB]
    assert stray_dbs == [], f"Stray .db files appeared: {stray_dbs}"

    # Cleanup for test isolation
    for suffix in ("", "-wal", "-shm"):
        (CANONICAL_DB.with_suffix(suffix)).unlink(missing_ok=True)
```

### Why This Matters

- The `alembic.ini` uses `%(here)s` for `script_location` and `prepend_sys_path`, making it CWD-independent. This test verifies that property holds.
- A regression to a relative default in `alembic.ini` or `env.py` would write to the launch directory — this test catches it.
- The AST checks remain as cheap guards, but the docstring must state plainly: *"This is a source-text check, not behavioural evidence."*

---

## Gap 2: Import Side-Effect Test for `.shc` Regression

### Current State

`test_import_has_no_filesystem_side_effects` in `backend/tests/unit/test_config.py`:
- Runs `python -c "import backend.main; import backend.core.config"` in a subprocess.
- Sets `SECRET_KEY=test-key-not-persisted` in the child environment.
- This short-circuits `_resolve_secret_key()` — key generation never runs.
- If `DEFAULT_STATE_DIR` reverts to `Path(".shc")`, the test **still passes** because no key is generated.

### Required Fix

Modify `test_import_has_no_filesystem_side_effects` to:
- **Not** set `SECRET_KEY` in the child environment.
- Instead, set `SHC_STATE_DIR` to a temp directory so key generation happens but is contained.
- Assert the temp directory gets the key, and the launch directory stays empty.

```python
def test_import_has_no_filesystem_side_effects(tmp_path, monkeypatch):
    """
    AC-4: importing the application writes nothing into the CWD.

    This test MUST NOT set SECRET_KEY in the child env — doing so short-circuits
    _resolve_secret_key() and the test would pass even if DEFAULT_STATE_DIR
    reverted to the old relative ".shc" (which wrote ./.shc/secret_key into the
    launch directory). QA mutation-tested this: reverting DEFAULT_STATE_DIR to
    Path(".shc") and re-running ONLY this test still passed.

    Instead, we set SHC_STATE_DIR to a temp directory so key generation runs
    and is contained, then assert the launch directory stays empty.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parents[3]

    # Temp directory for the generated key (contained, not the launch dir)
    key_dir = tmp_path / "keys"
    key_dir.mkdir()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    # Do NOT set SECRET_KEY — let _resolve_secret_key() generate one
    env["SHC_STATE_DIR"] = str(key_dir)
    env.pop("DATABASE_URL", None)
    env.pop("SECRET_KEY", None)  # Explicitly ensure it's not set

    launch_dir = tmp_path / "launch-from-here"
    launch_dir.mkdir()

    result = subprocess.run(
        [sys.executable, "-c", "import backend.main; import backend.core.config"],
        cwd=str(launch_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    # Launch directory must be empty — no .shc/ created there
    assert list(launch_dir.iterdir()) == [], (
        f"Launch directory {launch_dir} was not empty: {list(launch_dir.iterdir())}"
    )
    assert not (launch_dir / ".shc").exists(), ".shc created in launch directory"

    # Key should have been generated in the designated SHC_STATE_DIR
    assert (key_dir / "secret_key").exists(), "Key not generated in SHC_STATE_DIR"
    assert (key_dir / "secret_key").stat().st_mode & 0o777 == 0o600, "Key not 0600"
```

### Why This Matters

- The test name claims to verify "no filesystem side effects on import" but the `SECRET_KEY` env var makes it vacuous for the `.shc` regression.
- `test_state_dir_independent_of_cwd` catches the regression, but the test named for the property should carry it.
- Docstring must document: *"This test is one careless `setenv` away from proving nothing. Do not add SECRET_KEY to the child env."*

---

## Acceptance Criteria

### For Gap 1 (Alembic CLI Test)
- [ ] A new subprocess test runs `alembic upgrade head` from a temp CWD.
- [ ] Asserts canonical database exists and `alembic_version` is at head.
- [ ] Asserts launch directory stayed empty.
- [ ] Asserts no stray `.db` files appeared under the repo.
- [ ] Docstring explains the AST checks are source-text only, not behavioural.
- [ ] Test placed so #57 flakes (database locked) cannot be mistaken for a regression here.

### For Gap 2 (Import Side-Effect Test)
- [ ] `test_import_has_no_filesystem_side_effects` no longer sets `SECRET_KEY` in child env.
- [ ] Sets `SHC_STATE_DIR` to a temp directory instead.
- [ ] Asserts key generated in `SHC_STATE_DIR`, launch directory empty.
- [ ] Mutation test: reverting `DEFAULT_STATE_DIR` to `Path(".shc")` makes **this test alone** fail (not only `test_state_dir_independent_of_cwd`).
- [ ] Docstring documents the vacuity risk and why `SECRET_KEY` must not be set.

### Documentation
- [ ] Both tests have docstrings explaining why they are easy to get vacuous.
- [ ] `test_alembic_env_delegates_to_the_shared_resolver` docstring updated to state it is a source-text check only.

---

## Implementation Notes

- These are **tests only** — no production code changes.
- Do not wait for #26 (CI wiring) — these can land independently.
- Place the alembic CLI test carefully: it opens its own DB connection, so #57 flakes (intermittent `database is locked`) should not be confused with a regression. Consider marking it with a custom pytest marker or placing it in a separate test file.
- The alembic test should clean up the canonical database after itself (or use a dedicated temp database via monkeypatch) to avoid polluting the developer's environment.
- For the alembic test, consider using a temp database path (via `monkeypatch.setattr(settings, "DATABASE_URL", ...)`) rather than the real canonical one, to avoid interfering with other tests or the developer's database. But the issue specifically asks to verify the *canonical* file is used — so use the real path but clean up aggressively.

---

## Related Files

- `backend/tests/unit/test_alembic_url.py` — Add/extend behavioural test
- `backend/tests/unit/test_config.py` — Fix `test_import_has_no_filesystem_side_effects`
- `backend/alembic/env.py` — Uses shared resolver (verified by new test)
- `backend/alembic.ini` — Uses `%(here)s` for CWD-independence
- `backend/core/config.py` — Contains `DEFAULT_STATE_DIR` and `_resolve_secret_key()`

---

## References

- Issue #34: Database URL must not depend on working directory
- Issue #56: Committed JWT signing keys (related to `.shc` default)
- Issue #57: Intermittent `database is locked` / post-summary hang
- Issue #26: CI wiring (backlog, not a blocker)