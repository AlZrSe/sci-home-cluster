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
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
# edit .env: set SHARED_TOKEN and SYNCTHING_ROOT
docker compose up -d      # builds the image on first run
docker compose ps         # expect "(healthy)" within about 10 seconds
```

Then open <http://localhost:8000>. The API is at `/api/v1`, the docs at `/docs`,
and the SPA is served from `/` and every client-side route.

Two variables are **required**, and compose refuses to invent either:

| Variable | Why it is required |
| --- | --- |
| `SHARED_TOKEN` | The bearer token. Unset, the container would still boot and report itself healthy while `POST /api/v1/auth/token` answered `SHARED_TOKEN_NOT_CONFIGURED` to every login (issue #56). |
| `SYNCTHING_ROOT` | The **host** path of your Syncthing folder. Bind-mounted to `/tmp/syncthing` inside the container. |

Both are read from the repository-root `.env` automatically — compose interpolates
`${...}` from it without any `env_file:` key — or from your shell environment. If
either is unset **or empty**, `docker compose up` stops immediately with exit 1
and a message naming the variable, and nothing is created: no image built, no
volume made, no container started. That is deliberate; the file explains why at
each use site.

Two things compose does that the `docker run` line below does not:

- **`DB_POOL` is overridden to `pooled`.** The image bakes `DB_POOL=null`
  (`docker/Dockerfile:128`), which exists for the *test suite* — it opens a fresh
  connection per session so connections are never shared between event loops
  (`backend/core/config.py`, `backend/core/database.py`; the test session sets it
  for itself). This container runs exactly one `uvicorn` process and therefore one
  event loop, so there is nothing to protect against, and `NullPool` silently
  disables `pool_pre_ping` while opening and closing a connection per request.
  `pooled` is the application's own default. Overriding the image is not the
  ideal fix — removing `DB_POOL=null` from the Dockerfile is — but the Dockerfile
  is not this file's to change, so the override is the fix. If `docker inspect`
  shows `DB_POOL=null` in the image config, that is why the running container
  says `pooled`.
- **The volumes have pinned names.** `shc-data` and `shc-state` are declared with
  `name:`, so they are not prefixed with a project name derived from your checkout
  directory. That keeps `docker volume ls` a reliable way to find your state, and
  keeps them shared with the `docker run` form below.

Compose does not restart an unhealthy container, and `restart: unless-stopped`
restarts on exit rather than on ill health. Observe health with `docker compose ps`
and recover with `docker compose restart backend`.

### The Syncthing folder must be bind-mounted

`SYNCTHING_ROOT` inside the container is the literal `/tmp/syncthing`, and the
path you set in `.env` is bind-mounted there. Without the mount the failure is
silent: the backend creates `/tmp/syncthing/{jobs,nodes}` itself on startup, then
reports `syncthing_status: "available"` because the directory it just made
exists — empty, and invisible to every agent in the fleet. The cluster looks
healthy, jobs get submitted, and no node ever claims one. Hence the required
variable rather than a default.

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