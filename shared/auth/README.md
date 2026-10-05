# `localhost_hosts.json`

The hostnames that get the authentication bypass, on **both** sides of the wire.

| Key | Meaning | Matching |
| --- | --- | --- |
| `exact` | Whole hostnames, canonical: lowercase and **bracket-free** | equality |
| `suffixes` | Domain suffixes, each with a leading dot | `hostname.endswith(suffix)` |

Consumers are two literal lists, one per language:

| File | Language | Function |
| --- | --- | --- |
| `backend/core/utils.py` | Python | `is_localhost()` |
| `frontend/src/lib/settings.ts` | TypeScript | `isLocalhost()` |

**Change the two lists together.** This file exists so the change is reviewable
in one place; it does not feed either list.

## Only tests read this file

That is deliberate, and it is the one exception in the design.

- `backend/tests/unit/test_utils.py` parametrizes `is_localhost()` over it.
- `backend/tests/unit/test_frontend_alignment.py` reads
  `frontend/src/lib/settings.ts` as **text** and asserts this file's entries are present there,
  that the removed Lovable preview suffix is absent, and that it normalises.

So drift between the two lists fails `pytest`, rather than being caught by a
reviewer who happens to be looking. That satisfies the goal behind sharing one
file without the build coupling.

## Why production code does not import it

`frontend/` is a **separate git repository** (a submodule), and the entire
JS/TS toolchain lives inside it:

- `frontend/tsconfig.json`'s `include` lists only paths relative to
  `frontend/`, so `../shared/…` is outside the TypeScript program and would
  not be typechecked.
- `frontend/vite.config.ts` does not own the alias configuration; it delegates
  to `@lovable.dev/vite-tanstack-config`, an external package this repository
  does not control. Making `src/` import `../shared/` would mean patching a
  vendored package's `resolve.alias` and `server.fs.allow`.
- A production build that reads a sibling repository's directory is a worse
  coupling than the duplicated list it would remove.

Tests can cross the boundary — pytest and vitest run with filesystem access.
The bundler cannot.

## Deliberate omissions

- `0.0.0.0` and `testserver` are **backend-only** (wildcard bind address, and
  Starlette `TestClient`'s default `Host`). The frontend must reject them, so
  they are not shared entries.
- `.lovableproject.com` is on **neither** list. It was removed from the
  frontend rather than added to the backend, because it is the one entry a
  third party could plausibly present in a `Host` header.

## Not a security boundary

The bypass reads a client-supplied `Host` header, so any client that can reach
the backend and set an arbitrary `Host` can already claim `localhost` and get
the bypass. These entries exist for developer convenience; adding a
loopback-shaped one grants no capability `localhost` does not already grant.
See `docs/spec.md#authentication`.
