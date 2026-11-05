# Spec: Profiles Backend Store (Issue #91)

**Issue**: #91 — "Profiles live only in the browser: the 9 built-ins are compiled into the frontend bundle and every custom profile dies with one localStorage key"
**Labels**: `enhancement`, `frontend`, `integration`, `phase-2`, `ready-for-grooming` → `groomed`
**Author**: PM
**Date**: 2026-11-04

---

## 1. Verified Problem Statement

Profiles are entirely client-side. `frontend/src/lib/profiles.ts` (313 lines) is the whole system: 9 built-ins compiled into the bundle, custom profiles in `localStorage` under `shc.profiles`, plus `overrides`/`hidden` collections. There is no backend half, no API, no validation, and no sharing across machines. The backend has no knowledge that a profile exists, so agents and the CLI cannot resolve a profile by id.

## 2. Decisions (Design Questions Resolved)

| # | Question | Decision |
|---|----------|----------|
| D1 | Editing a built-in | **Overlay, not mutation.** Built-in YAML files stay pristine. Edits are written as an overlay file under `backend/profiles/overrides/<id>.yaml`. The shipped file is never modified, so git stays clean. |
| D2 | `hidden` built-ins | A tombstone list file `backend/profiles/.hidden.yaml` (list of ids). Deleting a built-in appends to it; `restoreBuiltins()` clears it. |
| D3 | Filename vs id | `id` is stored in the file body and is authoritative. Filename is `<id>.yaml` (id is slugified + 4-char suffix at creation, matching today's `slugify()`). Built-in ids pinned to today's slugs. |
| D4 | Write API | **Yes.** Full CRUD on `/api/v1/profiles`. Read-only PR workflow rejected — the UI's create/edit/delete is the feature. |
| D5 | Auth on writes | Behind the shared bearer token (same as jobs/nodes). Localhost bypass applies. |
| D6 | Multi-node sync | `backend/profiles/` lives **inside** `SYNCTHING_ROOT` so it syncs. Concurrent edits resolve last-write-wins per file (atomic write + Syncthing conflict copies). No distributed lock. |
| D7 | Home directory | `backend/profiles/` (inside package) per requester's choice; path resolved CWD-independently via `Path(__file__).resolve()`. |
| D8 | Malformed file | Log at ERROR naming the file; skip it; endpoint returns 500 only if the requested profile is the malformed one, with `error_code=PROFILE_INVALID` and the filename in `detail`. |
| D9 | Retire localStorage | One-time import: on first backend start, if `shc.profiles` exists in a provided export, import; otherwise ignore. Frontend stops reading/writing `shc.profiles`. |
| D10 | Built-ins seed | 9 YAML files committed under `backend/profiles/`, byte-equivalent to `builtinProfiles`. |

## 3. Scope

**In scope**
- `shared/schemas/profile.py` — `ProfileSpec` (16 fields) + `ProfileEnvEntry`
- `backend/profiles/*.yaml` — 9 built-ins
- `backend/services/profile_service.py` — file-backed CRUD with locking
- `backend/api/v1/profiles.py` — router
- `openapi.yaml` update
- Frontend: `ClusterService` profile methods in http.ts + mock.ts; React Query; rewrite `profiles.test.ts`
- E2E: `tests/e2e/profiles.spec.ts` against live backend

**Out of scope**
- Profile versioning/history
- Cross-profile inheritance

## 4. Schema

```python
class ProfileEnvEntry(BaseModel):
    key: str
    value: str

class ProfileSpec(BaseModel):
    id: str
    name: str
    software: str
    description: str = ""
    command: str
    working_dir: str = ""
    gpus: int = 0
    cpus: int = 1
    memory_gb: int = 0
    vram_gb: int = 0
    input: str = ""
    output: str = ""
    max_retries: int = 0
    retry_delay_seconds: int = 0
    env: List[ProfileEnvEntry] = Field(default_factory=list)
    builtin: bool = False
```

## 5. API

| Method | Path | Notes |
|--------|------|-------|
| GET | /api/v1/profiles | list all (built-ins + customs, overlays applied, hidden excluded) |
| GET | /api/v1/profiles/{id} | single |
| POST | /api/v1/profiles | create (custom, builtin=false) |
| PUT | /api/v1/profiles/{id} | update; built-in writes overlay |
| DELETE | /api/v1/profiles/{id} | custom removes file; built-in appends tombstone |
| POST | /api/v1/profiles/{id}/reset | drop overlay + tombstone |
| POST | /api/v1/profiles/restore-builtins | clear tombstones + overlays |

## 6. Acceptance Criteria

- AC-1: All 9 built-ins exist as YAML under `backend/profiles/` and load through the same code path as a custom profile.
- AC-2: Round-trip test proves no field lost on write→read, including `env` ordering and `vram_gb`.
- AC-3: Profile directory path is CWD-independent (test from repo root and `backend/`).
- AC-4: Malformed profile file yields error naming that file.
- AC-5: New endpoints in `openapi.yaml`; `mock.ts` implements every one.
- AC-6: `shc.profiles` no longer read/written, or one-time import exists and tested.
- AC-7: Semantics table (hidden≠delete, reset restores, overrides apply) implemented and tested.
- AC-8: `npm run typecheck`, `npm run lint`, `npm test` clean; backend gates clean.
- AC-9: `tests/e2e/profiles.spec.ts` passes against live backend.

## 7. Definition of Done

- Spec reviewed and issue labelled `groomed`
- Implementation complete (SWE)
- AC-1…AC-9 verified (QA)
- Gates green
- PM acceptance
- Commit + push

---

*This spec is intake/grooming only. Implementation is the SWE's.*
