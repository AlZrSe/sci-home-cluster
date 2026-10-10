# Spec — Issue #56: two committed JWT signing keys, and what to do about the ones already published

**Issue:** [#56](https://github.com/AlZrSe/sci-home-cluster/issues/56) — *Two different JWT signing keys are committed to git and selected by CWD*
**Status:** groomed, ready for SWE
**Author:** PM
**Baseline commit:** `b19114f` (`fix: anchor the default database path to the package location, not the CWD (#62)`)

---

## 0. Where this spec lives, and why

The prompt for this grooming asked for `docs/specs/56-committed-jwt-signing-keys.md`, which is where it is.

For the record, `docs/` already had a convention: `docs/<topic>-spec.md`
(`api-server-core-spec.md`, `jobs-router-spec.md`, `spec.md`, …). That convention holds **component design
specs** written before implementation, and every one of the recent issues (#27, #31, #34, #35) was groomed with
the spec posted as an **issue comment** rather than a file. This issue is neither: it is a per-issue grooming
artifact that is referenced by QA and by later issues (#58, #60 already cross-reference #34's spec sections).

**Decision:** per-issue grooming specs go in `docs/specs/<issue-number>-<slug>.md`; `docs/*.md` keeps meaning
"component design spec". Do not retro-move `docs/spec.md` or the component specs. The only cost is one new
directory, and it keeps the two artifact classes from blurring.

---

## 1. Problem statement, in user-facing terms

**A JWT signing key is committed to a public GitHub repository, and it is the key the application is actually
using.**

Today, a default checkout of `https://github.com/AlZrSe/sci-home-cluster` ships two signing keys in its index:

| Path | First 16 of SHA-256 of the key value | Blob bytes | Added in |
| --- | --- | --- | --- |
| `.shc/secret_key` | `8b5cb844a8beed1c` | 43 | `2d1a8cb` (issue #24) |
| `backend/.shc/secret_key` | `794185eb6dfdc1fa` | 43 | `08f1f27` (issue #20) |

The repository is **public** (`gh repo view --json visibility` → `PUBLIC`). The live one is
`.shc/secret_key`: since issue #34, `_state_dir()` returns `<repo-root>/.shc` for every working directory, so
the "selected by CWD" half of the issue title is already fixed — but the file it selects is still the published
one. Verified on `b19114f`:

```
$ python -c "from backend.core.config import settings; print(settings.SECRET_KEY == open('.shc/secret_key').read().strip())"
True
$ python - <<'PY'   # forge a token with the committed key, decode it with the live settings
import datetime; from jose import jwt
key = open('.shc/secret_key').read().strip()
tok = jwt.encode({'sub': 'attacker', 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)}, key, algorithm='HS256')
from backend.core.security import decode_access_token
print(decode_access_token(tok))
PY
{'sub': 'attacker', 'exp': 2106658728}
```

So today: **anyone who can clone the repository can mint a token that the running cluster accepts**, with an
`exp` of their choosing, for as long as that cluster keeps the committed key on disk.

Who this hurts, concretely:

1. **An operator whose cluster is reachable from outside their LAN.** The auth model is a single shared bearer
   token plus this JWT (`docs/spec.md#authentication`). The localhost bypass does not cover them, so the JWT is
   the only credential. Repo access is cluster access.
2. **Every developer and every future cloner.** They cannot tell a published key from a fresh one; nothing in
   the tree warns them, and `.gitignore` has no rule for either path, so the next `git add -A` re-publishes it.
3. **The project itself.** This is the kind of finding that, once written down, is read by everyone who ever
   evaluates the repository.

How did it happen, mechanically: `backend/core/config.py:264` is `settings = Settings()`, and
`Settings.model_post_init` (`:257-261`) calls `_resolve_secret_key()`, which **writes the key file at import
time**. Verified on `b19114f`:

```
$ SHC_STATE_DIR=<tmp> python -c "import backend.core.config"   # no exception, file written
<tmp>/secret_key
```

A bare `import` creates the file. There was never a rule stopping it, and nothing named it as a secret, so a
routine `git add -A` swept it in. That is the bug class to close: **the mechanism has to stop, not just the two
instances.**

---

## 2. Corrections to the issue (PM is the acceptance authority — these matter)

| Issue says | Reality on `b19114f` | Consequence for this spec |
| --- | --- | --- |
| "the application picks between them by CWD" | **Already fixed** by #34. `_state_dir()` (`config.py:141-154`) returns the absolute `DEFAULT_STATE_DIR` for every CWD. | The CWD half of the title is stale. The exposure half is not. Scope this issue on **untracking, rotation and enforcement**, not on path resolution. Re-doing #34's work would be a regression. |
| "`backend/.shc/secret_key` … written when launched from `backend/`" | True **pre-#34** only. No code path reads it today. | It is a legacy orphan. Delete it; do not migrate it (§4, D3). |
| "`_state_dir()` writes this file at import time" | Correct, and still true — via `model_post_init`, not `_state_dir()` itself. | The generated-file-on-import mechanism is the thing to make safe (§4, D3/D4). |
| "confirm the exact reader before changing anything: `backend/core/utils.py::_state_dir()`" | `_state_dir()` is in **`backend/core/config.py`**, not `utils.py`. `utils.py` only holds `is_localhost()`. The reader is `backend/core/security.py:21,31` (`settings.SECRET_KEY`). | Search `utils.py` and you will find nothing. Minor, but it would cost the SWE a detour. |
| "Severity: security, not hygiene" | **Agreed, and stronger than stated.** | — |
| — (not in the issue) | **`SHARED_TOKEN` is documented as "auto-generated" (`README.md:237`) but defaults to `None` and is never generated.** `POST /auth/token` therefore answers `SHARED_TOKEN_NOT_CONFIGURED` unless an operator configures it. | This makes the published key *more* serious (the JWT is the only obtainable credential) and it makes the rotation below **lock an operator out** if they have not set `SHARED_TOKEN`. Handled in D2 and in the upgrade note; the feature itself is filed as a separate issue (§8). |

Provenance (useful for the deny-list and for anyone auditing history): `.shc/secret_key` was added by `2d1a8cb`
(issue #24) and `backend/.shc/secret_key` by `08f1f27` (issue #20) — two ordinary feature commits that swept the
file in via `git add`, not a deliberate act.

---

## 3. User stories

**US-1 — a developer who clones the repo.**
> As a developer who clones this repository, I want no signing key in it, so that the cluster I run locally is
> not signing with a credential that the entire world can read, and so that I can tell a real key from a
> published one.

**US-2 — a developer who clones the repo (ergonomics half).**
> As a developer who pulls this branch, I want the state directory to stay out of `git status`, so that I do not
> see a stray untracked `.shc/` every morning and conclude git is broken.

**US-3 — the operator whose tokens would be invalidated.**
> As an operator running a cluster that has been using the committed key, I want my key rotated on the first
> start after the upgrade, so that the published key stops working **without** me having to notice a README
> warning, and I want to be told loudly that my clients need a new token.

**US-4 — the operator who needs to get back in.**
> As that operator, I want the upgrade note to tell me exactly how to obtain a new token (set `SHARED_TOKEN`
> first), so that being logged out is a five-minute inconvenience and not an outage.

**US-5 — the maintainer reviewing the diff.**
> As a maintainer, I want the tests for this to fail when the fix is undone, so that "we removed the secret"
> is a fact the suite proves rather than a claim in a commit message.

---

## 4. Decisions (the issue left these open; here is what was decided and why)

### D1 — Untrack the two files; do **not** rewrite git history. No follow-up issue for the rewrite.

**Decision:** `git rm --cached` both paths in this PR. History stays as it is.

**Why:** erasing the object does not erase the copy. The repository is public: the blob is in GitHub's object
store, in every fork and mirror, and in the caches of every tool that has ever indexed the repository. A
force-push to `main` would additionally force every collaborator to re-clone and rewrite the gitlink SHA
recorded for `frontend/` — for **zero** security gain once the key is rotated, because a rotated key signs
nothing. Rewriting history is disruptive, unreviewable in a normal PR, and out of scope for the agent-team flow.

**Follow-up:** **none.** Rotation (D2) is mechanised in this issue; erasure buys nothing after it. If the team
ever wants a public advisory anyway, that is a maintainer decision outside this workflow, not a tracked issue.
Recording it here so nobody files a "please rewrite history" issue later.

### D2 — Rotation is **automatic and code-driven**, not a documented manual step.

**Decision:** ship a deny-list of the SHA-256 digests of the two published keys. When `_resolve_secret_key()`
reads a persisted key whose digest is on the list, it treats it as absent: generates a fresh key, overwrites
the file (still `0600`), and logs a `WARNING`.

**Why this and not the alternatives:**

- *Untrack only (issue candidate 2 as written, "regenerate everywhere" as advice).* Rejected: the deployed
  clusters are exactly the ones that already have the file on disk, and they are the ones still signing with a
  public key. A README note reaches nobody who is not already reading the README. The code that starts their
  process, on the other hand, runs every time.
- *Regenerate unconditionally on every upgrade.* Rejected: it invalidates tokens for the (vast majority of)
  deployments whose key was never published, for no security gain, and it teaches operators to expect token
  churn — which is exactly how a real rotation gets ignored.
- *Accept both keys for a grace period during verification.* Rejected: it deliberately widens what the server
  accepts to include a credential the world knows. That is the opposite of the fix, and it complicates
  `backend/core/security.py::decode_access_token` to buy nothing.
- *A deny-list of known-bad digests* is the standard mitigation for "this exact credential leaked", it is
  ~10 lines, it is deterministic, and it converges: after one process start, no deployment is signing with a
  published key. A digest does not enable signing, so storing digests in the repository is safe.

**Precedence is unchanged and `SECRET_KEY` from the environment is never rotated** — an operator who sets it
explicitly owns it, and it is not persisted, so it cannot be the published key.

**Cost, stated honestly:** rotation invalidates live tokens. That is the point, and US-3/US-4 + the upgrade note
below are the mitigation.

### D3 — One canonical location: `<repo-root>/.shc`. `backend/.shc/secret_key` is **deleted, not migrated**.

**Decision:** remove it from the index; do not copy it anywhere; do not merge it into `<repo-root>/.shc`.

**Why:** both published keys are compromised, so "preserving" either one preserves a forge capability. There is
no content in `backend/.shc/secret_key` worth keeping — it is a 43-character random string, and any deployment
still using it is using a credential the world can read. Migration would mean *writing a known-bad key into the
canonical location*, which is strictly worse than the status quo.

Note there is **no coverage gap**, and the reason is worth stating precisely because the two cases differ. A
process started from post-#34 code never reads `backend/.shc/secret_key` at all — it reads
`<repo-root>/.shc/secret_key`, which is the *other* published key. A process still running pre-#34 code from
`backend/` keeps reading the legacy file until it restarts. Both of those keys are on the deny-list, so D2
catches either one on the first start that runs the new code.

**Upgrade consequence, in the note verbatim:** the first start after upgrading mints a **new** key, so every
JWT issued before it fails with `AUTH_TOKEN_INVALID` / "Invalid or expired token", and WebSocket log streams
(issue #53) will reject the old token too. Recovery, in the order the note gives it:

1. **Set `SHARED_TOKEN` in `.env` before the first start**, then `POST /api/v1/auth/token` mints a fresh JWT.
   This is the recommended route, and it is currently the *only* way to get a token over the API, because
   `SHARED_TOKEN` is unset by default (D5 below).
2. Or delete `<repo-root>/.shc/secret_key` and start the backend, if you use the localhost bypass and are
   happy to lose sessions.
3. Or point `SHC_STATE_DIR` at an empty directory.

### D4 — Generation-on-first-use is preserved. Nothing fails closed.

**Decision:** no new startup failure, no required configuration, no prompt. First use still generates.

**Why:** it is the documented behaviour in three places (`README.md:238`, `.env.example`,
`docs/backend-setup-spec.md:104`); it is what makes `pip install -e . && uvicorn backend.main:app` work; and
failing closed would break every fresh clone and every `pytest` run, since the test session imports
`backend.core.config`. The issue's candidate 3 ("fail or warn if a signing key is absent at startup") is
therefore **split**: see D6 — *loud* about the things an operator must know, *silent* about the thing they
already expect.

### D5 — Logging: `INFO` on generation, `WARNING` on rotation, nothing on a normal restart.

**Decision**, mirroring the precedent issue #34 set with `get_engine()`'s `(database file: …)` line
(`backend/core/database.py:110-119`):

| Event | Level | Must contain |
| --- | --- | --- |
| Key generated and persisted (first use) | `INFO` | the **absolute** path of the key file, and the word "generated" |
| Persisted key matched the deny-list and was replaced | `WARNING` | the **absolute** path, that it was **published in git history**, and that **previously issued tokens are now invalid** |
| Key could not be persisted | `WARNING` | unchanged from today (`config.py:132-137`) |
| Existing key read back on an ordinary restart | *nothing* | — this is the steady state; a line here would be noise on every boot |

**Why `INFO` and not `WARNING` for first use:** a fresh clone generating a key is the *expected* path. A
warning on every developer's first start is noise that trains people to ignore warnings, including the rotation
one. `LOG_LEVEL` defaults to `INFO`, so the line is visible.

**Why the rotation warning is guaranteed to be seen:** resolution happens at **import** of
`backend.core.config`, which is *before* anything in `backend/main.py` can configure logging. `WARNING` survives
that, because `logging.lastResort` emits unhandled `WARNING` and above no matter how the process was started. So
the one line that must not be missed is the one that is robust to the launcher.

> **Correction (PM acceptance review, issue #56 / PR #63, defect D-1).** This paragraph originally continued:
> *"Under `uvicorn`, logging is configured by `Config.__init__` before the app is imported, so the `INFO` line
> appears."* **That is false on this stack**, and the original AC-3 evidence clause ("observe one `INFO` line
> naming the absolute path") was therefore unobtainable. Verified: `'root' in uvicorn.config.LOGGING_CONFIG` →
> `False` on uvicorn 0.25.0, which configures `uvicorn`, `uvicorn.error` and `uvicorn.access` and no root. The
> only root handler arrived from `backend/main.py`'s `logging.basicConfig`, which runs *after* the
> `backend.core.config` import has already emitted — and the record was dropped by the root logger's default
> `WARNING` level before handler dispatch, so it was never created at all. Under `python -m uvicorn
> backend.main:app` the generation line appeared in neither stream while `INFO:backend.main:Starting …` did.
>
> **Resolution: the code was fixed, not this paragraph.** `logging.basicConfig` now runs *above* the
> `backend.core.config` import in `backend/main.py`, with `settings.LOG_LEVEL` applied afterwards via
> `logging.getLogger().setLevel(...)` — a second `basicConfig` is a no-op once root has a handler, which is why the
> ordering is the fix and not an extra call. A docs-only fix was rejected: it would leave the feature reachable
> only from `pytest`, the shape this repository has already declined in #46 and #60. The table above is unchanged;
> D5's *conclusion* survives and the `WARNING` half is the only load-bearing part of it.

**Explicitly rejected:**

- *A startup line in `main.py`'s lifespan naming the key.* Not required. It would need a provenance flag
  threaded out of `_resolve_secret_key()` just to repeat what the event already said, i.e. a second source of
  truth. If the SWE adds one anyway it must not duplicate the rotation logic.
- *Exposing the key path (or the key) on `GET /api/v1/health`.* **No.** `/health` has no auth dependency
  (`backend/main.py:269`), so it is an unauthenticated read. #34 put the *database* path there because a path
  is not a credential; a secret key's location is closer to a credential than #54's author assumed. `docs/`
  and `README.md` document the location instead.
- *A warning every time a key is generated with `SECRET_KEY` unset.* That is the first-use case (D4).

### D6 — One ignore rule, directory form: `.shc/`

**Decision:** a single line `.shc/` (plus an explanatory comment) in `.gitignore`, matching at **any depth**.

Verified experimentally, because the spec must not depend on a guess about gitignore semantics:

```
$ cat .gitignore | tail -1
.shc/
$ git check-ignore --no-index -v .shc/secret_key backend/.shc/secret_key
.gitignore:1:.shc/	.shc/secret_key
.gitignore:1:.shc/	backend/.shc/secret_key
```

**Why directory form and not `secret_key` / `.shc/secret_key`:**

- `SHC_STATE_DIR` is documented as the home for *locally persisted server state* (`AGENTS.md:172`), not for one
  file. The next thing written there will be unignored by a file-shaped rule.
- After `git rm --cached`, every existing checkout **keeps the file on disk** — that is deliberate (see D7). So
  without a directory rule, every developer sees an untracked `.shc/` (and `backend/.shc/`) after pulling, which
  is US-2: it looks like the fix left litter.
- Precedent: `data/` (`.gitignore:109-113`), added by #34 for exactly this reason, with a comment naming the
  issue. Copy the shape, cite the issue.

**Ordering matters (do this first):** add the `.gitignore` rule **before** `git rm --cached`. Ignore rules do not
affect tracked files, so the sequence "rule, then untrack" leaves no window where the files are untracked *and*
unignored.

### D7 — Untrack, do not delete the working copy.

**Decision:** `git rm --cached .shc/secret_key backend/.shc/secret_key`. Do **not** `git rm` and do not add a
post-checkout cleanup.

**Why:** the file in your working tree is the key your running cluster is signing with. Deleting it from disk is
a rotation by stealth — the running deployment's key vanishes and nothing regenerates it until restart. The
explicit, logged, denylist-driven rotation (D2) is strictly better than a filesystem surprise, and it works for
every *other* clone too, which a local delete cannot.

### D8 — Where the deny-list lives: a module-level constant in `backend/core/config.py`.

**Decision:** `PUBLISHED_KEY_SHA256: Final[frozenset[str]]` next to `DEFAULT_STATE_DIR` (line ~40), holding
**digests only** — never the key values. Name the two paths and issue #56 in the comment.

**Why not `shared/auth/published_keys.json` (the `shared/auth/localhost_hosts.json` shape):** that file exists
for one reason — two repositories must not drift. Nothing outside this repository reads the deny-list, so a
JSON file would imply a cross-repo contract that does not exist and would need drift tests that have nothing to
drift against.

**Digest values** (from the blobs at `b19114f`, digest taken over `read_text(encoding="utf-8").strip()`):

```
8b5cb844a8beed1cfaa320a1443410de4d4c375d01af8e8d7083b48d9ad56952   .shc/secret_key
794185eb6dfdc1fa7362a89f1b51b83265777c2c6ef2bae1298a13a7c256c00b   backend/.shc/secret_key
```

**⚠ Windows CRLF landmine, read before computing them.** The blobs are exactly 43 bytes with **no trailing newline**
(`git cat-file -s` → 43). On **some** git versions `git show <rev>:<path>` under `core.autocrlf=true` writes **45**
bytes (`…KGPw\r\n`) into a pipe. Hash that and you get a digest that never matches anything, and the rotation
silently never fires while every test still passes.

> **Correction (PM acceptance review, issue #56 / PR #63).** This originally asserted the 45-byte behaviour as a
> certainty for this checkout. **It does not reproduce on this stack.** Measured on git 2.54.0.windows.1 with
> `core.autocrlf=true` set: `git show 2d1a8cb:.shc/secret_key` emits **43** bytes, byte-identical to
> `git cat-file blob`, no CR present. The hazard is **git-version-dependent** — real on some versions, absent on
> this one. Design around it rather than expect it. Nothing about the verdict changes: `git cat-file blob` is
> correct under **both** behaviours and is what U11 uses.

Derive the digests from the **checked-out file**, stripped:

```powershell
python -c "import hashlib,pathlib; [print(p, hashlib.sha256(pathlib.Path(p).read_text(encoding='utf-8').strip().encode('utf-8')).hexdigest()) for p in ('.shc/secret_key','backend/.shc/secret_key')]"
```

The runtime comparison must hash the same thing the reader produces — `existing.strip()` — for the same reason.

### D9 — The deny-list mechanism must be exercised by a test that does not contain a published key.

The real keys stay in history; the test suite must never re-publish them. So the rotation test
**monkeypatches a synthetic digest into the deny-list** rather than reading the real ones, and a separate
assertion checks the real list is well-formed and non-empty. Rationale and detail in U5/U6.

---

## 5. Acceptance criteria

Each AC is independently verifiable. "Evidence" is what QA must produce, not what the SWE asserts in a comment.

---

**AC-1 — Neither signing key is tracked.**
*Given* the branch at `b19114f`, *when* `git ls-files -- .shc backend/.shc` runs, *then* it prints nothing.

*Evidence:* the command output above; `git status --short` on the branch shows `D  .shc/secret_key` and
`D  backend/.shc/secret_key` as **staged** deletions (not unstaged); the PR diff shows both files removed;
`git clone <branch> && ls .shc` → *No such file or directory*.

---

**AC-2 — One ignore rule covers both locations, and covers nothing that is tracked.**
*Given* `.gitignore`, *when* `git check-ignore --no-index -v .shc/secret_key backend/.shc/secret_key` runs,
*then* both paths are reported as ignored, with exit code 0.

*Evidence:* both lines of output naming the `.shc/` rule; and the negative half — for every path in
`git ls-files`, `git check-ignore --no-index` reports nothing (so the rule cannot be swallowing something real,
e.g. a typo'd `/s/` or `*key*`).

---

**AC-3 — Generation on first use still works, unchanged.**
*Given* an empty `SHC_STATE_DIR`, *when* the app resolves the signing key twice, *then* it generates one key,
persists it at `<SHC_STATE_DIR>/secret_key`, returns the identical value both times, and the file is not
world-readable on POSIX.

*Evidence:* U9 in §6 (unit), and a manual run: delete the key, start the backend, observe one `INFO` line naming
the absolute path, restart, observe no line and the same key still validating an old token.

---

**AC-4 — A published key on disk is replaced, loudly.**
*Given* a persisted key whose digest is in `PUBLISHED_KEY_SHA256`, *when* the signing key is resolved,
*then* a **different** key is returned, the file on disk holds the new key, the file is still `0600` on POSIX,
and exactly one `WARNING` was emitted naming the absolute path, saying the key was **published in git history**,
and saying previously issued tokens are now invalid.

*Evidence:* U6 (unit, mutation-pinned) and U10 (integration, end to end through the HTTP API).

---

**AC-5 — The deny-list is real, not a placeholder.**
*Given* `PUBLISHED_KEY_SHA256`, *then* it has exactly two entries, both 64-character lowercase hex, both
distinct, and both equal to the digests of the two keys committed at `2d1a8cb:.shc/secret_key` and
`08f1f27:backend/.shc/secret_key`.

*Evidence:* U5, plus QA independently re-deriving both digests from git history with the §D8 command and
comparing them to the constant. A typo'd digest must fail here, not silently disable rotation.

---

**AC-6 — No tracked file in this repository contains a published key.**
*Given* the index, *when* every tracked regular file's bytes are hashed and compared against
`PUBLISHED_KEY_SHA256`, *then* there is no match.

*Evidence:* U3. Note this test is **red before `git rm --cached` and green after** — that is the point; it is
the regression guard that survives someone re-committing the key under a different filename later.

---

**AC-7 — The two log lines exist, at the right level, with the right content; the steady state is silent.**
*Evidence:* U9 (generation → one `INFO` containing the absolute path), U6 (rotation → one `WARNING`
containing the path + "published in git history" + the invalidation warning), and U7 (restart with an
unrecognised key → **no** record at all).

---

**AC-8 — Nothing about startup got stricter.**
*Given* a fresh checkout with no key file, *when* `import backend.core.config` and `uvicorn backend.main:app`
run, *then* both succeed, and the existing import-side-effect test
(`backend/tests/unit/test_config.py::test_import_has_no_filesystem_side_effects`) still passes unchanged.

*Evidence:* the unmodified test still green (no edit to it — this issue must not weaken #34's guard) and a
manual `uvicorn` start reaching "Database store initialized".

---

**AC-9 — No secret material in logs, and no key path over HTTP.**
*Given* any log record emitted by generation or rotation, *then* it contains no substring of the key of length
≥ 8, and `GET /api/v1/health` contains neither the key nor the string `secret_key`.

*Evidence:* U9/U6 assert the negative directly; `/health` verified by reading the response body of a running
server. This AC exists because the log lines are the one place the SWE could leak the value by accident.

---

**AC-10 — The upgrade note exists and is actionable.**
*Given* an operator who has been running the published key, *then* the documentation tells them, in one place:
that the key was published in a public repository; that the first start after upgrading replaces it; that their
existing tokens (and WebSocket log streams) stop working; and the three recovery options from D3, with
"set `SHARED_TOKEN` first" first.

*Evidence:* the file list in §7, reviewed diff by diff. QA checks the note against the *actual* behaviour —
including that `POST /api/v1/auth/token` really does mint a token once `SHARED_TOKEN` is set.

---

**AC-11 — The frontend submodule is untouched.**
*Evidence:* `git diff --stat b19114f...HEAD -- frontend` is **empty** (no gitlink change);
`git -C frontend status --short` is clean. If a gitlink bump appears, the PR is wrong.

---

**AC-12 — No new lint, type or test errors.**
*Evidence:* §8 verification block, with the before/after numbers recorded in the PR description. The gates are
red on `main` (issue #54), so the criterion is **delta zero**, recorded as a measurement — not "the gate is
green".

---

**AC-13 — No history rewriting.**
*Evidence:* `git log --oneline b19114f..HEAD` contains only additive commits; `git show 2d1a8cb --stat` and
`git show 08f1f27 --stat` still resolve and still list both blobs; no force-push was used on a shared branch.

---

**AC-14 — The stale `SHARED_TOKEN` documentation is corrected.**
`README.md:237` documents `SHARED_TOKEN` as "auto-generated". It is `None` and never generated. The upgrade note
tells operators to set it, so leaving that cell as-is would contradict the note. The cell must read as unset /
none, with a pointer to `POST /api/v1/auth/token` returning `SHARED_TOKEN_NOT_CONFIGURED`.

*Evidence:* diff review. (Implementing `SHARED_TOKEN` generation is **out of scope** — §8.)

---

## 6. Test scenarios

New file **`backend/tests/unit/test_signing_key_hygiene.py`** (units) and
**`backend/tests/integration/test_signing_key_rotation.py`** (end-to-end).

**Why not append to `test_config.py`:** that file is #34's regression file, and #60 is already asking for
mutation-testing inside it. Two issues' guards in one file makes that harder, not easier.

**Shared requirements for every git-touching test:** anchor on `Path(__file__).resolve().parents[3]` (the
convention already in `test_config.py:26`), pass `-C <REPO_ROOT>` to every git call so the assertions hold from
any CWD, and say in the docstring which single mutation turns the test red — naming it is the anti-vacuity
discipline #60 asks for.

Also required: **no test may contain either published key, or any prefix of one of length ≥ 8.** Use synthetic
values only.

---

### Unit — `backend/tests/unit/test_signing_key_hygiene.py`

**U1 — the ignore rule exists and covers both locations** (AC-2)
Run `git check-ignore --no-index -v` on both paths; assert exit 0 and that **both** are named, and that both are
attributed to the same rule.
*Mutation that makes it red:* delete the `.shc/` line from `.gitignore`. Also red if a later
`!.shc/secret_key` negation is added — gitignore is last-match-wins and `check-ignore` evaluates the whole set,
so the test sees it.
*`--no-index` is mandatory:* verified on a scratch repo — plain `git check-ignore` reports a **tracked** path as
*not ignored* (exit 1), so without it the test silently measures "untracked **and** ignored" and stops
measuring the rule once untracking regresses.

**U2 — the key files are not tracked** (AC-1)
Assert `git ls-files -- .shc backend/.shc` is empty, for each of the two paths individually.
*Mutation that makes it red:* `git add -f .shc/secret_key`. The `-f` is required — `.shc/` is ignored, so a
plain `git add` is refused with exit 1 and stages nothing, and the test would stay green while proving
nothing.
*Why a separate test from U1:* U1 must stay **green** when the files are tracked-but-ignored, which is what
proves the ignore rule itself is still correct and a re-tracking regression is about tracking, not about ignoring.

> **Correction (PM acceptance review, defect D-3).** This originally read: *"so that a re-tracking regression is
> attributable to U2 alone and not masked by U1."* **The attribution half is false.** Under U2's mutation
> (`git add -f .shc/secret_key`) **U4 also goes red**, because `git check-ignore --no-index` reports a
> tracked-but-ignored path as ignored, so U4's "the rule ignores nothing tracked" assertion fails on the
> re-tracked path. U1 stays green, and that is the property that actually matters and that is preserved.

**U3 — no tracked file contains a published key** (AC-6)
List index entries with `git ls-files -s -z`, skip gitlinks (mode `160000` — the `frontend` entry is a commit id,
not a blob, so there is nothing to hash), read the remaining object ids' content in **one** `git cat-file --batch`,
hash each, and assert no digest is in `PUBLISHED_KEY_SHA256`.
Read the **index blobs**, not the working-tree files, because AC-6's normative text names the index and the two are
not the same object: stage a secret and then edit the file, and a working-tree scan reads the edit and passes while
`git commit` publishes the staged blob. This is the defect PM acceptance review raised as D-6, and the original
implementation here scanned `REPO_ROOT / path`.
*Mutation that makes it red:* `git add -f .shc/secret_key`; **or** committing the same key under any other name
or in any other file — which is why this is a content scan and not a path check.

**U4 — the new rule ignores nothing that is tracked** (AC-2, negative half)
For every path from `git ls-files`, assert `git check-ignore --no-index --quiet` fails.
*Mutation that makes it red:* replacing `.shc/` with something over-broad (`*key*`, `/s/`, `*.shc` variants that
clash) that starts matching a tracked file. This is the guard against fixing the secret by ignoring too much.

**U5 — the deny-list is well-formed and non-empty** (AC-5)
Assert: `len == 2`; every entry matches `^[0-9a-f]{64}$`; the two are distinct.
*Mutation that makes it red:* `frozenset()` (the "fix" that does nothing), a truncated digest, or an
uppercase/bytes entry.

**U6 — rotation replaces a published key and warns** (AC-4, AC-7, AC-9)
With `SHC_STATE_DIR` in a `tmp_path` and `SECRET_KEY`/`SHC_STATE_DIR` handled via `monkeypatch`: write a
synthetic key `k = "published-" + "x" * 40` to `secret_key`; `monkeypatch.setattr(config, "PUBLISHED_KEY_SHA256",
frozenset({sha256(k)}))`; call `_resolve_secret_key()`; assert the result **differs** from `k`, that the file
now holds the new value, and that exactly one `WARNING` record names the absolute path, mentions the git
history, and mentions invalidation. Then assert no 8-character-or-longer substring of `k` appears in any
emitted record (AC-9).
*Mutations that make it red:* deleting the digest check; `in` → `not in`; dropping the `WARNING` (it must not
degrade to `INFO`); hashing the raw file bytes instead of `.strip()`ed text (the Windows CRLF bug from D8 —
note the synthetic key has no newline, so add one to the file in this test to catch exactly that: write
`k + "\r\n"` and assert rotation still fires).

**U7 — an unrecognised key is never rotated, and the steady state is silent** (AC-7)
Same setup, deny-list left at its real value (or monkeypatched to a digest that does not match); assert the
persisted key is returned unchanged, the file's mtime/bytes are unchanged, and **no** record at any level
mentions rotation.
*Mutation that makes it red:* rotating unconditionally; logging on every read; or a `!=` in the comparison.

**U8 — an explicit `SECRET_KEY` wins and is never persisted or rotated**
With `SECRET_KEY` set in the environment, `_resolve_secret_key()` returns it, writes nothing into
`SHC_STATE_DIR`, and emits no rotation warning even when that value is on the deny-list.
*Mutation that makes it red:* moving the deny-list check ahead of the env-var branch.
*Note:* `backend/tests/unit/test_api_fixes.py:137-149` already covers persistence and reuse; this adds the
environment-precedence-and-no-rotation case, which nothing covers today.

**U9 — generation logs `INFO` with the absolute path** (AC-3, AC-7, AC-9)
`tmp_path` state dir, `caplog` at `INFO` for `backend.core.config`, resolve once; assert exactly one record, at
`INFO`, containing `str(tmp_path / "secret_key")` and a generation word. Then resolve again and assert **no new
record**. Also assert no emitted record contains the generated value.
*Mutations that make it red:* logging the path without resolving it absolutely (assert against the absolute
string, not the basename); logging at `WARNING`; logging on every read.

**U10 — integration: a token signed with the pre-rotation key stops working** (AC-4, end to end)
`backend/tests/integration/test_signing_key_rotation.py`. With a synthetic "published" key installed into a
temp state dir and monkeypatched into the deny-list: mint a token with `create_access_token` under the *old* key,
rotate, then (a) `GET /api/v1/auth/verify` with that token → `{"valid": false}`, and (b) a token minted after
rotation verifies → `{"valid": true}`.
*Mutation that makes it red:* removing the rotation; rotating without overwriting the file; leaving
`settings.SECRET_KEY` pointing at the old value after the file changes.
**⚠ Trap for the SWE:** Starlette's TestClient sends `Host: testserver`, which **is** in the localhost bypass
list, so `get_current_token_payload` short-circuits and a *missing* token is accepted too. Assert on the
`valid` field produced by `AuthService.validate_token`, never on a 401 — a test written as "expect 401" would be
red for a reason that has nothing to do with this fix.

### Integration — `backend/tests/integration/test_signing_key_rotation.py`

Covered by U10 above; that is the whole file. Keep it to the token-level round trip, which is the property that
actually matters: **a credential an outsider can mint must stop verifying.**

---

## 7. Files to change

| File | Change | One-line reason |
| --- | --- | --- |
| `.gitignore` | **add** the `.shc/` rule + comment (D6) | stops the generated key from ever being re-committed, at any depth |
| `.shc/secret_key` | **untrack** (`git rm --cached`) | it is the live signing key of a default checkout and it is public (AC-1) |
| `backend/.shc/secret_key` | **untrack** (`git rm --cached`) | legacy orphan from before #34; deleting, not migrating (D3) |
| `backend/core/config.py` | add `PUBLISHED_KEY_SHA256` (D8) + deny-list check, regeneration, and the two log lines in `_resolve_secret_key()` | makes rotation automatic (D2) and the path answerable (D5) |
| `backend/tests/unit/test_signing_key_hygiene.py` | **new** — U1…U9 | the four non-vacuous guards for ignore/untrack/content/logging |
| `backend/tests/integration/test_signing_key_rotation.py` | **new** — U10 | proves the forged credential stops verifying, end to end |
| `README.md` | new **"Where is the signing key?"** subsection after "Which database am I using?" + an upgrade block; correct the `SHARED_TOKEN` cell (AC-10, AC-14) | the note operators will actually read |
| `.env.example` | update the "Local server state" block: key is generated, never committed, rotated if it was published; recommend setting `SHARED_TOKEN` | keeps the documented precedence story honest |
| `AGENTS.md` | one clause in the Environment & Auth bullet (`:172`): the signing key is generated into `.shc/`, is **never** committed, and a published key is rotated on next start | this file is what agents read before touching the repo |
| `docs/backend-setup-spec.md` | update the `SECRET_KEY` line (`:104`); **close open question 2** (`:216`) with the answer chosen here | it is the design spec that still poses this as undecided |
| `docs/spec.md` | one paragraph under **Authentication** (`:113`) recording that the key is locally generated and untracked, and that the two committed keys are revoked | the auth contract must not imply a shipped key |
| `docs/specs/56-committed-jwt-signing-keys.md` | this file | — |

**Must not change:** `frontend/**` (submodule — AC-11), `backend/core/security.py`,
`backend/core/deps.py`, `backend/services/auth_service.py`, `backend/tests/unit/test_config.py`,
`backend/tests/conftest.py`, `data/` and the database story.

---

## 8. Out of scope (explicit)

1. **The `frontend/` submodule.** Nothing in it reads the signing key. AC-11 forbids even a gitlink bump.
2. **Rewriting git history.** D1. Not recommended, no follow-up issue.
3. **Issue #51** — the localhost bypass derived from the client-supplied `Host` header. Orthogonal: fixing it does
   not unpublish a key, and unpublishing a key does not close the bypass.
4. **Issue #60** — strengthening `test_import_has_no_filesystem_side_effects`. This change is *compatible* with
   #60's plan to drop `SECRET_KEY` from that child env and use a temp `SHC_STATE_DIR` instead (generation then
   happens and is contained, and the "CWD stays empty" assertion still holds). Do not bundle the #60 fix here, and
   do not edit that test to accommodate this change.
5. **Issue #58** — CWD-relative `.env` discovery. Untouched.
6. **Implementing `SHARED_TOKEN` generation.** AC-14 corrects the documentation; generating the token is a
   feature and should be filed separately (see §10).
7. **Changing the auth model** (per-user credentials, asymmetric signing, key rotation endpoints).
8. **Failing closed when no key exists.** D4.
9. **Secret scanning / pre-commit hooks / CI.** #26 territory, and #26 is deliberately deferred.
10. **`chmod` semantics on Windows.** `state_path.chmod(0o600)` is already best-effort there; changing it is a
    separate platform question.
11. **The `data/` directory and stale `*.db` files.** Unrelated; README already covers them.

---

## 9. Verification commands

Run from the repo root. Record every number in the PR description.

**Baseline on `b19114f` (measured by PM on 2026-10-06, ruff 0.5.0, mypy 1.0.0, Python 3.12.7):**

| Gate | Baseline |
| --- | --- |
| `ruff check .` | **44 errors** |
| `ruff format --check .` | **3 files** would be reformatted |
| `mypy .` | **133 errors in 24 files** (89 source files checked) |
| `pytest --collect-only -q` | 534 tests collected |

Issue #54 recorded 46 / 3 / 133; the ruff delta is toolchain drift, which is exactly why **the criterion is a
measured delta, not an absolute**. Re-measure the baseline on the branch's merge-base rather than trusting this
table.

```powershell
# 0. Baseline, on the merge-base, in a clean worktree
git worktree add ..\shc-baseline b19114f
Push-Location ..\shc-baseline
ruff check . 2>&1 | Select-Object -Last 1
ruff format --check . 2>&1 | Select-Object -Last 1
mypy . 2>&1 | Select-Object -Last 1
Pop-Location

# 1. The fix, in this order (AC-2 before AC-1 — see D6)
#    a. add the `.shc/` rule to .gitignore
#    b. git rm --cached .shc/secret_key backend/.shc/secret_key

# 2. Both paths ignored (AC-2)
git check-ignore --no-index -v .shc/secret_key backend/.shc/secret_key   # exit 0, two lines

# 3. Neither tracked (AC-1)
git ls-files -- .shc backend/.shc                                          # empty
git status --short .shc backend/.shc                                       # two staged "D " entries

# 4. The submodule is untouched (AC-11)
git diff --stat b19114f...HEAD -- frontend                                 # empty
git -C frontend status --short                                             # empty

# 5. No history rewriting (AC-13)
git show 2d1a8cb --stat | Select-String "secret_key"                      # still listed
git show 08f1f27 --stat | Select-String "secret_key"                      # still listed

# 6. The gates, on the branch, compared to the baseline (AC-12)
ruff check . 2>&1 | Select-Object -Last 1                                 # <= 44
ruff format --check . 2>&1 | Select-Object -Last 1                        # <= 3 files
mypy . 2>&1 | Select-Object -Last 1                                       # <= 133 errors

# 7. The suite (AC-12)
python -m pytest -q -p no:cacheprovider                                   # no new failures
python -m pytest -q -p no:cacheprovider --cov=backend                     # coverage >= 80 (pyproject fail_under)

# 8. The new tests, alone, and then mutation-tested (AC-3, AC-4)
python -m pytest backend/tests/unit/test_signing_key_hygiene.py -v
python -m pytest backend/tests/integration/test_signing_key_rotation.py -v
```

**The mutation pass is mandatory, not optional.** For each of U1, U2, U3, U4, U6, U7, U9: apply the mutation named
in §6, confirm **that test alone** goes red, and revert. Paste the result into the PR description. A test that
survives its own mutation is not evidence, and this repository has already accepted two issues (#46, #60) for
guards that did not carry the property they were named for.

**#57 warning (RESOLVED — no longer applies):** this spec was written when the suite was intermittently
flaky (`sqlite3.OperationalError: database is locked`, and a hang after the summary line). PR #109 fixed
that and closed #57. **Do not invoke it to excuse a failure here.** A run that fails is a real result:
re-run it to confirm it is reproducible, and if it persists, it belongs to this change.

**Manual check (AC-3, AC-8):** delete `.shc/secret_key`, `uvicorn backend.main:app`, observe exactly one
`INFO` line naming the absolute key path, and `/health` returning 200 with **no** `secret_key` string in the
body (AC-9). Restart, observe no second line and the same key still validating the token issued before the
restart. `git status --short` shows nothing for `.shc/` (US-2).

---

## 10. Risks and open questions

**Risk — the deny-list is a one-shot device.** Once the two digests are rotated out everywhere, the list is
history, and a *third* accidental commit would not be caught by it. *Mitigation:* U3 scans **every tracked file's
content** against the list, so it catches re-publication of these two keys forever, and U1/U2 catch any key at
all. *Residual:* a brand-new key committed under a new name would pass U3. Accepted: general secret scanning is
#26's job, and a ratchet that pretends to cover it would be worse than one that does not.

**Risk — rotation logs out every operator on their first start.** Intended and unfixable (that is what rotation
*means*), but the blast radius depends on them being able to get a new token, and `SHARED_TOKEN` is unset by
default. *Mitigation:* the upgrade note leads with "set `SHARED_TOKEN` first" (D3). *Residual:* an operator who
upgrades without reading it is locked out of their API until they set `SHARED_TOKEN` or use the localhost
bypass. This is the strongest argument for filing `SHARED_TOKEN` generation as its own issue (§10 below) rather
than leaving the operator to configure it by hand.

**Risk — a typo'd digest disables rotation silently.** Every test would still pass; nothing would rotate.
*Mitigation:* AC-5 requires QA to re-derive both digests from git history independently, and U6 exercises the
mechanism with a synthetic digest so the code path is proven regardless.

**Risk — `git check-ignore` without `--no-index` passes for the wrong reason** (verified: a tracked path is
never reported as ignored). *Mitigation:* U1 uses `--no-index` and U2 carries the untracking assertion.

**Risk — Windows CRLF corrupts a digest computed from `git show` output.** Real **on some git versions**, where
`core.autocrlf=true` turns 43 blob bytes into 45 on the way out of a pipe — **not** on git 2.54.0.windows.1, which
was measured emitting 43 (see D8's correction), so do not treat this as a live landmine on this stack.
*Mitigation:* `git cat-file blob`, which bypasses the filter on every version, plus D8's command, U6's `\r\n`
payload, AC-5.

**Risk — over-broad ignore rule.** `.shc/` is precise today, but a future edit could become `*key*`.
*Mitigation:* U4 asserts no tracked path is ignored.

**Open question, answered — history rewrite.** No. D1. The published material stays in history; rotation, not
erasure, is the control.

**Open question, answered — `backend/.shc/secret_key`.** Deleted, not migrated. D3.

**Open question, answered — fail or warn at startup?** Neither fails; `INFO` on generation, `WARNING` on
rotation, silence otherwise. D4, D5.

**Open question, answered — one place or two?** One: `<repo-root>/.shc`. `backend/.shc` is deleted. D3.

**Open question — should the key path be on `/health`?** No: unauthenticated. D5.

**Recommendation for a new issue (PM to file, not this PR): `SHARED_TOKEN` is documented as auto-generated and is
never generated.** `README.md:237` is wrong, `POST /api/v1/auth/token` cannot mint a token out of the box, and
that is what makes the rotation above an outage instead of a re-login. Out of scope here (AC-14 corrects the
documentation; the feature is not this issue's), but it should be tracked.

**Recommendation for a new issue (PM to file): the test suite generates a signing key at import.**
`python -m pytest` writes `<repo-root>/.shc/secret_key` on a fresh clone, because `settings = Settings()` runs at
import (`config.py:264`). That is safe after this change (the directory is ignored), but it means "no test in the
repo may depend on the real signing key" is a rule nobody has written down. #60 is adjacent; if #60 does not take
it, file it there.
