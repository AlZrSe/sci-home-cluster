"""
MUST M-2.1 .. M-2.4 - what the image actually contains (layer L2).

These scenarios read a **never-started** container. That is deliberate and it is
why the paths below are the four that are NOT declared `VOLUME`s: in a plain
`docker run` those two are fresh anonymous volumes shadowing whatever the image
carries there, while every other path reports the image's real bytes. Issue #131
leaked through `COPY backend/` (`docker/Dockerfile:103`), which is not shadowed -
so walking the non-VOLUME paths measures the image, and MUST M-2.2 covers the two
shadowed paths separately by checking them for absence rather than content.

Every walk here is proven non-vacuous by asserting it saw at least one
known-present file. A `find` that silently matched nothing, because of a typo or
because the path does not exist, would otherwise pass for the same reason a
clean image does.
"""

from backend.tests.compose.conftest import (
    COMPOSE_PYTESTMARK,
    IMAGE_TAG,
    assert_not_in,
    image_config,
    run_in_image,
)

pytestmark = COMPOSE_PYTESTMARK

#: The source trees the Dockerfile COPYs. `/app/data` and `/app/.shc` are
#: deliberately absent: they are the image's declared VOLUMEs
#: (`docker/Dockerfile:122`), so in a `docker run` they are fresh empty anonymous
#: volumes and a walk of them measures the run, not the image.
SOURCE_TREES = (
    "/app/backend",
    "/app/shared",
    "/app/agent",
    "/app/cli",
    "/app/frontend",
)

#: A file that MUST be there, used as the non-vacuity self-check.
KNOWN_FILE = "/app/backend/main.py"

#: The default `VITE_API_URL`, `docker-compose.yaml:110` and
#: `docker/Dockerfile:66`.
DEFAULT_API_URL = "http://localhost:8000/api/v1"


# ---------------------------------------------------------------------------
# M-2.1  No SQLite state shipped in the image - the #131 regression (AC-7)
# ---------------------------------------------------------------------------


def test_image_ships_no_sqlite_state(image_id: str) -> None:
    """
    M-2.1 / AC-7 - no `*.db`, `*-shm`, `*-wal`, `*.sqlite` or `*.sqlite3` file
    anywhere under the four non-`VOLUME` source trees, AND the walk is proven
    non-vacuous by finding `/app/backend/main.py`.

    Protects `.dockerignore:79-106` - specifically the four files it names at
    `:88-92` (`backend/scientific_home_cluster.db`, its `-shm` and `-wal`
    companions, and `backend/tests/unit/scientific_home_cluster.db`), all copied
    in by `COPY backend/ ./backend/` (`docker/Dockerfile:103`) because the
    patterns carried no `**/` prefix and so matched only the repository root.

    The `-shm` and `-wal` companions are the give-away that this was live
    cluster state and not a fixture: those files exist only beside a database
    that was OPEN at build time. Anyone who could `docker pull` could read it,
    which is the same exposure issue #56 recorded for signing keys.

    SHOULD C-1 (plant a database, rebuild, watch this go red) is the belt-and-
    braces version of this test and costs two full image builds; the known-file
    self-check below is the cheap substitute, and it is what stops a
    mis-written walk from passing for the wrong reason.
    """
    script = (
        "for p in " + " ".join(SOURCE_TREES) + "; do "
        '  if [ ! -d "$p" ]; then echo "MISSING_TREE $p"; fi; '
        "done; "
        "echo '--- OFFENDERS ---'; "
        "find " + " ".join(SOURCE_TREES) + " -type f "
        "\\( -name '*.db' -o -name '*-shm' -o -name '*-wal' "
        "-o -name '*.sqlite' -o -name '*.sqlite3' \\) "
        "| sort; "
        "echo '--- END ---'; "
        f"if [ -f {KNOWN_FILE} ]; then echo KNOWN_FILE_PRESENT; "
        f"else echo KNOWN_FILE_ABSENT; fi"
    )
    output = run_in_image(IMAGE_TAG, script)

    assert "MISSING_TREE" not in output, (
        f"a source tree is missing from the image, so the walk below was "
        f"vacuous:\n{output}"
    )
    offenders = _between(output, "--- OFFENDERS ---", "--- END ---")
    assert offenders == [], (
        "the image ships SQLite state. These files are operator data, not "
        "source; anyone who can `docker pull shc:latest` can read them "
        "(issue #131, .dockerignore:79-106):\n" + "\n".join(offenders)
    )
    assert "KNOWN_FILE_PRESENT" in output, (
        f"{KNOWN_FILE} is absent, so the walk proved nothing - it would pass "
        f"identically on an image with no /app/backend at all:\n{output}"
    )


# ---------------------------------------------------------------------------
# M-2.2  No `.env`, no signing key, empty VOLUME dirs (AC-8)
# ---------------------------------------------------------------------------


def test_image_ships_no_env_file_and_no_signing_key(image_id: str) -> None:
    """
    M-2.2 / AC-8 - no `/app/.env*`, no `/app/.shc/secret_key`, and `/app/data`
    and `/app/.shc` are empty before the process starts.

    Protects `.dockerignore:66-77` (`.env`, `.env.*`, `!.env.example`,
    `frontend/.env`, `frontend/.env.*`, `!frontend/.env.example` - the real lines
    are 72-77, not the stale `:51-52` that `docker-compose.yaml:148-149` and
    `:155` cite) and `:99-106`; `docker-compose.yaml:148-149` ("pydantic's
    _REPO_ROOT is `/app` inside the image, so it looks for `/app/.env`, which
    `.dockerignore` excludes from the build context and the Dockerfile never
    COPYs"); and the committed-signing-key exposure in `AGENTS.md` / issue #56.

    The two directory checks are the VOLUME ones this walk cannot do: in a plain
    `docker run` they are fresh anonymous volumes, so "empty" here is a fact
    about what the IMAGE put there, checked on the pre-start filesystem.
    """
    script = (
        "echo '--- ENVFILES ---'; "
        "find /app -maxdepth 1 -name '.env*' | sort; "
        "echo '--- ENVFILES END ---'; "
        "echo '--- DATA ---'; ls -A /app/data; echo '--- DATA END ---'; "
        "echo '--- STATE ---'; ls -A /app/.shc; echo '--- STATE END ---'"
    )
    output = run_in_image(IMAGE_TAG, script)

    envfiles = _between(output, "--- ENVFILES ---", "--- ENVFILES END ---")
    assert envfiles == [], (
        "the image contains a .env file. backend/core/config.py loads "
        "env_file=<repo root>/.env, so baking one in publishes the operator's "
        "SHARED_TOKEN and SECRET_KEY to anyone who pulls the image "
        "(.dockerignore:72-77):\n" + "\n".join(envfiles)
    )

    secret = run_in_image(IMAGE_TAG, "ls -A /app/.shc/secret_key 2>/dev/null || true")
    assert secret.strip() == "", (
        "the image ships a JWT signing key. A committed signing key has to be "
        "rotated on sight, and two were committed to this public repository "
        "(issue #56)."
    )

    data = _between(output, "--- DATA ---", "--- DATA END ---")
    assert data == [], f"/app/data is not empty in the image: {data}"
    state = _between(output, "--- STATE ---", "--- STATE END ---")
    assert state == [], f"/app/.shc is not empty in the image: {state}"


# ---------------------------------------------------------------------------
# M-2.3  No Node, no npm, no frontend source; the shell document is present (AC-9)
# ---------------------------------------------------------------------------


def test_image_has_no_node_and_no_frontend_source(image_id: str) -> None:
    """
    M-2.3 / AC-9 - `node` and `npm` do not resolve on `PATH`, there is no
    `node_modules` and no `package.json` anywhere under `/app`, and
    `/app/frontend/_shell.html` exists and is non-empty.

    Protects `docker/Dockerfile:3-6` (the two-stage design), `:112-115`
    (`COPY --from=frontend /build/frontend/.output/public/ /app/frontend/` -
    "node_modules, the source tree and the nitro server bundle all stay
    behind"), `:74-79` (the grouped build/shell check), and
    `docker/README.md:310-311`.

    The `_shell.html` assertion is the load-bearing half and it is genuinely
    non-vacuous: this repository's own `frontend/.output/public` has NO
    `_shell.html` (only `assets/`), because `npm run build` emits no HTML until
    SPA mode prerenders it. `.dockerignore:58` excludes `frontend/.output/` from
    the build context, so the local build cannot influence what is asserted here
    - this is testing the container's build.
    """
    script = (
        "echo '--- NODE ---'; "
        "command -v node || echo NODE_ABSENT; "
        "command -v npm || echo NPM_ABSENT; "
        "echo '--- NODE END ---'; "
        "echo '--- JS ---'; "
        "find /app -name node_modules -o -name package.json | sort; "
        "echo '--- JS END ---'; "
        "if [ -f /app/frontend/_shell.html ]; then "
        "  echo SHELL_BYTES=$(wc -c < /app/frontend/_shell.html); "
        "else echo SHELL_BYTES=0; fi"
    )
    output = run_in_image(IMAGE_TAG, script)

    node_block = _between(output, "--- NODE ---", "--- NODE END ---")
    assert node_block == ["NODE_ABSENT", "NPM_ABSENT"], (
        f"node/npm resolve inside the image: {node_block}. The final stage is "
        "python:3.11-slim (docker/Dockerfile:84) and only the BUILT frontend is "
        "copied across (docker/Dockerfile:112-115)."
    )

    js_block = _between(output, "--- JS ---", "--- JS END ---")
    assert js_block == [], (
        "frontend source or dependencies shipped into the final image: "
        f"{js_block}. COPY --from=frontend reads the build stage's filesystem "
        "directly, so only `.output/public` should have crossed."
    )

    assert "SHELL_BYTES=0" not in output, (
        "/app/frontend/_shell.html is absent or empty, so every route in the "
        "container would 200 the API's JSON root or nothing at all "
        "(docker/Dockerfile:74-79, backend/core/frontend.py:114-120)"
    )
    size = int(_marker_value(output, "SHELL_BYTES"))
    assert size > 0, (
        f"/app/frontend/_shell.html is {size} bytes; the SPA document is not "
        "empty (docker/Dockerfile:74-79)"
    )


# ---------------------------------------------------------------------------
# M-2.4  The bundle carries an ABSOLUTE VITE_API_URL (AC-10)
# ---------------------------------------------------------------------------


def test_bundle_carries_an_absolute_api_url(image_id: str) -> None:
    """
    M-2.4 / AC-10 - the concatenated text of every `.js` file under
    `/app/frontend` contains the absolute `http://localhost:8000/api/v1`.

    Protects `docker-compose.yaml:95-98`, `docker/Dockerfile:61-64` and
    `docker/README.md:52-56`: "`frontend/src/services/http.ts` derives the socket
    URL with `base().replace(/^http/, "ws")`, so a same-origin relative base
    produces `new WebSocket("/api/v1/...")`, which throws in a browser and kills
    the job log stream."

    **Honest limitation, stated because it is a real one.** With the default
    build this cannot distinguish "the compose build-arg reached the bundle" from
    "the developer's local `frontend/.env.production` happened to hold the same
    value". The literal is inlined into `settings-*.js`, `login-*.js` and
    `services-*.js` because `frontend/src/lib/settings.ts:20` reads it as a
    static `import.meta.env["VITE_API_URL"]` key, so the search is reliable -
    but SHOULD S-2 is the test that closes the gap, and it says so too.

    The negative half is asserted as well: a *relative* `/api/v1` must NOT appear
    as a standalone base, because that is the exact value that breaks the socket.
    """
    script = "find /app/frontend -type f -name '*.js' -exec cat {} +"
    bundle = run_in_image(IMAGE_TAG, script)

    assert bundle.strip(), (
        "no .js files under /app/frontend, so the search proved nothing. The "
        "build must have produced hashed asset bundles "
        "(docker/Dockerfile:112-115)."
    )
    assert DEFAULT_API_URL in bundle, (
        f"the bundle does not contain {DEFAULT_API_URL}. VITE_API_URL is baked "
        "at BUILD time (docker/Dockerfile:61-68), so a relative or missing base "
        "here breaks the job-log WebSocket in the browser "
        '(docker/Dockerfile:61-64). Look for `"base":"/api/v1"` or similar '
        "in the bundle."
    )
    assert_not_in(
        '"/api/v1"',
        bundle,
        'the bundle contains a bare "/api/v1" base. A relative base produces '
        'new WebSocket("/api/v1/..."), which throws in a browser and kills the '
        "job log stream (docker-compose.yaml:95-98)",
    )


# ---------------------------------------------------------------------------
# SHOULD S-2  The compose build-arg really reaches the bundle
# ---------------------------------------------------------------------------


def test_sentinel_build_arg_reaches_the_bundle(sentinel_image_id: str) -> None:
    """
    SHOULD S-2 - rebuild with `VITE_API_URL=https://compose-e2e.invalid/api/v1`
    and assert the bundle carries the sentinel and NOT the localhost default.

    This is what MUST M-2.4 cannot be. A literal match on the default proves
    the bundle has *a* value; it does not prove compose supplied it. Here the
    sentinel is a value nothing else could have produced:
    `.dockerignore:72-77` excludes `frontend/.env*` from the build context, so a
    developer's local dotfile cannot leak the localhost default back in, and the
    negative assertion catches a build that ignored the ARG entirely.

    Skipped unless `SHC_COMPOSE_SENTINEL_BUILD=1`, because it costs a full vite
    build. The spec's guidance was to drop it first if the suite is too slow;
    M-2.4 still catches the relative-URL regression without it.
    """
    bundle = run_in_image(
        sentinel_image_id,
        "find /app/frontend -type f -name '*.js' -exec cat {} +",
    )
    sentinel = "https://compose-e2e.invalid/api/v1"
    assert sentinel in bundle, (
        f"the sentinel {sentinel!r} is absent from the bundle built with "
        f"--build-arg VITE_API_URL={sentinel}. Either the ARG does not reach the "
        "vite build (docker/Dockerfile:61-68) or something else supplies the "
        "base."
    )
    assert DEFAULT_API_URL not in bundle, (
        f"the sentinel build still contains {DEFAULT_API_URL}. That value came "
        "from somewhere other than the build ARG - which is exactly the "
        "ambiguity MUST M-2.4 cannot resolve."
    )


# ---------------------------------------------------------------------------
# SHOULD S-3  The image bakes the documented env, healthcheck, volumes, ports
# ---------------------------------------------------------------------------


def test_image_bakes_the_documented_environment(image_id: str) -> None:
    """
    SHOULD S-3 - `docker image inspect` reports `SERVE_FRONTEND=true`,
    `FRONTEND_DIR=/app/frontend`, `SHC_DATA_DIR=/app/data`, `PYTHONPATH=/app`,
    `DB_POOL=null`; the healthcheck's `Test` references `/api/v1/health` with a
    `--start-period=10s`; `Volumes` is exactly `{"/app/data", "/app/.shc"}`; and
    `ExposedPorts` is exactly `{"8000/tcp"}`.

    Protects `docker/Dockerfile:122`, `:126-130`, `:132`, `:136-137`, and
    `docker-compose.yaml:122-134` - where those five values are deliberately NOT
    re-stated by compose ("re-stating them here would make this file a second
    source of truth for values the image owns, and the two would drift
    silently").

    The healthcheck must not depend on the token: it is unauthenticated, so a
    change that made it token-aware would report the container unhealthy for
    exactly the reason an operator cannot debug it from `docker compose ps`.
    """
    config = image_config(IMAGE_TAG)

    environment = dict(entry.split("=", 1) for entry in config["Env"] if "=" in entry)
    expected = {
        "SERVE_FRONTEND": "true",
        "FRONTEND_DIR": "/app/frontend",
        "SHC_DATA_DIR": "/app/data",
        "PYTHONPATH": "/app",
        "DB_POOL": "null",
    }
    for key, value in expected.items():
        assert environment.get(key) == value, (
            f"the image bakes {key}={environment.get(key)!r}, expected {value!r} "
            "(docker/Dockerfile:126-130). docker-compose.yaml inherits these on "
            "purpose (docker-compose.yaml:122-134), so a change here silently "
            "changes the deployment."
        )

    healthcheck = config.get("Healthcheck") or {}
    test_command = " ".join(healthcheck.get("Test") or [])
    assert "/api/v1/health" in test_command, (
        f"the image healthcheck does not probe /api/v1/health: {test_command!r} "
        "(docker/Dockerfile:136-137). MUST M-3.1 asserts the container actually "
        "reaches `healthy`, and this is what it depends on."
    )
    assert healthcheck.get("StartPeriod") == 10_000_000_000, (
        f"healthcheck start period is {healthcheck.get('StartPeriod')!r}ns; "
        "docker/Dockerfile:136 sets --start-period=10s. D3 depends on it: "
        "health legitimately arrives tens of seconds after start."
    )

    assert set(config.get("Volumes") or {}) == {"/app/data", "/app/.shc"}, (
        f"the image declares VOLUME {sorted(config.get('Volumes') or {})}; "
        "docker/Dockerfile:122 declares exactly /app/data and /app/.shc. A "
        "missing one means a container replace destroys that state."
    )
    assert set(config.get("ExposedPorts") or {}) == {"8000/tcp"}, (
        f"the image exposes {sorted(config.get('ExposedPorts') or {})}; "
        "docker/Dockerfile:132 exposes 8000 and nothing else."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _between(output: str, start: str, end: str) -> list[str]:
    """The non-blank lines strictly between two marker lines."""
    assert start in output, f"marker {start!r} absent from:\n{output}"
    body = output.split(start, 1)[1]
    assert end in body, f"marker {end!r} absent from:\n{output}"
    return [line.strip() for line in body.split(end, 1)[0].splitlines() if line.strip()]


def _marker_value(output: str, key: str) -> str:
    for line in output.splitlines():
        if line.strip().startswith(f"{key}="):
            return line.strip().split("=", 1)[1]
    raise AssertionError(f"{key}=... absent from:\n{output}")
