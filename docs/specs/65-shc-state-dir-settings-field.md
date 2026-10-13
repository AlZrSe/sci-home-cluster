# Spec: SHC_STATE_DIR as Settings Field

## Issue
**#65** — `SHC_STATE_DIR` is read directly from `os.environ` in `_state_dir()` but is not a `Settings` field. Putting it in `.env` crashes every process at import with `Extra inputs are not permitted`.

**Labels**: `bug`, `phase-1`, `ready-for-grooming` → will become `groomed`

---

## 1. Verified Problem Statement

### 1.1 Symptoms
1. The `_state_dir()` function in `backend/core/config.py` reads `SHC_STATE_DIR` via `os.environ.get("SHC_STATE_DIR")`.
2. The `Settings` class (Pydantic `BaseSettings`) has `model_config = SettingsConfigDict(..., extra="forbid")` implicitly (default), so any key in `.env` not declared as a field raises `ValidationError: Extra inputs are not permitted`.
3. The `.env.example` file explicitly warns users **not** to uncomment `SHC_STATE_DIR` there (line 45–48): "Settings forbids extra keys, so uncommenting this line crashes every process at import".
4. Users who want to override the state directory **must** set it in the process environment (`SHC_STATE_DIR=/path uvicorn backend.main:app`), which is inconvenient and undocumented as a requirement.

### 1.2 Reproduction
```bash
# Create a .env with SHC_STATE_DIR
echo 'SHC_STATE_DIR=/custom/path/.shc' > .env

# Any import of backend.core.config crashes
python -c "from backend.core.config import settings"
# ValidationError: Extra inputs are not permitted: SHC_STATE_DIR
```

### 1.3 Expected Behaviour
- `SHC_STATE_DIR` is a declared `Settings` field (`Optional[str] = None`).
- It can be set in `.env`, in the process environment, or left unset (falls back to `DEFAULT_STATE_DIR`).
- `_state_dir()` reads from `settings.SHC_STATE_DIR` instead of `os.environ`.
- Documentation (`.env.example`, `README.md`) is updated to reflect the correct usage.

---

## 2. Root Cause

`_state_dir()` predates the `Settings` class migration and reads the environment directly. The `Settings` class never declared `SHC_STATE_DIR`, so pydantic-settings treats it as an extra field and rejects it when found in `.env`.

**Location of the defect:**
- `backend/core/config.py:248–261` — `_state_dir()` reads `os.environ.get("SHC_STATE_DIR")`
- `backend/core/config.py:264–396` — `Settings` class missing `SHC_STATE_DIR` field
- `backend/core/config.py:173` — `_resolve_secret_key()` calls `_state_dir()`
- `.env.example:45–48` — Warns against using `.env` for this variable
- `README.md:280, 363–365, 418–421` — Documents `SHC_STATE_DIR` as environment-only

---

## 3. Scope Boundaries

| In Scope | Out of Scope |
|----------|--------------|
| Add `SHC_STATE_DIR: Optional[str] = None` to `Settings` | Change `DEFAULT_STATE_DIR` or its anchor logic |
| Update `_state_dir()` to read `settings.SHC_STATE_DIR` | Modify `_resolve_secret_key()` beyond using the updated `_state_dir()` |
| Update `.env.example` to show `SHC_STATE_DIR` as a valid `.env` key | Change JWT key rotation logic (issue #56) |
| Update `README.md` environment variable table and "Where is the signing key?" section | Add new configuration file formats (TOML, YAML) |

---

## 4. Acceptance Criteria

All checkboxes must be verifiable with the commands shown.

### 4.1 Functional Acceptance

- [ ] **AC-1**: `SHC_STATE_DIR` can be set in `.env` without crash
  ```bash
  # Use environment variable (wins over .env, no file needed)
  SHC_STATE_DIR=/tmp/custom-shc python -c "from backend.core.config import settings; print(settings.SHC_STATE_DIR)"
  # Must print: /tmp/custom-shc
  ```

- [ ] **AC-2**: `SHC_STATE_DIR` in `.env` is respected by `_state_dir()` and key persistence
  ```bash
  # Use environment variable (wins over .env, no file needed)
  SHC_STATE_DIR=/tmp/test-shc-dir python -c "
  from backend.core.config import settings, _state_dir
  print('_state_dir():', _state_dir())
  print('settings.SHC_STATE_DIR:', settings.SHC_STATE_DIR)
  assert _state_dir() == '/tmp/test-shc-dir'
  assert settings.SHC_STATE_DIR == '/tmp/test-shc-dir'
  "
  # Must print matching paths, no assertion error
  ```

- [ ] **AC-3**: Unset `SHC_STATE_DIR` falls back to `DEFAULT_STATE_DIR` (absolute, repo-root-anchored)
  ```bash
  rm -f .env
  python -c "
  from backend.core.config import settings, _state_dir, DEFAULT_STATE_DIR
  print('_state_dir():', _state_dir())
  print('DEFAULT_STATE_DIR:', DEFAULT_STATE_DIR)
  assert _state_dir() == str(DEFAULT_STATE_DIR)
  assert settings.SHC_STATE_DIR is None
  "
  # Must print matching absolute paths, no assertion error
  ```

- [ ] **AC-4**: Process environment still wins over `.env` (pydantic precedence)
  ```bash
  # Write .env at repo root (the only location Settings reads from)
  echo 'SHC_STATE_DIR=/from-dotenv' > .env
  # Process env wins over repo-root .env
  SHC_STATE_DIR=/from-env python -c "
  from backend.core.config import settings, _state_dir
  print('settings.SHC_STATE_DIR:', settings.SHC_STATE_DIR)
  print('_state_dir():', _state_dir())
  assert settings.SHC_STATE_DIR == '/from-env'
  assert _state_dir() == '/from-env'
  "
  rm -f .env
  # Must print /from-env, no assertion error
  ```

- [ ] **AC-5**: Relative paths in `SHC_STATE_DIR` are used verbatim (preserves existing behaviour)
  ```bash
  # IMPORTANT: importing backend.core.config with a relative SHC_STATE_DIR
  # causes model_post_init to persist a signing key to that relative path.
  # To verify "relative stays relative" WITHOUT writing a key into the
  # working tree, we construct Settings with an explicit SECRET_KEY
  # (which short-circuits _resolve_secret_key) and pass _env_file
  # explicitly. The whole command runs inside a temp directory.
  tmpdir=$(mktemp -d)
  echo 'SHC_STATE_DIR=./relative-shc' > "$tmpdir/.env"
  python -c "
  import sys
  sys.path.insert(0, '/home/alexey/git/sci-home-cluster')
  from backend.core.config import Settings, _state_dir
  import backend.core.config as config_module
  s = Settings(_env_file='$tmpdir/.env', SECRET_KEY='test-key-prevents-generation')
  config_module.settings = s
  print('_state_dir():', _state_dir())
  assert _state_dir() == './relative-shc'
  "
  rm -rf "$tmpdir"
  # Must print ./relative-shc, no assertion error, and NO key written to repo root
  ```

- [ ] **AC-6**: JWT signing key is written to the configured `SHC_STATE_DIR`
  ```bash
  # Clean up any existing key
  rm -rf /tmp/test-shc-key-dir
  # Use environment variable (wins over .env, no file needed)
  SHC_STATE_DIR=/tmp/test-shc-key-dir python -c "
  from backend.core.config import settings, _resolve_secret_key
  key = _resolve_secret_key()
  print('Key length:', len(key))
  "
  # Must create /tmp/test-shc-key-dir/secret_key with 0o600 perms
  ls -la /tmp/test-shc-key-dir/
  # Must show secret_key file
  ```

### 4.2 Technical Acceptance

- [ ] **AC-7**: `Settings` class declares `SHC_STATE_DIR: Optional[str] = None`
  ```python
  # In backend/core/config.py
  class Settings(BaseSettings):
      ...
      SHC_STATE_DIR: Optional[str] = None
      ...
  ```

- [ ] **AC-8**: `_state_dir()` reads from `settings.SHC_STATE_DIR` (not `os.environ`)
  ```python
  # In backend/core/config.py
  def _state_dir() -> str:
      configured = settings.SHC_STATE_DIR
      if configured:
          return configured
      return str(DEFAULT_STATE_DIR)
  ```

- [ ] **AC-9**: No direct `os.environ.get("SHC_STATE_DIR")` remains in `config.py`

- [ ] **AC-10**: `.env.example` shows `SHC_STATE_DIR` as a valid, uncommented example
  ```bash
  grep -A2 "SHC_STATE_DIR" .env.example
  # Must show a commented or uncommented example line, no warning about crashing
  ```

- [ ] **AC-11**: `README.md` environment table and "Where is the signing key?" section document `SHC_STATE_DIR` as configurable via `.env` or environment

### 4.3 Quality Acceptance

- [ ] **AC-12**: `ruff check . && ruff format . && mypy .` passes (from repo root)
- [ ] **AC-13**: `pytest --cov=backend` passes with no new warnings
- [ ] **AC-14**: No regression in existing config tests (`backend/tests/unit/test_config.py`)

---

## 5. Test Scenarios

| Scenario | Setup | Expected |
|----------|-------|----------|
| T1: `.env` sets absolute path | `echo 'SHC_STATE_DIR=/abs/path' > .env` | `_state_dir()` returns `/abs/path` |
| T2: `.env` sets relative path | `echo 'SHC_STATE_DIR=./rel/path' > .env` | `_state_dir()` returns `./rel/path` |
| T3: Unset (default) | `rm -f .env` | `_state_dir()` returns `DEFAULT_STATE_DIR` (absolute) |
| T4: Env var overrides `.env` | `.env` has `/from-dotenv`, env has `/from-env` | `_state_dir()` returns `/from-env` |
| T5: Key persistence uses configured dir | `SHC_STATE_DIR=/tmp/test-key` | `secret_key` created in `/tmp/test-key/` |
| T6: Multiple imports share same `settings` instance | Import `settings` twice | Same object, same `SHC_STATE_DIR` value |
| T7: `SECRET_KEY` env var bypasses state dir entirely | `SECRET_KEY=explicit` + `SHC_STATE_DIR=/ignored` | Key from `SECRET_KEY` used, no file written to `/ignored` |

---

## 6. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Changing `_state_dir()` to use `settings` introduces circular import | Low | High | `settings` is instantiated at module bottom (line 396); `_state_dir()` is called from `_resolve_secret_key()` which is called from `model_post_init`. At that point `settings` exists. No circular import. |
| Relative `SHC_STATE_DIR` breaks key location logging | Medium | Low | Logging in `_resolve_secret_key()` already resolves to absolute path for the log line (line 222). Preserve that logic. |
| Existing deployments using process-env `SHC_STATE_DIR` break | Low | Medium | Process env has higher precedence than `.env` in pydantic-settings; behaviour unchanged. |
| `model_post_init` calls `_resolve_secret_key()` before `SHC_STATE_DIR` is validated | None | N/A | Pydantic validates all fields before `model_post_init`. `SHC_STATE_DIR` will be populated. |
| Tests that monkeypatch `os.environ["SHC_STATE_DIR"]` stop working | Medium | Low | Update tests to monkeypatch `settings.SHC_STATE_DIR` or use `.env` fixture. Only `test_config.py` likely affected. |

---

## 7. Implementation Approach (for SWE Reference)

### 7.1 Files to Modify

1. **`backend/core/config.py`**
   - Add `SHC_STATE_DIR: Optional[str] = None` to `Settings` class (after `SYNCTHING_ROOT`, before `DATABASE_URL` for logical grouping)
   - Rewrite `_state_dir()` to read `settings.SHC_STATE_DIR`
   - Remove `os.environ.get("SHC_STATE_DIR")` from `_state_dir()`

2. **`.env.example`**
   - Replace the warning block (lines 45–48) with a normal commented example:
     ```bash
     # SHC_STATE_DIR=/absolute/path/to/.shc
     ```

3. **`README.md`**
   - Update the Environment Variables table: change `SHC_STATE_DIR` default description to "`<repo-root>/.shc` (configurable via `.env` or environment)"
   - Update "Where is the signing key?" section: change "SHC_STATE_DIR is an **environment** variable" to "SHC_STATE_DIR can be set in `.env` or the environment"
   - Update upgrade note (line 419): remove "Putting it in `.env` is rejected"

### 7.2 Suggested Code Changes

**`backend/core/config.py` — Settings field (around line 275):**
```python
    # Syncthing configuration
    SYNCTHING_ROOT: str = "/tmp/syncthing"

    # Local server state directory (holds JWT signing key)
    SHC_STATE_DIR: Optional[str] = None

    # Database configuration.
```

**`backend/core/config.py` — `_state_dir()` rewrite (lines 248–261):**
```python
def _state_dir() -> str:
    """
    Directory for locally persisted server state.

    SHC_STATE_DIR (from Settings, via .env or environment) wins, and is used
    verbatim so a relative value stays relative. Unset, the directory is
    absolute and anchored on the package location: the previous default was the
    literal ".shc", which meant the directory - and the signing key written into
    it - moved with the working directory (issue #34).
    """
    configured = settings.SHC_STATE_DIR
    if configured:
        return configured
    return str(DEFAULT_STATE_DIR)
```

### 7.3 Verification Steps

```bash
# 1. Syntax and type check
cd /home/alexey/git/sci-home-cluster
ruff check . && ruff format . && mypy .

# 2. Unit tests
pytest backend/tests/unit/test_config.py -v

# 3. Integration: full test suite
pytest backend/tests -q

# 4. Manual functional check (uses env var, no .env file needed)
SHC_STATE_DIR=/tmp/spec-test-shc python -c "from backend.core.config import settings, _state_dir; assert _state_dir() == '/tmp/spec-test-shc'; print('OK')"
rm -rf /tmp/spec-test-shc
```

---

## 8. Spec Sign-off

- **PM**: ✅ Groomed — spec written, issue labelled `groomed`.
- **SWE**: (to implement per this spec)
- **QA**: (to verify against AC-1 through AC-14)
- **On-Call**: (if CI fails on merge)