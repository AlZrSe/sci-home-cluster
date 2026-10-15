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

Then open <http://localhost:8000>. The API is at `/api/v1`, the docs at `/docs`,
and the SPA is served from `/` and every client-side route.

Without `-v`, the data is lost when the container is removed. Losing the signing
key specifically logs every user out, since previously issued tokens stop
verifying.

### Configuration

Standard environment variables, all read by `backend/core/config.py`:

| Variable | Default in the image | Purpose |
| --- | --- | --- |
| `SHARED_TOKEN` | unset | Bearer token for API access. Set it. |
| `SERVE_FRONTEND` | `true` | Serve the SPA. Set `false` for an API-only container. |
| `FRONTEND_DIR` | `/app/frontend` | Where the built frontend was copied. |
| `DATABASE_URL` | `/app/data/scientific_home_cluster.db` | SQLite location. |
| `SHC_STATE_DIR` | `/app/.shc` | JWT signing key location. |
| `SYNCTHING_ROOT` | `/tmp/syncthing` | Mount the Syncthing folder here. |
| `BACKEND_CORS_ORIGINS` | localhost dev origins | Not needed when same-origin; set it if split. |

`SECRET_KEY` is honoured but not set in the image: left unset, a key is generated
on first start and persisted under `SHC_STATE_DIR`. See the note on committed
signing keys in `AGENTS.md` — never bake one into the image.

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