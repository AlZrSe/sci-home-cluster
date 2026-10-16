# Docker

Container image for the Scientific Home Cluster platform: a Node stage builds the
frontend, and a Python stage carries the backend plus the resulting static files.
The backend serves both, so the whole application is one image on one port.

## Build

The frontend is a **git submodule** (see `AGENTS.md`), not vendored here. A plain
clone leaves `frontend/` empty and the build fails deliberately rather than
producing an image whose UI 404s. Clone recursively:

```bash
git clone --recurse-submodules <url> scientific-home-cluster
cd scientific-home-cluster
```

or, in a checkout you already have:

```bash
git submodule update --init --recursive
```

Then build from the **repository root** — the build context is the root, not this
directory:

```bash
docker build -f docker/Dockerfile -t shc:latest .
```

`.dockerignore` lives at the repository root for that reason.

`docker-compose.yaml` lives there too, and passes the same two build arguments
with the same defaults, so `docker compose up -d` builds this image for you —
see [Run](#with-docker-compose-the-normal-path).

### Build arguments

Both default to a developer-machine setup. Override them for a real deployment:

```bash
docker build -f docker/Dockerfile \
  --build-arg VITE_API_URL=https://cluster.example.com/api/v1 \
  -t shc:latest .
```

| Argument | Default | Notes |
| --- | --- | --- |
| `VITE_CLUSTER_BACKEND` | `http` | Baked into the bundle. The exact string `mock` is the only value that selects in-memory demo data; anything else means the real backend. |
| `VITE_API_URL` | `http://localhost:8000/api/v1` | Default API base URL. Overridable at runtime from the app's settings page. |

> **`VITE_API_URL` must be absolute.** A same-origin relative base such as
> `/api/v1` will break the job-log WebSocket: `frontend/src/services/http.ts`
> derives its socket URL with `base().replace(/^http/, "ws")`, and
> `new WebSocket("/api/v1/...")` throws in a browser. Set it to the externally
> reachable URL. It is a build-time constant, so changing it needs a rebuild.

## Run

### With `docker compose` (the normal path)

`docker-compose.yaml` at the **repository root** describes the whole deployment:
it builds the image, mounts both state volumes, bind-mounts your Syncthing
folder, publishes port 8000, and restarts the container if the host reboots.
From the repository root:

```bash
# Nothing to configure. No .env is required.
docker compose up -d      # builds the image on first run
docker compose ps         # expect "(healthy)" within about 10 seconds
```

Then open <http://localhost:8000>. The API is at `/api/v1`, the docs at `/docs`,
and the SPA is served from `/` and every client-side route.

**`SHARED_TOKEN` is generated, not demanded.** If it is unset or empty the
application creates one on first start, saves it to `/app/.shc/shared_token`
(the `shc-state` volume) and logs a `WARNING` naming that file — never the
value. So:

```bash
docker compose exec backend cat /app/.shc/shared_token
```

The token is stable across restarts, and reading it back on a normal restart is
silent — the `WARNING` is for first start only.

This replaced a `${SHARED_TOKEN:?...}` guard that refused to start. That guard
was right as a refusal of a *guessable* token and useless as a refusal of an
unconfigured one: the cluster came up, the dashboard opened, and every login
answered `SHARED_TOKEN_NOT_CONFIGURED` with no hint what to do. Worse, on a
machine with no Python there was nothing available to generate the token with
except the image itself.

The cost, stated plainly: **a typo in an intended `SHARED_TOKEN` is no longer a
startup failure.** It yields a working cluster with a token nobody else has. The
`WARNING` exists to make that visible. Set the variable explicitly when more
than one machine authenticates against the same cluster, or when you want the
value in your own dotenv rather than in a volume — an explicit value always wins
and is never persisted.

`SYNCTHING_ROOT` defaults to `./syncthing`, a folder inside this checkout that
is created for you. See below.

Both are read from the repository-root `.env` automatically — compose interpolates
`${...}` from it without any `env_file:` key — or from your shell environment.

```bash
make env-init     # seed .env yourself (needs Python on the host)
make env-token    # print the current SHARED_TOKEN
python docker/init_env.py --rotate-token   # generate a new one
```

One thing compose does that the `docker run` line below does not:

- **The volumes have pinned names.** `shc-data` and `shc-state` are declared with
  `name:`, so they are not prefixed with a project name derived from your checkout
  directory. That keeps `docker volume ls` a reliable way to find your state, and
  keeps them shared with the `docker run` form below.

Compose does not restart an unhealthy container, and `restart: unless-stopped`
restarts on exit rather than on ill health. Observe health with `docker compose ps`
and recover with `docker compose restart backend`.

### `DB_POOL`: do not override it

The image bakes `DB_POOL=null` (`docker/Dockerfile:128`) and compose inherits
that value. **Do not add `DB_POOL: pooled` to the service**, even though `pooled`
is `Settings.DB_POOL`'s own default (`backend/core/config.py:330`) and looks like
the correct production setting. It does not start:

```
sqlalchemy.exc.ArgumentError: Pool class QueuePool cannot be used with asyncio engine
```

`backend/core/database.py:108` maps anything other than `"null"` to `QueuePool`,
and SQLAlchemy 2.0 rejects a bare `QueuePool` on an async engine. It is raised
inside the lifespan handler, so the process dies during startup and
`restart: unless-stopped` turns that into a crash loop — it never reaches
`/health`.

So `null` is not a test accommodation that leaked into the image, which is what
the docstring at `backend/core/config.py:327-330` implies. It is the only value
this code path can currently produce. The real defect is one level up: `pooled`
was never implemented for an async engine — it needs `AsyncAdaptedQueuePool` —
which means the `Settings` default crashes any deployment that does not override
it, including a bare `uvicorn backend.main:app`. Tracked as
[#130](https://github.com/AlZrSe/sci-home-cluster/issues/130).

Check what the container actually runs:

```bash
docker compose exec backend printenv DB_POOL
```

### The Syncthing folder is bind-mounted

`SYNCTHING_ROOT` inside the container is the literal `/tmp/syncthing`, and the
host path is bind-mounted there. The default is `./syncthing` — a folder inside
this checkout, created by `docker/init_env.py`.

It has to be a real shared folder, because without the mount the failure is
silent: the backend creates `/tmp/syncthing/{jobs,nodes}` itself on startup, then
reports `syncthing_status: "available"` because the directory it just made
exists — empty, and invisible to every agent in the fleet. The cluster looks
healthy, jobs get submitted, and no node ever claims one (issue #104).

This used to be a **required** absolute path, and that was a poor answer: every
value is machine-specific, so it cannot be copied between hosts, shared, or
committed, and it is the single most likely thing to get wrong. A project-local
default resolves the same way everywhere, is visible in `ls`, and is created for
you. Point it at your real Syncthing folder by editing `SYNCTHING_ROOT` in `.env`
and re-running `python docker/init_env.py`.

`./syncthing` is gitignored and excluded from the build context, so the `jobs/`
and `nodes/` trees in it are never committed and never shipped in the image.

For the same reason, the **worker agent is not a service in
`docker-compose.yaml`**. It runs on each compute node, is file-only and holds no
credential, needs that node's GPU and that node's own Syncthing folder, and has no
documented deployment yet (issue #104). Run it there with
`run-agent --node-id <id>` — see `README.md` "Worker Agent (on each compute
node)". `run-agent` *is* on `PATH` inside this image, because `docker/Dockerfile`
copies `agent/` and the wheel ships it; that is a packaging artefact, not an
endorsement of `docker compose exec backend run-agent`.

### `VITE_API_URL`: the `localhost` default is only right locally

`VITE_API_URL` is a **build** argument, so the bundle is fixed at build time. The
default `http://localhost:8000/api/v1` is correct only for a browser **on the
machine running the container**. A browser on another machine will show
`BACKEND_UNREACHABLE` until you do one of:

- rebuild with an externally reachable value —
  `VITE_API_URL=http://cluster.lan:8000/api/v1 docker compose up -d --build`, or
  the same key in `.env`; or
- change it once on the app's settings page, which stores it in
  `localStorage["shc.settings"]` and overrides the build-time default.

Note the port is a constant in the compose file (`8000:8000`) and cannot be
derived from `VITE_API_URL`, because build arguments are interpolated before any
container exists. Changing one means editing both.

### Stopping, and what `down -v` costs

`docker compose down` removes the container and keeps the data. `docker compose
down -v` deletes **`shc-data` and `shc-state`**: the former is your cluster, and
the latter is the JWT signing key, so every token ever issued stops verifying and
every user is logged out.

One more host-side consequence worth knowing before you go looking for files:
the Dockerfile has no `USER`, so the container runs as root and anything it
writes into `shc-data` is root-owned on a Linux host.

### Single-container alternative: `docker run`

State lives in two directories that must survive a container replace — the
SQLite database and the JWT signing key. Mount a volume for them:

```bash
docker run -d --name shc \
  -p 8000:8000 \
  -v shc-data:/app/data \
  -v shc-state:/app/.shc \
  -e SHARED_TOKEN="$(openssl rand -hex 32)" \
  shc:latest
```

Without `-v`, the data is lost when the container is removed. Losing the signing
key specifically logs every user out, since previously issued tokens stop
verifying.

### Custom database location

The database path is controlled by the `SHC_DATA_DIR` environment variable
(default: `/app/data`). To store the database at a custom host path:

```bash
docker run -d --name shc \
  -p 8000:8000 \
  -v /host/path/to/data:/mnt/data \
  -v shc-state:/app/.shc \
  -e SHARED_TOKEN="$(openssl rand -hex 32)" \
  -e SHC_DATA_DIR=/mnt/data \
  shc:latest
```

The database file will be created at `<SHC_DATA_DIR>/scientific_home_cluster.db`.

### Configuration

Standard environment variables, all read by `backend/core/config.py`:

| Variable | Default in the image | Purpose |
| --- | --- | --- |
| `SHARED_TOKEN` | unset | Bearer token for API access. Set it. |
| `SERVE_FRONTEND` | `true` | Serve the SPA. Set `false` for an API-only container. |
| `FRONTEND_DIR` | `/app/frontend` | Where the built frontend was copied. |
| `DATABASE_URL` | derived from `SHC_DATA_DIR` | SQLite location. Set explicitly to override the default. |
| `SHC_DATA_DIR` | `/app/data` | Directory containing the SQLite database file. |
| `SHC_STATE_DIR` | `/app/.shc` | JWT signing key location. |
| `SYNCTHING_ROOT` | `/tmp/syncthing` | Mount the Syncthing folder here. |
| `DB_POOL` | `null` | `pooled` or `null`. The baked `null` is the *test suite's* setting; `docker compose` overrides it to `pooled`. See above. |
| `BACKEND_CORS_ORIGINS` | localhost dev origins | Not needed when same-origin; set it if split. |

`SECRET_KEY` is honoured but not set in the image: left unset, a key is generated
on first start and persisted under `SHC_STATE_DIR`. See the note on committed
signing keys in `AGENTS.md` — never bake one into the image.

Everything else the image bakes (`SERVE_FRONTEND`, `FRONTEND_DIR`,
`SHC_DATA_DIR`, `PYTHONPATH`) is inherited by `docker compose` rather than
re-stated there, so this image remains the single source of truth for them.

## How the frontend is served

This needed a decision that is not obvious from the file list, so it is worth
stating plainly.

The frontend is a **TanStack Start** app. Out of the box `npm run build` emits
client assets into `.output/public` and **no HTML at all** — every document is
rendered on request by a Nitro server. Copying that directory into an image and
mounting it as a static site serves the JS and CSS and then 404s on every route.

The build therefore runs in **SPA mode**, configured in `frontend/vite.config.ts`.
It prerenders exactly one document at build time and writes it into the same
directory as `_shell.html`. The backend serves that file for any path that is not
a real file and not an API route, and the router hydrates it client-side.

That works because this app has no server functions and no route loaders — data
arrives client-side over react-query from the API — so there is nothing that
needs a server at request time.

The Python image has no Node runtime and no Nitro server. If you later need true
SSR, that means shipping the Node runtime too; see the alternatives noted in the
commit that introduced this file.

### What the backend serves

`backend/core/frontend.py`, enabled by `SERVE_FRONTEND`:

- a file that exists under `FRONTEND_DIR` is served as-is;
- `/assets/*` additionally gets a one-year immutable cache header, since asset
  bundles are content-hashed;
- anything else gets `_shell.html`;
- `/api/v1/**`, `/docs` and `/redoc` are never answered with a page — an unknown
  API route stays a JSON 404.

## Notes

- The final image contains no Node, no npm, and no frontend source — only the
  prerendered document and the hashed bundles.
- `docker/Dockerfile` runs `npm run lockfile:check` during the build. This is not
  decoration: npm treats an existing lockfile as authoritative and does not
  re-resolve it, so a lockfile missing another platform's native binaries
  installs cleanly, exits 0, and then fails later with `Cannot find module
  @rollup/rollup-linux-x64-gnu`. See `frontend/scripts/check-lockfile-platforms.mjs`.
- Nothing is verified by these files alone on a machine without Docker or Node.
  The first real `docker build` is what confirms the SPA build emits `_shell.html`
  on this submodule revision.