# Spec: Job Submission Enters the Syncthing Folder (`jobs/<job_id>/state.yaml`)

**Issue**: #105 — "Job submission never enters the Syncthing folder, so a file-only agent can never see a submitted job"
**Labels**: `agent`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-10-14

---

## 1. Verified Problem Statement

Under the file-only transport decision on #92, the Syncthing folder is the only channel in both
directions. `POST /jobs` writes a database row and nothing else, so the channel the agent has
never opens carries nothing.

**There is no write into the folder anywhere in the backend.** Grepping every write-shaped call in
the three directories that could plausibly contain one:

```console
$ grep -rnE "write_yaml|update_yaml|write_text|open\(.*['\"]w" backend/services/ backend/api/ backend/store/
$ echo $?
1
```

Zero hits. `syncthing_service.py` is a pure reader — its only I/O helper is `read_yaml`, at
`:253` (jobs) and `:284` (nodes), imported at `:19`.

`create_job` is `backend/store/database_store.py:334-353` in full:

```python
async def create_job(self, job_spec: JobSpec) -> JobState:
    """Create a new job and return the created job state."""
    await self._ensure_seeded()
    async with get_session() as session:
        self._job_counter += 1
        job_id = f"job-{self._job_counter}"
        now = datetime.now()
        job = JobModel(
            job_id=job_id,
            spec=job_spec.model_dump(),
            status=JobStatus.PENDING,
            created_at=now,
            retry_count=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return self._model_to_job_state(job)
```

It imports nothing from `shared.file_ops` and touches no filesystem.

Reproduced against the real API with a real, writable, empty `SYNCTHING_ROOT`:

```python
# scratch test, run against backend/tests/conftest.py, then deleted
async def test_submission_never_enters_the_folder(syncthing_root, monkeypatch):
    monkeypatch.setenv("SYNCTHING_ROOT", syncthing_root)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        ac.headers["Authorization"] = "Bearer localhost-no-auth"
        r = await ac.post(
            "/api/v1/jobs/", json=create_job_spec().model_dump(mode="json")
        )
    print(r.status_code, r.json()["job_id"])
    print(
        sorted(
            str(p.relative_to(syncthing_root)) for p in Path(syncthing_root).rglob("*")
        )
    )
```

```text
SYNCTHING_ROOT            = /tmp/tmpt04j03yp
os.environ[SYNCTHING_ROOT] = '/tmp/tmpt04j03yp'
get_syncthing_root()       = /tmp/tmpt04j03yp
get_job_state_file('job-1')= /tmp/tmpt04j03yp/jobs/job-1/state.yaml
POST /api/v1/jobs/ -> 201 {"job_id":"job-1051", ...}
files under SYNCTHING_ROOT after submit: []
```

`201 Created`, a row in the database, and an **empty folder**. The job is listed by `GET /jobs/`,
appears in the dashboard, and can be retried and cancelled — and no node will ever pick it up.

(`job-1051`, not `job-1`, because `backend/tests/conftest.py:191` turns `SEED_DEMO_DATA` on for
the whole session. Any test in this issue that asserts on a literal job id must request the
`seeding_disabled` fixture or read the id from the response. See AC-2 and the risk table.)

### The write is not the only gap — the file would be unreadable if written

This is the finding the issue body does not contain, and it is the reason this is not a one-line
change. `write_yaml` serialises with `data.model_dump()` — **python mode, not json mode**
(`shared/file_ops/yaml_utils.py:77`) — and `JobState.status` is a `JobStatus`, a
`(str, Enum)` member (`shared/schemas/job_status.py:8`). PyYAML therefore emits a Python-object
tag that `read_yaml`'s `yaml.safe_load` (`:39`) refuses:

```text
--- jobs/job-1/state.yaml as written by write_yaml ---
job_id: job-1
spec: {...}
status: !!python/object/apply:shared.schemas.job_status.JobStatus
- PENDING
node_id: null
created_at: 2026-10-14 03:54:46.050575
...
ROUND TRIP: FAILS -> ConstructorError: could not determine a constructor for the tag
  'tag:yaml.org,2002:python/object/apply:shared.schemas.job_status.JobStatus'
```

```text
--- with model_dump(mode='json') ---
status: PENDING
created_at: '2026-10-14T03:54:46.087450'
ROUND TRIP: OK -> status=<JobStatus.PENDING: 'PENDING'> created_at=datetime(2026, 10, 14, 3, 54, 46, 87450)
```

The backend's watcher wraps `_process_job_file` in a bare `except Exception` that **logs and
drops** (`syncthing_service.py:277-278`), so an unreadable file produces no job change and no
visible failure — the cluster looks empty, exactly as in #95. Any agent reading the file with
`read_yaml(..., JobState)` gets the same `ConstructorError`.

`NodeSpec` is unaffected — it has no enum field, only `status: str` and a `datetime` — and it
round-trips today and under `mode="json"` alike. Verified both ways. `write_yaml` has exactly one
production caller today, `agent/hardware.py:187`, which writes a `NodeSpec`.

### Corrections to the issue body's line references

The body is right about the substance. Every `file:line` citation in it is either stale or
wrong, and two of its factual claims about `SYNCTHING_ROOT` are wrong in a way that changes the
proposed approach. Current state:

| Issue body | Actual |
|---|---|
| `database_store.py:314-331` (`create_job`) | `:334-353` |
| `database_store.py:318` (`self._job_counter += 1`) | `:338` |
| `database_store.py:453-461` (metric purges in `delete_job`) | `:474-482` (`delete_job` is `:460-486`) |
| `job_service.py:53-71` (`retry_job`) | `:54-72` |
| `job_service.py:73-88` (`cancel_job`) | `:74-89` |
| `path_utils.py:106` (`get_job_state_file`) | `:105-118` |
| `yaml_utils.py:55` (`write_yaml`) | `:56-110` |
| `backend/main.py:107` — *"the watcher is not started when it [SYNCTHING_ROOT] is unset"* | **Wrong.** `main.py:131` starts it unconditionally, and `settings.SYNCTHING_ROOT` is a `str` with the truthy default `/tmp/syncthing` (`config.py:304`), so there is no "unset" state on this path at all |
| `syncthing.py:32-36` — `GET /syncthing/status` returns 503 when unset | **Half dead.** `not settings.SYNCTHING_ROOT` is never true; and `Path(...).exists()` is true as soon as `SyncthingService.start()` has run, because `start()` does `jobs_dir.mkdir(parents=True, exist_ok=True)` (`:164-167`). The 503 branch is unreachable after startup |
| `docs/spec.md:293` says node registration is "Not yet wired" | `:301`, and it is about `POST /nodes/register`, not the folder |

The last two matter because the issue builds its proposed approach 5 ("Handle the
`SYNCTHING_ROOT` unset case") on them. See Q4.

---

## 2. Root Cause

This was specified and never built. `docs/api-server-core-spec.md:77` requires `POST /jobs` to
*"Persist job to database and write initial state.yaml to Syncthing folder"*, and `:87` requires
`DELETE /jobs/{id}` to *"Remove job from database and Syncthing folder"*. Neither exists. The
`shared/` helpers both operations need are built, tested and unused: `write_yaml`
(`yaml_utils.py:56`), `get_job_state_file` (`path_utils.py:105`), `ensure_directory`
(`path_utils.py:35`).

The same gap exists on **two more paths**, not one:

| Path | Code | What it does to the folder |
|---|---|---|
| `POST /jobs/` | `database_store.py:334-353` | nothing |
| `POST /jobs/{id}/retry` | `job_service.py:54-72` | nothing — resets 6 fields and increments `retry_count` in the database only |
| `DELETE /jobs/{id}` | `jobs.py:111-120` → `database_store.py:460-486` | nothing — purges `gpu_metrics`, `cpu_metrics`, `log_entries` and the job row |

`retry_job` is the same bug on a second path, and it is the more insidious one: a retried job
looks healthy in the UI (status `PENDING`, `retry_count` incremented) and is still invisible.

`DELETE` is not a cleanup, it is a data-loss hazard, and the ordering is load-bearing — see §5.4.

---

## 3. Open Questions — Resolved

The issue left four questions open: two are resolved here (Q1, Q2), one is answered as
"nothing to do here" and deferred for its observation (Q3), and one is explicitly deferred to a
named issue (Q4). Three further questions the analysis raised are also decided (Q5, Q6, Q7).

### Q1 — Where does `job_id` allocation belong? **Resolved: it already has a durable anchor.**

The body asks whether `self._job_counter` can reissue an id after a restart. **It cannot, and
this is already fixed.** `_ensure_seeded` (`:264-288`) calls `_sync_job_counter` (`:290-302`)
**unconditionally** — the call at `:287` sits *outside* the `if settings.SEED_DEMO_DATA` block at
`:276`, so it runs even with demo seeding off. `_sync_job_counter` selects every `job_id` from the
table, parses the numeric suffix, and raises the counter to the maximum.

```console
$ python -m pytest backend/tests/unit/test_seed_flag.py -k "counter or collision" -q
5 passed, 24 deselected
```

Pinned by three tests in that file: `test_job_counter_syncs_when_seeding_disabled:265` (a fresh
`DatabaseStore` over a table holding `job-1050` issues `job-1051`),
`test_no_id_collision_with_existing_low_ids:305` (low ids `job-1`/`job-2` arriving via the folder
do not collide) and `test_counter_starts_at_one_on_a_virgin_database:339` (`job-1` is correct on a
virgin database — pinning it so nobody "fixes" it with a constant).

`create_job_with_id` (`:355-379`) additionally raises the counter from ids it ingests at `:375-377`,
so a job that arrives via the folder also moves the counter forward.

**The residual is real and is narrower than the issue suggests.** The counter is derived from the
**database**, not from the **folder**. So a wiped database with a surviving `jobs/` directory
restarts the counter at 1, and `POST /jobs` allocates `job-1` whose directory already exists.
Before this issue that was harmless — the folder was never written. After this issue it is not:
the new job's `state.yaml` **overwrites another job's file**, and the backend's own watcher then
reads that file and applies the new job's `status`/`node_id`/`started_at`/`completed_at`/
`exit_code`/`error` to the *other* job's row (`syncthing_service.py:258-269`, which updates those
seven fields). Silent cross-job corruption, no error anywhere.

`DatabaseStore.reset()` reaches this state in-process: it sets `_job_counter = 0` (`:601`) and
`_seeded = True` (`:609`), so the counter is never re-derived.

**Decision: detect here, fix elsewhere.** §5.2 adds a clobber guard on create — if
`<root>/jobs/<job_id>/` already exists at submission time the id has been reissued, and the
request fails loudly instead of overwriting. The root fix (deriving the counter from the folder as
well, or making the id durable) changes id allocation, which #99 and #100 inherit, so it is a
**new issue**, not this one. See §10.

### Q2 — `state.yaml` directly, or a separate `request.yaml`? **Resolved: `state.yaml` directly. Settled by the operator, not re-opened.**

The backend writes `jobs/<id>/state.yaml` itself. No second file, no second ingestion path.

- This is what #105's own acceptance criteria describe ("confirm a file-only agent observes the
  new `state.yaml`"), and what `docs/api-server-core-spec.md:77` specifies.
- `state.yaml` is already watched by the agent (`agent/watcher.py:103-108` filters on `.yaml` and
  enqueues everything under `jobs/`), already parsed by the backend (`:253`), and already validated
  by `JobState`. A `request.yaml` would be a third path shape that
  `SyncthingEventHandler._process_file` (`:108-124`) **silently ignores**, because it routes only
  `jobs/*/state.yaml` and `nodes/*.yaml`.
- Two files per job doubles the replication cost for a job whose whole content is one record.

**Consequence to record — `state.yaml` now has two writers.** The backend writes on create and on
retry; the agent writes on claim and on terminal state (#97, #100). Each write is whole-file and
locally atomic (`write_yaml` → temp file → `os.replace`, `yaml_utils.py:100`), but there is no
mutual exclusion and Syncthing resolves conflicts last-writer-wins. §5.5 is the ownership table
that keeps this safe.

**Consequence to record — the two writers must serialise identically.** Syncthing compares file
content, so if the backend emitted `created_at: 2026-10-14 03:54:52.469756` and the agent emitted
`created_at: '2026-10-14T03:54:52.469756'` for the same instant, every single write would be a
conflict. That is what makes the `mode="json"` fix in §5.1 a correctness requirement and not a
tidiness fix: both writers go through `write_yaml`, so one change fixes both sides.

### Q3 — What if Syncthing has not yet replicated the file when the agent reads it? **Resolved: nothing, here. Deferred to #103.**

The backend's write is local to its own node. It has no visibility into replication and no
mechanism to acquire one — it never speaks to Syncthing, only to the folder. A "waited long
enough" retry would need a signal the backend structurally does not have.

What *is* contractually guaranteed is local and testable: **before `POST /jobs` returns 201, the
file exists and is complete on the backend's own node**, written atomically so a concurrent reader
sees either the previous file or the whole new one. That is AC-3. Delivery to other nodes is
Syncthing's problem and #103's (crash/restart recovery) — it is the issue that already reasons
about what the folder durably holds after a restart.

### Q4 — What is the behaviour when `SYNCTHING_ROOT` is unset? **Resolved: the question is not well-formed for the backend, and the reason matters.**

The issue proposes handling it and cites two facts about it that are both wrong (§1's correction
table). The real situation:

| | `settings.SYNCTHING_ROOT` | `os.environ["SYNCTHING_ROOT"]` |
|---|---|---|
| source | env, `.env`, default `/tmp/syncthing` (`config.py:304, 415-419`) | `os.environ` only (`path_utils.py:57`) |
| type | `str`, never `None`, **always truthy** | `str` or `None` |
| unset ⇒ | `/tmp/syncthing` | `None` |

Two channels, one name, and they disagree. `path_utils` reads the second; the watcher
(`main.py:131`), `/health` (`main.py:347`) and `/syncthing/status` (`syncthing.py:41`) read the
first. `path_utils` has no injection point at all — recorded as defect **C8** in
`docs/specs/93-agent-skeleton.md:306` and as the reason `agent/paths.py:1-8` builds
`AgentPaths` from the settings value instead.

**Decision: the backend write path uses `settings.SYNCTHING_ROOT`**, via a new injectable
resolver (§5.1) modelled on `agent/paths.py:49-71`. Writing through `get_job_state_file` would
mean writing into the `os.environ` root while the watcher watches the `settings` root — two roots
for one folder, which is the same class of defect as #34 (two SQLite files for one database) and
#58 (`.env` found from one CWD and not another). Both disagreements are reachable today: an
operator who sets `SYNCTHING_ROOT` only in `.env` gets exactly that split, because
`Settings` reads `.env` (`config.py:417`) and `os.environ` does not.

Consequently **`None` is unreachable on this path**, which discharges the issue's AC *"does not
depend on a `None` path from `get_job_state_file`"* by construction rather than by a guard.
AC-9 pins it.

**And "unset" for the backend means `/tmp/syncthing`.** That is the documented default, the
watcher already watches it and has already created `jobs/` and `nodes/` inside it
(`syncthing_service.py:164-167`), and `/health` reports it as `config.syncthing_root`. Writing
there is consistent with everything else the backend already does with that path. It is also
*inert* — the submitted job is one no agent will ever see — but an inert job is not the bug this
issue is fixing: the bug is a job that claims to be queued and is not. **Decision: write to
`settings.SYNCTHING_ROOT` whatever it is, including the default. No new gate is added.**

The residual — *an operator who forgets to set it submits jobs into `/tmp/syncthing` and they are
silently inert* — is a configuration defect, not a submission defect, and it predates this issue.
Fixing it means removing the default, which changes `/health`, `/syncthing/status`, watcher
start-up and the agent's own `SYNCTHING_ROOT` handling (which already refuses to start without
one, `agent/config.py:335-339`). Out of scope; new issue, cross-linked to #32 in §10.

### Q5 — What happens when the folder write fails? **Settled by the operator: `503 SYNCTHING_UNAVAILABLE`.**

Recorded here with its reasoning rather than re-litigated. `syncthing_unavailable(detail)` already
exists at `backend/core/errors.py:154-157` and already renders through the `ErrorResponse` the
frontend parses; `docs/spec-error-handling.md:66` already lists the code. The alternative —
return 201 and mark the job unusable — reproduces today's symptom one level further away: a job
that looks queued and is not. **No new error code is introduced.**

### Q6 — Does `cancel_job` need to touch the folder? **Deferred to #106, explicitly.**

`job_service.py:74-89` writes `CANCELLED` to the database only, and under file-only the agent
cannot see it. That is BLOCKER 2 on #92 and it is issue **#106** (command files for operator
intent). **This issue must not add a cancel file.** Recording it so the two are not confused:
#105 makes the *submission* half of `retry_job` work because retry re-submits an already-existing
job through the same `state.yaml`; it does not attempt cancel, which is a new command protocol
and needs a new directory or file shape plus a watcher routing branch that does not exist yet
(`syncthing_service.py:108-124`).

### Q7 — Should the backend ever overwrite agent-written fields? **No. Enforced by an ownership table, not by a lock.**

The issue's proposed approach 6 is right and is §5.5. The backend writes `state.yaml` **only** on
create and on retry, and only the fields that make a job newly-claimable. It never writes a file
for a job that has left `PENDING` except through `retry_job`, which is already gated on
`status in (FAILED, CANCELLED)` (`job_service.py:59-60`). The remaining race — an agent's terminal
write that has not yet replicated to the backend's node when `retry_job` runs — is a monotonicity
problem and is owned by **#100**, not here. §5.5 cross-references it.

---

## 4. Scope Boundaries

| In Scope | Out of Scope |
|---|---|
| `POST /jobs/` writes `jobs/<job_id>/state.yaml` before returning 201 | Claiming a job (#97) |
| `POST /jobs/{id}/retry` re-emits the same file | Launching a process (#98) |
| `DELETE /jobs/{id}` removes `jobs/<job_id>/` | Terminal state, `exit_code`, monotonicity (#100) |
| `write_yaml` serialises so a `JobState` round-trips (§5.1) | Cancellation propagation (#99) |
| An injectable backend-side Syncthing path resolver (§5.1) | The cancel/retry **command** protocol (#106) |
| A clobber guard that refuses to overwrite an existing job directory (§5.2) | Log capture / ingest (#101), metric sampling (#102) |
| Failure surfacing as `503 SYNCTHING_UNAVAILABLE` with compensating rollback | Removing the `SYNCTHING_ROOT` default (Q4, D6) |
| Docs: `docs/spec.md` submission path, this spec | Folder ACLs as a security boundary (#104) |

### Couplings recorded, not fixed

- **#100 — monotonicity.** The terminal-state overwrite race is #100's. This spec narrows the
  backend's write set so #100 has a stable precondition to build on.
- **#97 — the discovery half.** Nothing here makes a job *discoverable*; it makes it *present*.
  #97 chooses how the agent finds it and must cope with the `job-\d+` id constraint that #105's
  allocation satisfies.
- **#103 — reconciliation.** Two residuals in §8 (the crash window between commit and write, and
  the wiped-database counter) are both "the folder and the database disagree" problems. #103 is
  the issue that owns reconciliation.
- **#106 — commands.** `retry_job`'s *file* half lands here; its *command* half does not.

---

## 5. Implementation Approach

### 5.1 Two prerequisites, both of which the naive one-liner skips

**(a) `write_yaml` must serialise in json mode.** `yaml_utils.py:77`:

```python
yaml_data = data.model_dump()  # current: python mode
```

becomes `data.model_dump(mode="json")`. Verified consequences:

| Model | Today | After |
|---|---|---|
| `JobState` | `status: !!python/object/apply:...JobStatus` — **unparseable by `read_yaml`** | `status: PENDING` — round-trips |
| `NodeSpec` | round-trips (verified) | round-trips (verified), with `last_heartbeat` as an ISO string instead of a YAML timestamp |

There is one production caller, `agent/hardware.py:187`, and it writes a `NodeSpec`. The
`agent/tests/test_hardware.py:546-556` AST guard ("one write, one place to audit") constrains
*where* `write_yaml` is called in that module, not how it serialises, so it does not fire.
`update_yaml` (`:142`) has no production caller.

The reason this is in scope and not a drive-by: **two writers, one file, Syncthing comparing
bytes** (Q2). Both writers must emit the same bytes for the same value or every write is a
conflict.

**(b) A backend-side, injectable path resolver.** `path_utils` has no injection point (C8), so the
backend must not go through `get_job_state_file`. A new small module — `backend/services/job_folder.py`
or `backend/core/syncthing_paths.py`, SWE's choice — exposing pure functions over an explicit root:

```python
def job_dir(root: str | Path, job_id: str) -> Path: ...  # <root>/jobs/<job_id>
def job_state_file(
    root: str | Path, job_id: str
) -> Path: ...  # <root>/jobs/<job_id>/state.yaml
```

modelled on `agent/paths.py:49-71`, for the same stated reason (`agent/paths.py:1-8`). AC-10
pins the two conventions together so they cannot drift. `ensure_directory` (`path_utils.py:35`)
and `write_yaml` (`yaml_utils.py:56`) are still used from `shared/`; only the *root* resolution
is new.

The write helper itself follows the sibling precedent `agent/hardware.py:168-189`
(`write_node_spec`): one function, path in, log the resolved path at INFO on every success so an
operator can compare it against the watcher, `write_yaml` doing the atomicity.

### 5.2 Create — `POST /jobs/`

```
1. created = await store.create_job(spec)          # row committed; returns JobState
2. if job_dir(root, created.job_id).exists():      # clobber guard (Q1)
       compensate(); raise <409>
3. try: write_yaml(str(job_state_file(root, created.job_id)), created)
   except OSError: compensate(); raise syncthing_unavailable(...)
```

**The row is written first and the file second, with a compensating rollback.** The alternative
— allocate the id, write the file, then insert — needs the id *before* the commit, and the
counter is `store._job_counter`, a private that
`backend/tests/unit/test_api_fixes.py:153-170` (`TestNoPrivateStoreAccess`) forbids the service
and router layers from touching. Reaching for it would fail a live test. DB-first costs one
compensating `delete_job` on the failure path and nothing on the success path.

The compensation is what makes "a job in the DB but not in the folder" impossible: on a write
failure the row is deleted, so **either both exist or neither does**. If the compensation *itself*
fails, log at **ERROR** with the `job_id` and still return 503 — the issue's "reported, not
swallowed" requirement, and a greppable orphan is the only honest outcome left (§8).

The clobber guard fires only on create. It is a loud failure instead of silent cross-job
corruption, and it is cheap: the id was just allocated from a counter synced to `MAX(job-N)`, so a
pre-existing directory means the database and the folder disagree (Q1).

The file content is the **exact `JobState` the store returned** — not a freshly constructed one.
That is what makes the filename's `job_id` byte-identical to the primary key, and what keeps
`created_at`, `status` and `retry_count` consistent between the two halves. The `JobState` already
has `status=PENDING`, `node_id=None`, `retry_count=0`, `started_at/completed_at/exit_code/error =
None` straight out of `create_job`, so there is nothing to assemble.

### 5.3 Retry — `POST /jobs/{id}/retry`

```
1. job = await store.get_job(job_id)               # None -> 404 JOB_NOT_FOUND
2. job.status not in (FAILED, CANCELLED) -> 409 JOB_NOT_RETRYABLE
3. updated = await store.update_job(...)            # the existing reset, job_service.py:62-71
4. try: write_yaml(str(job_state_file(root, job_id)), updated)
   except OSError: restore(job); raise syncthing_unavailable(...)
```

`restore(job)` is a second `update_job` carrying the snapshot's `status`, `node_id`,
`started_at`, `completed_at`, `exit_code`, `error` and `retry_count` back — the mirror of step 3,
so the rollback is exact rather than approximate. Same compensation discipline as create: log at
ERROR with the `job_id` if the restore fails, and still return 503.

**Retry writes unconditionally — it does not run the clobber guard.** The directory is *supposed*
to exist, and a retried job whose directory was lost (written before this issue landed, or a
folder that was reset) must come back. Retry is the repair path.

### 5.4 Delete — `DELETE /jobs/{id}`

```
1. job = await store.get_job(job_id)               # None -> 404, decided BEFORE any fs access
2. assert job_state_file(...).parent is inside <root>/jobs
3. shutil.rmtree(job_dir(root, job_id), ignore_errors=False)
       OSError -> 503 SYNCTHING_UNAVAILABLE; the row is left intact
   (FileNotFoundError -> not a failure: already absent, continue)
4. await store.delete_job(job_id)                  # result is NOT used to decide the status code
```

**The folder goes first. This ordering is forced, and the other order is a data-loss bug.**

Verified: a `state.yaml` whose database row is gone is **re-created** by the next scan.

```text
processed_files={'/tmp/tmpnckip08s/jobs/job-1051/state.yaml'}
row gone after DELETE: True
RESURRECTED BY RESCAN: True
```

`_process_job_file` takes the `else` branch at `:270-272` and calls `create_job_with_id`. So
delete-the-row-first leaves the directory on disk and the **next** ingestion path brings the job
back: the initial scan at service start (`_initial_scan`, `:217-241`), any watcher event on the
directory's parent, or `POST /syncthing/scan` → `manual_scan` (`:356-365`) →
`_process_new_files` (`:409-413`) → `_initial_scan`. The row comes back with its metrics and logs
gone and its terminal fields overwritten by the stale file. Delete-the-folder-first cannot do that.

**And the recursion the issue raises terminates.** Removing the directory fires the watcher's
`on_deleted` → `_process_delete` → `_handle_job_deleted` (`:310-314`) → `store.delete_job`, which
returns `False` because the row is already gone. Verified: the job is not present after the
watcher's reaction. It is a second delete of a missing row, not a loop.

**Which changes the route's status code.** Today `jobs.py:117-119` turns a `False` from
`delete_job` into a 404. After this change a `False` most often means *the watcher won the race
and the row is already gone* — the end state the client asked for. So the 404 must be decided at
step 1, from the pre-read, and step 4's return value must be ignored (logged at INFO). A delete
that happened returns **204**, not 404.

`rmtree` rather than unlinking one file: the whole directory must go, or it leaks and the
agent's per-job scratch survives a job the operator deleted. It cannot touch agent state —
`AGENT_STATE_DIR` defaults to `<root>/.agent/<node_id>` (`agent/config.py:67, 365`), not under
`jobs/`. AC-12 pins that.

### 5.5 Ownership — who may write which field, and when

Read down the "Backend" column for the complete set of backend writes. Anything not listed there
is the agent's, permanently.

| Field of `state.yaml` | Backend writes | Agent writes | Notes |
|---|---|---|---|
| `job_id` | on create (it *is* the path) | never | the directory name is the key |
| `spec` | on create; on retry (**unchanged**) | never | `_process_job_file` never passes `spec` to `update_job` (`:260-269`), so the folder can never change a job's command after submission |
| `created_at` | on create | never | same |
| `status` | `PENDING` **only**, on create and on retry | `RUNNING` (claim, #97 / start, #98), `COMPLETED`/`FAILED`/`CANCELLED` (#100) | a backend write that is not a transition *into* `PENDING` is a bug |
| `node_id` | `null` on create and on retry | on claim (#97) | |
| `started_at` | cleared to `null` on retry only | on start (#98), on terminal (#100) | |
| `completed_at` | cleared to `null` on retry only | on terminal (#100) | |
| `exit_code` | cleared to `null` on retry only | on terminal (#100) | `-1` means operator-cancelled (`job_service.py:87`), which #99 owns |
| `error` | cleared to `null` on retry only | on terminal failure (#100) | |
| `retry_count` | `n+1` on retry only | never | |

**Deletion is asymmetric and that is deliberate: the backend MAY delete `jobs/<job_id>/`; the
agent NEVER may.** Deleting `state.yaml` routes straight to `store.delete_job`
(`syncthing_service.py:310-314`), which purges `gpu_metrics`, `cpu_metrics` and `log_entries`
(`database_store.py:474-482`). The agent's only state is that file (#92 trap 2, #100 hazard 3),
so an agent that deletes its own job's file destroys the job, its history and its telemetry. The
delete site carries a comment saying so, and it is the only such comment in the repository
(AC-13).

### 5.6 Write ordering / failure matrix

**Create** — compensation = `store.delete_job(job_id)`:

| Step | Fails | Client sees | Left behind |
|---|---|---|---|
| 1 `store.create_job` | DB error | 500 (existing handler) | nothing |
| 2 clobber guard | id reissued | 409 `JOB_ID_COLLISION` (D7) | compensated → nothing |
| 3 `write_yaml` | `OSError` | **503 `SYNCTHING_UNAVAILABLE`** | compensated → nothing |
| 3 `write_yaml`, compensation *also* fails | DB error | **503** | **orphan row, logged at ERROR with the job_id** (§8) |
| — | *crash between 1 and 3* | *(no response)* | **orphan row, no file** (§8, → #103) |

**Retry** — compensation = `update_job` restoring the step-1 snapshot:

| Step | Fails | Client sees | Left behind |
|---|---|---|---|
| 1 `get_job` → None | — | 404 `JOB_NOT_FOUND` | untouched |
| 2 status not retryable | — | 409 `JOB_NOT_RETRYABLE` | untouched |
| 3 `update_job` | DB error | 500 | row unchanged (transaction rolled back) |
| 4 `write_yaml` | `OSError` | **503 `SYNCTHING_UNAVAILABLE`** | restored → row is back to `FAILED`/`CANCELLED` |
| 4 restore *also* fails | DB error | **503** | row is `PENDING` with no file; logged at ERROR (§8) |

**Delete** — no compensation, because the ordering already makes the failure modes safe:

| Step | Fails | Client sees | Left behind |
|---|---|---|---|
| 1 `get_job` → None | — | 404 `JOB_NOT_FOUND` | nothing touched (decided before any fs access) |
| 3 `rmtree` | `OSError` | **503 `SYNCTHING_UNAVAILABLE`** | row intact **and** file intact — consistent, and crucially **not** resurrected |
| 3 `rmtree` | directory already absent | not a failure | continue to step 4 |
| 4 `delete_job` | DB error | 500 | directory gone; the running watcher deletes the row anyway. With no watcher running the row lingers with no file (§8, → #103) |
| 4 returns `False` | watcher won the race | **204** (not 404) | end state achieved |

### 5.7 The backend's own watcher re-reads what the backend just wrote

Verified benign, and the spec says so because a reader deserves evidence rather than reassurance:

```text
job_id=job-1051 path=/tmp/tmpuaqdf0sj/jobs/job-1051/state.yaml
status after watcher read-back: JobStatus.PENDING
```

`create_job` commits `PENDING` and the file says `PENDING`, so `_process_job_file` takes the
`existing_job` branch (`:258-269`) and `update_job` writes back the same seven values. The
`else` branch that calls `create_job_with_id` (`:272`, which raises `IntegrityError` on the
`job_id` primary key) is unreachable for a job the backend just created — **verified by
exercising both branches, not by reading them.** AC-6 asserts it, including the negative case, so
the reassurance cannot rot when the branch order changes.

Cost: one redundant `UPDATE` per submission. AC-16 pins the count so it is noticed if it grows.

`write_yaml`'s temp files are `state.yaml.<rand>.tmp` (`yaml_utils.py:92-94`) and both watchers
skip them — verified, `_is_relevant_file(tmp) = False`, `_is_relevant_file(state.yaml) = True` —
because the name ends in `.tmp` (`syncthing_service.py:60`, `agent/watcher.py:108`).

---

## 6. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **DB first, file second, compensating rollback** | The id is allocated inside `create_job`, behind a private the API layer may not touch (`test_api_fixes.py:153-170`). DB-first needs no new store API; file-first would change id allocation for #99/#100 to inherit. |
| D2 | **Folder write failure ⇒ `503 SYNCTHING_UNAVAILABLE`** | Operator decision. A partial success is today's bug, moved. The helper exists (`errors.py:154`) and the code is already in the frontend's documented set. |
| D3 | **One file, `state.yaml`, written by the backend directly** | Operator decision. Already watched, parsed and validated; a second shape is silently ignored by `_process_file:108-124`. |
| D4 | **All three paths land here**: create, retry re-emit, delete removes the directory | Operator decision. Retry is the same bug on a second path; leaving it would ship a fix that works once. |
| D5 | **The backend resolves the root from `settings.SYNCTHING_ROOT`**, not `os.environ` | The watcher, `/health` and `/syncthing/status` all use `settings` (Q4). Two roots for one folder is #34/#58's class of bug. Makes `None` unreachable. |
| D6 | **No gate on the `/tmp/syncthing` default** | It is the documented default and the watcher already watches and creates it. An inert job is not this bug; removing the default is a config change with a wide blast radius (new issue). |
| D7 | **A clobber guard on create only, failing `409`** with a new `APIError` code `JOB_ID_COLLISION`, built the way `job_not_retryable`/`job_not_cancellable` are (`errors.py:132-151`) | Converts the wiped-DB counter residual from silent cross-job corruption into a loud failure (Q1). Retry legitimately reuses a directory and must not be blocked |
| D8 | **Delete removes the folder first, and the 404 is decided by a pre-read** | Folder-second resurrects the job on the next scan (verified). A `False` from `delete_job` means the watcher won the race, which is success. |
| D9 | **The backend may delete a job directory; the agent never may** | Traps #92-2 / #100-3. Encoded in §5.5 and AC-13. |
| D10 | **No cancel file, no retry command file** | #106. Retry here is the *submission* half of an existing route, not a new command protocol. |
| D11 | **`openapi.yaml` needs no regeneration** | Verified: the snapshot documents only `200/201/204/404/422` and the live `app.openapi()` documents exactly the same set — the 503s are raised inside handlers and declared in no `responses={}`. A test asserts the two stay identical (AC-14). |

---

## 7. Acceptance Criteria

The issue's eleven checkboxes are the backbone. None was dropped and none was left as a comment
about the code — each is an assertion with a named test. Where the origin map below says
"AC-*n*", that criterion *is* the issue's, sharpened; the rest is what this analysis added.

| Origin | Where it went |
|---|---|
| Issue AC 1 (`state.yaml` created with PENDING/null/full spec) | AC-1 |
| Issue AC 2 (filename `job_id` byte-identical to the DB) | AC-2 |
| Issue AC 3 (end-to-end, no HTTP, file-only agent observes the file) | AC-3 |
| Issue AC 4 (write failure surfaced, not swallowed, tested) | AC-4 |
| Issue AC 5 (`retry_job` re-emits, test retries then observes) | AC-5 |
| Issue AC 6 (`DELETE` removes the directory) | AC-7, AC-8 |
| Issue AC 7 (backend may delete / agent never may, documented + enforced) | AC-13 |
| Issue AC 8 (`SYNCTHING_ROOT` unset has defined behaviour) | AC-9, AC-10 |
| Issue AC 9 (backend write never clobbers agent terminal fields) | AC-15, §5.5 |
| Issue AC 10 (`openapi.yaml` regenerated *if* a shape changes; assert, don't assume) | AC-14 |
| Issue AC 11 (docs updated; `docs/spec.md:301`) | AC-19 |
| — | AC-6, AC-11, AC-12, AC-16 … AC-18 added by this analysis |

### Functional

- **AC-1** — `POST /jobs/` creates `<settings.SYNCTHING_ROOT>/jobs/<job_id>/state.yaml` whose
  content, read back from disk with `read_yaml(path, JobState)`, has `status == PENDING`,
  `node_id is None`, `started_at/completed_at/exit_code/error is None`, `retry_count == 0`, and
  a `spec` equal to the submitted one. Assert on the **file**, not on a mock.
  *Proved by* an integration test posting through `ASGITransport(app=app)` and reading the file
  with the `syncthing_root` fixture.

- **AC-2** — The `job_id` in the path is byte-identical to the `job_id` in the database. Asserted
  by a test that reads **both** — the API response and the directory listing — and compares them,
  not by reusing a variable.

- **AC-3** — The end-to-end path with **no HTTP anywhere**: submit through the API, then assert a
  **real `agent.watcher.FolderWatcher`** over the same `SYNCTHING_ROOT` enqueues a `created` event
  for that `state.yaml` within a bounded interval. The agent's filter
  (`agent/watcher.py:103-108`) admits it and the `SelfWriteLedger` (`:116-117`) does not suppress
  it, because the agent did not write it. No mock, no monkeypatched watcher.
  *Proved by* an integration test that constructs a real `FolderWatcher` against the fixture root.

- **AC-4** — A folder-write failure is surfaced, not swallowed: `write_yaml` raising `OSError`
  yields **503** with `error_code == "SYNCTHING_UNAVAILABLE"`, on **all three** paths
  (create, retry, delete). Assert the status code, the `error_code`, and — separately — that the
  rollback ran (no row survives a failed create; the pre-retry status survives a failed retry).

- **AC-5** — `POST /jobs/{id}/retry` re-emits `state.yaml` for a `FAILED` job and the file is
  observable afterwards with `status: PENDING`, `node_id: null`, `retry_count: n+1`. Retry is also
  tested for a job whose **directory is missing** — the write must recreate it.

- **AC-6** — The backend's own watcher round-trip is proven benign, **including the branch that
  would break**: after a create, drive the real `_process_job_file` over the file the backend
  wrote and assert the job is still `PENDING` and still exactly one row; then drive
  `create_job_with_id` for a *duplicate* id and assert it raises `IntegrityError` — proving that
  if the `existing_job` branch at `:258-269` ever stopped matching, the create branch at `:272`
  would not silently paper over it.

- **AC-7** — `DELETE /jobs/{id}` removes `jobs/<job_id>/`: after the call the directory does not
  exist. Asserted on the filesystem.

- **AC-8** — **Delete does not resurrect.** Drive the real `_initial_scan` after the delete and
  assert the job is still gone. This is the regression guard for the §5.4 ordering; without it a
  reordering that deletes the row first passes every other AC in this list.

- **AC-9** — Submission never depends on a `None` from `get_job_state_file`. With
  `SYNCTHING_ROOT` absent from `os.environ` entirely, `POST /jobs/` still writes the file under
  `settings.SYNCTHING_ROOT`. Asserted by popping the variable, pointing `settings` at a tmp
  directory, and posting. (Note: `conftest.py:348-360` sets `SYNCTHING_ROOT` for the whole
  session, so the test has to pop it explicitly — which is exactly the point.)

- **AC-10** — The new backend resolver and `shared.file_ops.path_utils` agree. With
  `os.environ["SYNCTHING_ROOT"]` **and** `settings.SYNCTHING_ROOT` pointed at the same directory,
  `job_state_file(root, jid) == get_job_state_file(jid)` for several ids. This is the tripwire
  that keeps C8 from turning into a real split — the direct analogue of
  `agent/tests/test_hardware.py:531-543`.

- **AC-11** — **`write_yaml` round-trips every model the folder carries**: `JobState` and
  `NodeSpec` both survive `write_yaml` → `read_yaml` without raising. `JobState` is the one that
  fails today, and it fails with a bare `ConstructorError` that `syncthing_service.py:277-278`
  swallows. Assert both, and assert the `status` field comes back as `JobStatus.PENDING` rather
  than a string.

- **AC-12** — Delete removes `jobs/<job_id>/` and nothing else: `AGENT_STATE_DIR`
  (`<root>/.agent/<node_id>`) and every other job's directory still exist afterwards.

- **AC-13** — **The ownership rule is enforced in review, not just documented.** A comment at the
  delete site states that the backend may delete a job directory and the agent never may, and
  cites the purge it triggers (`database_store.py:474-482`). A test asserts the comment is
  present — the way `test_seed_flag.py:413-438` asserts `.env.example`'s contents — so a later
  edit that drops it fails CI rather than passing review by inertia.

- **AC-14** — **`openapi.yaml` is unchanged, and that is asserted rather than assumed.**
  `app.openapi()` for `/api/v1/jobs/`, `/api/v1/jobs/{job_id}` and
  `/api/v1/jobs/{job_id}/retry` reports exactly the response codes it reports before this change
  (`201/200/204` + `422`, `200/204` + `422`). The 503s are raised inside handlers and declared in
  no route's `responses={}`, so they are invisible to the generator — verified today, where
  neither the snapshot nor the live schema contains a `503` anywhere. A test compares the two
  dicts.

- **AC-15** — **A backend write never clobbers an agent-written field.** The sequence the issue
  is really worried about — the agent writes `RUNNING`/`node_id`, and the backend retries over the
  top — is **#100's** monotonicity race, and it is not implemented here. What *is* pinned here is
  the precondition #100 needs, and it is the part that is cheap and would otherwise rot silently:
  **`_process_job_file` must remain incapable of changing a job's `spec` or `created_at` from the
  folder** (`syncthing_service.py:260-269` never passes them to `update_job`), and `retry_job` must
  remain gated on `FAILED`/`CANCELLED`. Assert both: a `state.yaml` carrying a different `spec`
  than the row leaves the row's `spec` untouched, and `retry_job` on a `RUNNING` job is a 409. The
  monotonic terminal-state rule itself is #100's; this AC names it as such rather than pretending
  to implement it.

- **AC-16** — Write amplification is bounded: one `INSERT` + **exactly one** redundant `UPDATE`
  per submission (the watcher's read-back), and nothing more. Asserted by counting `update_job`
  calls with the real `_process_job_file` driven once.

### Quality

- **AC-17** — `ruff check . && ruff format --check . && mypy .` all exit 0. No new
  `type: ignore` and no widening of the overrides in `pyproject.toml`. The only non-test overrides
  that exist are `backend.core.deps` (`:72-73`) and `backend.api.v1.jobs` (`:75-77`) — note
  `syncthing_service.py` is **not** among them despite what #54 and #92's revision comment say, so
  it is type checked and `mypy backend/services/syncthing_service.py` exits 0 today. Because the
  route file is excluded, the submission/retry/delete logic must live in the service layer where
  it is checked.

- **AC-18** — `TestNoPrivateStoreAccess` (`backend/tests/unit/test_api_fixes.py:153-170`) still
  passes: `job_service.py`, `syncthing_service.py` and `backend/api/v1/*.py` contain no
  `(store|_store)\._[a-z]`. This is the mechanical guard behind D1.

- **AC-19** — Docs updated: `docs/spec.md`'s "Backend Integration" section describes the real
  submission path (it currently says nothing about the folder), and this spec is linked from
  #105. `docs/api-server-core-spec.md:77` and `:87` are the requirements being satisfied and stay
  as the historical record.

- **AC-20** — `pytest -q` green, and **not fewer than the baseline measured on `main` before any
  change**: `789 passed, 29 skipped, 818 collected` in 74 s (2026-10-14). `.shc/secret_key` is
  unchanged by the run — the `guard_signing_key` fixture from #65 must not fire — and
  `assert_no_db_in_source_tree` (`conftest.py:230`) must not fire either, which is the check that
  a test did not reach the real `data/` database (#34). The two modules this issue touches most
  directly are green today: `backend/tests/unit/test_shared_file_ops.py` +
  `backend/tests/unit/test_api_fixes.py` → **50 passed**; `agent/tests/test_hardware.py` → **34
  passed**.

---

## 8. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **`write_yaml` emits an unreadable `JobState`** — the file is written, the watcher drops it with a bare `except`, and the cluster looks empty | **Certain today** (§1) | **Critical** — the fix would ship a file nobody can parse | AC-11 round-trips `JobState` *and* `NodeSpec`; AC-1 reads the file back through `read_yaml`; §5.1(a) is a prerequisite, not a nice-to-have |
| **Delete resurrects the job** — row removed, directory left, next scan calls `create_job_with_id` | Medium (a plausible reordering, or a future "delete the row first because it's cleaner") | **High** — a deleted job returns with the wrong terminal data | AC-8 drives the real `_initial_scan` after the delete; §5.4 states the ordering and why |
| **Wiped DB + surviving `jobs/` reissues an id and overwrites another job's file** | Low (restore mismatch, `store.reset()`, a fresh container on a mounted folder) | **High** — silent cross-job corruption, no error anywhere | D7 clobber guard turns it into a loud 409 on create; the root fix (counter must consider the folder) is a **new issue** (§10) |
| **The agent deletes `state.yaml`** — which deletes the job, its metrics and its logs | Low (nothing tells it to) | **Critical** — total loss of one job and its history | D9; AC-13 pins the rule at the only site that can act on it; #97/#100 must not introduce a cleanup path |
| **Crash between the DB commit and the file write** leaves a permanent phantom row | Low | Medium — the exact bug this issue fixes, for in-flight requests only | Unfixable at this layer without an id-first design (D1). **Deferred to #103**, which owns folder↔database reconciliation. Recorded, not solved |
| **Retry write succeeds, DB restore fails** leaves a `PENDING` row with no file | Very low (two consecutive DB failures) | Medium | Log at ERROR with the `job_id` so it is greppable; → #103 |
| **`rmtree` succeeds, `delete_job` fails, no watcher running** — orphan row with no directory | Very low | Low | With the watcher running (the normal case) the deletion event cleans it up; without one it is an inert orphan row, not a resurrection. → #103 |
| **Syncthing replication cost: one more file per job.** Each submission fans out to every node, and the backend re-parses its own write on every node | Certain | Low–Medium | One small file per job, written once on create and once per retry — **two orders of magnitude below #102's per-10s metrics file**, which is the cost #92 flagged as needing measurement. Nothing to tune here; the binding cost is metrics, not jobs. AC-16 bounds the re-parse count |
| **The backend's watcher re-ingests its own write** and could in principle hit the `IntegrityError` branch | Low | Medium | Verified benign (§5.7); AC-6 proves both branches so a future branch reorder fails a test |
| **Two writers, last-writer-wins** — a retry's `PENDING` can race an unreplicated agent terminal write | Low (needs retry in the replication window) | Medium | Backend writes only on create and on retry, and retry is gated on `FAILED`/`CANCELLED`; the monotonicity rule is **#100's**, cross-referenced in §5.5 |
| **Test id literals break** — the session seeds demo data, so `POST /jobs` returns `job-1051`, not `job-1` | **High** if unnoticed | Low | AC-1/AC-2 read the id from the response; `seeding_disabled` for literal-id tests |
| **The `/tmp/syncthing` default silently swallows submissions** for an operator who forgot to set the root | Medium | Medium | Pre-existing (the watcher already creates and watches it); D6 records it as a config defect, **new issue** (§10), cross-linked to #32 |
| **Removing a job directory takes the agent's per-job scratch with it**, including on nodes that have not yet seen the job | Low | Low | Intended — that is what `DELETE` means. `AGENT_STATE_DIR` is outside `jobs/` (AC-12) |
| **`rmtree` on an unsanitised id** | Very low (ids are `job-\d+`) | High | §5.4 step 2 asserts the resolved path is inside `<root>/jobs` before removing |

---

## 9. Definition of Done

- [ ] Spec reviewed and issue labelled `groomed`
- [ ] Implementation complete (SWE)
- [ ] AC-1 … AC-20 verified with evidence (QA)
- [ ] `pytest -q` green; `ruff`/`mypy` clean
- [ ] `openapi.yaml` diff is empty (D11), asserted by AC-14 rather than asserted in a comment
- [ ] PM acceptance

---

## 10. Related

- **#92** — the transport decision. BLOCKER 1 of that decision. Option A taken strictly: no HTTP,
  no WebSocket, no JWT on the agent side. Its "revised roadmap" comment ranks this issue first:
  *"Nothing works without this."*
- **#95** — node registration, **done**. The sibling precedent and the style mirrored here:
  `agent/hardware.py:168-189` (`write_node_spec`), `agent/paths.py:49-71` (`resolve_paths`), and
  the mechanical AST tripwire at `agent/tests/test_hardware.py:546-556`.
- **#97** — discovery and claim. Consumes what this issue writes; owns `node_id` in the file.
- **#98** — execution. Owns `started_at`.
- **#99** — cancellation. **Blocked on #106**, not on this issue. `cancel_job` writes
  `CANCELLED` to the database only (`job_service.py:74-89`) and this issue deliberately does not
  touch it (D10, Q6).
- **#100** — terminal state, `exit_code`, `error`, retry ownership. Owns the monotonicity rule
  that AC-15 deliberately does not implement, and the agent-must-never-delete hazard that D9
  encodes.
- **#106** — the command-file protocol for operator intent. Takes cancel, and the retry *command*
  half.
- **#103** — crash/restart recovery. Owns folder↔database reconciliation, which is the right home
  for the crash window in §5.6 and the orphaned-row residuals in §8.
- **#32** — `/health` reports `degraded` for an idle-but-healthy cluster. Same neighbourhood as the
  `SYNCTHING_ROOT` default residual (Q4): both are "the configuration is missing and nothing says
  so".
- **#34** — CWD-relative `DATABASE_URL` produced two databases for one deployment. The reason D5
  refuses to let the folder have two roots is the same reason.
- **#36** — fabricated metrics/logs. Nothing here produces metrics or logs, but `delete_job`
  purges both (`database_store.py:474-482`), so the fabricated rows must be distinguishable before
  the delete path starts erasing real ones.
- **#54** — backend ruff/mypy. **Correction to #54 and to #92's revision comment**, both of which
  say `syncthing_service.py` is excluded from mypy (`pyproject.toml:70-80`): it no longer is. The
  only non-test overrides left are `backend.core.deps` (`:72-73`) and `backend.api.v1.jobs`
  (`:75-77`); `mypy backend/services/syncthing_service.py` exits 0 on `main` today. So this
  issue's behaviour depends most on a module that **is** type checked, and the route file is the
  one that is not — which is why AC-17 requires the logic in the service layer.
- **#58** — `.env` discovery is CWD-relative. The second way `settings.SYNCTHING_ROOT` and
  `os.environ["SYNCTHING_ROOT"]` come to disagree (D5).
- **#71** — `file_lock`'s dead `timeout`. Irrelevant here: this issue takes no lock and needs none.
- **New issue to raise — the counter must consider the folder.** `_sync_job_counter`
  (`database_store.py:290-302`) derives the id sequence from `MAX(job-N)` in the **database**,
  so a wiped database with a surviving `jobs/` reissues ids that already have directories (Q1).
  D7 makes it loud on create; the durable fix belongs here. Affects #99/#100, which inherit id
  allocation.
- **New issue to raise — `SYNCTHING_ROOT` has a truthy default on the backend and none on the
  agent.** `config.py:304` defaults to `/tmp/syncthing`; `agent/config.py:335-339` refuses to
  start without one. An operator who forgets it gets an inert backend and a refusing agent.
  Removing the default changes `/health`, `/syncthing/status` and watcher start-up (Q4, D6).