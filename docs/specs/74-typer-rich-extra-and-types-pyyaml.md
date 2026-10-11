# Spec: Fix typer[rich] extra and add types-PyYAML to dev extras

## Issue
Two problems in `pyproject.toml`:

1. **`typer[rich]==0.9.0`** — The `rich` extra does not exist for typer 0.9.0. Pip warns: `WARNING: typer 0.9.0 does not provide the extra 'rich'`. Valid extras for typer 0.9.0 are: `all`, `dev`, `doc`, `test`. Since `rich==13.0.0` is already a direct dependency, the fix is to use plain `typer==0.9.0`.

2. **`types-PyYAML` missing from dev extras** — mypy reports "Library stubs not installed for yaml" errors (4 occurrences). Need to add `types-PyYAML` to the `dev` optional dependencies.

## Changes

### pyproject.toml

**Line 26:** Change
```toml
"typer[rich]==0.9.0",
```
to
```toml
"typer==0.9.0",
```

**Line 45 (dev extras):** Add `types-PyYAML` entry. Use a version compatible with `pyyaml==5.3.1`. The latest `types-PyYAML` (6.0.12.20260906) supports PyYAML 5.3+. Add:
```toml
"types-PyYAML==6.0.12.20260906",
```

## Acceptance Criteria

1. **Clean install, no warnings**
   - `pip install -e ".[dev]"` completes without any "does not provide the extra" warnings.

2. **mypy no yaml stub errors**
   - `mypy .` produces zero "Library stubs not installed for yaml" errors.

3. **No other pin changes**
   - Only the two changes above. All other dependency versions remain identical.

## Test Plan

1. Create a fresh virtual environment.
2. Run `pip install -e ".[dev]"` — verify no warnings about `typer` extras.
3. Run `mypy .` — verify no "Library stubs not installed for yaml" errors.
4. Run existing test suite (`pytest --cov=backend`) — ensure no regressions.

## Out of Scope

- Upgrading typer to a newer version.
- Changing any other dependency versions.
- Fixing other mypy errors (pre-existing, unrelated to yaml stubs).