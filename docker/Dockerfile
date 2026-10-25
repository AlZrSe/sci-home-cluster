# syntax=docker/dockerfile:1
#
# Two-stage build: Node produces the static frontend, Python carries the backend
# plus those files. The final image has no Node, no npm and no frontend source in
# it - only the prerendered SPA document and its hashed asset bundles, which the
# backend serves on the same origin as the API.
#
# Build from the repository root:
#
#     docker build -f docker/Dockerfile -t shc:latest .
#
# IMPORTANT - the frontend is a git submodule, not vendored here (see AGENTS.md).
# A plain `git clone` leaves frontend/ empty and this build fails on purpose,
# one line into the frontend stage, rather than shipping an image whose UI 404s.
# Clone recursively:
#
#     git clone --recurse-submodules <url> && cd scientific-home-cluster
#
# or, in an existing checkout:
#
#     git submodule update --init --recursive
#
# Run:
#
#     docker run -p 8000:8000 -e SHARED_TOKEN=<token> shc:latest
#
# The container's writable state - the SQLite database and the JWT signing key -
# must be on a volume or it is lost on every replace. See docker/README.md.

# ---------------------------------------------------------------------------
# Stage 1 - frontend
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend

# NODE_ENV is deliberately not set to "production": `npm ci` would then skip
# devDependencies, and the build needs vite, the nitro plugin and the whole
# @lovable.dev toolchain, all of which are devDependencies. The discard is
# deliberate too - none of this reaches the final image.
WORKDIR /build/frontend

# The lockfile is copied on its own so this layer is reused whenever only source
# files change. `npm ci`, never `npm install`: ci resolves strictly from the
# lockfile and fails if package.json and it disagree.
COPY frontend/package.json frontend/package-lock.json ./

COPY frontend/ ./

# `lockfile:check` runs after the full source is present, so the script exists.
RUN npm ci && npm run lockfile:check

# .env.production is gitignored, so it is absent from this context by design and
# the build falls back to localhost (frontend/src/lib/settings.ts). Two variables
# are therefore passed explicitly:
#
#   VITE_CLUSTER_BACKEND  baked into the bundle, "mock" is the only value that
#                         selects the in-memory demo data. Left at "http" the
#                         build always talks to the real backend.
#   VITE_API_URL          default API base URL, overridable at runtime from the
#                         settings page.
#
# VITE_API_URL must be ABSOLUTE, and this is a real constraint rather than a
# style preference: frontend/src/services/http.ts derives its WebSocket URL with
# `base().replace(/^http/, "ws")`, and `new WebSocket("/api/v1/...")` throws in a
# browser, so a same-origin relative base would break the job log stream.
ARG VITE_CLUSTER_BACKEND=http
ARG VITE_API_URL=http://localhost:8000/api/v1
ENV VITE_CLUSTER_BACKEND=${VITE_CLUSTER_BACKEND} \
    VITE_API_URL=${VITE_API_URL}

RUN npm run build \
    # Grouped, so a failed *build* is not reported as a missing shell file:
    # `a && b || c` parses as `(a && b) || c` and would run c after a build
    # failure too, printing the wrong diagnosis for the same exit code.
    && { test -f .output/public/_shell.html \
        || { echo "ERROR: .output/public/_shell.html was not produced." \
             && echo "The frontend must build in SPA mode - check the 'spa'" \
             && echo "block in frontend/vite.config.ts, and that the change" \
             && echo "was committed upstream in the frontend submodule." \
             && exit 1; }; }

# ---------------------------------------------------------------------------
# Stage 2 - backend
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# The psycopg/asyncpg stack is not needed: this project's store is SQLite via
# aiosqlite (pyproject.toml). Build tools are not needed either - every
# dependency here has a wheel for this platform - so the image carries no
# compiler.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Sources are copied before installing, unlike a layer-caching-first ordering.
# setuptools resolves `packages.find` at build time, so `pip install .` against a
# directory holding only pyproject.toml would install the dependencies and none
# of the four packages - and a re-run after COPY would silently no-op. Correct
# packaging beats saving a layer here.
COPY pyproject.toml ./
COPY backend/ ./backend/
COPY shared/ ./shared/
COPY agent/ ./agent/
COPY cli/ ./cli/

# Not `-e`: an editable install would point at /app, and this image ships a
# finished artifact rather than a checkout someone intends to edit.
RUN pip install --no-cache-dir .

# The built frontend, and only the built frontend. `COPY --from=frontend` reads
# the stage's filesystem directly, so node_modules, the source tree and the
# nitro server bundle all stay behind.
COPY --from=frontend /build/frontend/.output/public/ /app/frontend/

# Create non-root user and group (UID/GID 1000 - standard first non-root user)
RUN groupadd -g 1000 shc \
 && useradd -u 1000 -g 1000 -m -s /bin/bash -d /home/shc shc

# Install gosu for privilege dropping (used by official images like PostgreSQL, Redis)
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/*

# Create and chown runtime directories BEFORE switching user
# These will be overlaid by named volumes at runtime, but pre-creating with
# correct ownership handles the image's own filesystem and documents the expected UID/GID.
RUN mkdir -p /app/data /app/.shc /tmp/syncthing \
 && chown -R shc:shc /app/data /app/.shc /tmp/syncthing \
 && chmod 750 /app/data /tmp/syncthing \
 && chmod 700 /app/.shc

# These paths are derived from the package location
# (backend/core/config.py::_REPO_ROOT), so they land under /app regardless of
# the working directory. Both are written at runtime and must survive a
# container replace: the database holds all cluster state, and the signing key
# means every restart otherwise logs every user out.
VOLUME ["/app/data", "/app/.shc"]

# Matches the frontend and backend sharing one origin, which is the point of
# this image: no CORS, no second port, one thing to deploy and to reverse-proxy.
ENV SERVE_FRONTEND=true \
    FRONTEND_DIR=/app/frontend \
    DB_POOL=null \
    SHC_DATA_DIR=/app/data \
    PYTHONPATH=/app

# Syncthing static build (issue #154). No apt repository: the binary is
# extracted from the official release tarball. curl + ca-certificates are the
# only apt packages added, for fetching it.
ARG SYNCTHING_VERSION=1.27.12
ARG TARGETARCH=amd64

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && case "$TARGETARCH" in \
        amd64|arm64|arm|386) ARCH="$TARGETARCH" ;; \
        *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL \
         "https://github.com/syncthing/syncthing/releases/download/v${SYNCTHING_VERSION}/syncthing-linux-${ARCH}-v${SYNCTHING_VERSION}.tar.gz" \
         -o /tmp/syncthing.tar.gz \
 && tar -xzf /tmp/syncthing.tar.gz -C /tmp \
 && install -m 0755 "/tmp/syncthing-linux-${ARCH}-v${SYNCTHING_VERSION}/syncthing" /usr/local/bin/syncthing \
 && rm -rf /tmp/syncthing* \
 && syncthing --version

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/entrypoint-root.sh /usr/local/bin/entrypoint-root.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/entrypoint-root.sh

EXPOSE 8000

# Unauthenticated, so it must not depend on the token. The Syncthing watcher and
# the database are both up once the lifespan handler has finished.
# PORT is injected by Render (and may be set locally); fall back to 8000 so
# the existing docker run / compose paths keep working unchanged.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health'%p, timeout=4).status == 200 else 1)"

# Root entrypoint wrapper fixes volume ownership then drops to shc user
USER root
ENTRYPOINT ["/usr/local/bin/entrypoint-root.sh"]