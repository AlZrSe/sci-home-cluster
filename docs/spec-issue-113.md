# Spec: Test suite git dependency (Issue #113)

**Issue**: #113 — "Test suite depends on \\git\\ being on PATH, and fails as if it were a signing-key breach"
**Labels**: enhancement, phase-1, windows
**Author**: PM
**Date**: 2026-11-05

## 1. Problem
`backend/tests/unit/test_signing_key_hygiene.py` shells out to `git`. Without `git` on PATH (or without a `.git` directory), 7 tests fail with messages that read like a signing-key security breach.

## 2. Acceptance Criteria
- AC-1: With `git` absent from PATH, the git-dependent tests skip with a reason naming `git`.
- AC-2: With `git` present but no `.git`, same skip.
- AC-3: With a normal repository, all 12 tests pass.
- AC-4: Prerequisite documented in AGENTS.md (and README.md testing section).

## 3. Implementation
- Add `_git_available()` helper (checks `shutil.which("git")` and `git rev-parse --is-inside-work-tree`).
- Apply `@pytest.mark.skipif(not _git_available(), reason=...)` to the 7 git-dependent tests:
  test_ignore_rule_covers_both_key_locations, test_published_key_file_is_not_tracked,
  test_no_tracked_file_contains_a_published_key, test_ignore_rule_ignores_nothing_tracked,
  test_deny_list_digest_matches_the_committed_key.
- Document prerequisite.

## 4. Definition of Done
- Gates green; tests pass in repo; skip behavior verified.