"""
MUST M-3.1 .. M-3.10 - the running container over its PUBLISHED port (layer L3).

Every request goes to `127.0.0.1:<free-port>`, which is a path the image's own
healthcheck does NOT cover: it uses `127.0.0.1:8000` *inside* the container, so
nothing it checks can tell you the port mapping works.

Every test that asserts something about authentication carries the AC-15 control
assertion alongside it. That control is not optional politeness.
`backend/tests/integration/test_signing_key_rotation.py:12-19` documents the trap
in its own words: "Starlette's TestClient sends `Host: testserver`, which *is* in
the localhost bypass list, so `get_current_token_payload` short-circuits... A test
written as 'expect 401' would pass for a reason that has nothing to do with this
fix." `127.0.0.1` is in the bypass list too, so a "expect 401" test issued
against the published port without overriding `Host` would pass whether or not
authentication worked at all.
"""

from backend.tests.compose.conftest import (
    COMPOSE_PYTESTMARK,
    HEALTH_TIMEOUT_S,
    JWT_SEGMENTS,
    NON_LOCALHOST_HOST_HEADER,
    ClusterClient,
    ComposeStack,
    _mint_jwt,
    assert_not_in,
)

pytestmark = COMPOSE_PYTESTMARK

#: backend/main.py:293-297, registered only when the SPA is NOT being served.
JSON_ROOT_BODY = {"message": "Welcome to Scientific Home Cluster API"}

#: The `cluster` fixture (over the published port, carrying a minted JWT) lives
#: in the harness conftest rather than here, because MUST M-4.3 in
#: `test_live_volumes.py` needs the same client. It is named `cluster` and not
#: `client` because `backend/tests/conftest.py` already owns `client`.


# ---------------------------------------------------------------------------
# M-3.1  The container reaches (healthy) (AC-11)
# ---------------------------------------------------------------------------


def test_container_reaches_healthy(compose_stack: ComposeStack) -> None:
    """
    M-3.1 / AC-11 - `.State.Health.Status` becomes `healthy` within 300 s of `up`
    returning.

    Protects the image's OWN HEALTHCHECK (`docker/Dockerfile:136-137`) and
    `docker-compose.yaml:12`, `:276-290` ("Observe health there"). Compose does
    not restart an unhealthy container, so this status is the operator's only
    signal - and `docker compose ps` is where they are told to look.

    **Control assertion.** `RestartCount == 0` and the published-port health
    endpoint answers 200 *before* the poll. Without them this test could pass for
    the wrong reason - a container that started, answered once, was then killed
    and came back would report `healthy` with a non-zero restart count, and a
    test that only watched the status string would call that a pass.

    `RestartCount` is read through `ComposeStack.restart_count()`, which
    templates `{{.RestartCount}}` in its own right. Reading it out of
    `{{json .State}}` - as this test originally did - yields 0 unconditionally,
    because the field is a SIBLING of `State`, so the control passed whatever the
    container had done.
    """
    cid = compose_stack.container_id()
    state = compose_stack.inspect("{{json .State}}", cid)
    assert state.get("Health"), (
        "the container has no health status at all. The image declares a "
        "HEALTHCHECK (docker/Dockerfile:136-137) and compose inherits it; a "
        "missing status means the image and this suite disagree."
    )
    restarts = compose_stack.restart_count(cid)
    assert restarts == 0, (
        f"RestartCount is {restarts} before the health poll. The container "
        "restarted, so a later `healthy` could be a restart loop settling rather "
        "than a healthy start. MUST M-5.3 is the scenario that deliberately "
        "causes a restart."
    )

    with ClusterClient(compose_stack.port) as instance:
        probe = instance.get("/api/v1/health")
    assert probe.status_code == 200, (
        f"the published port does not serve /api/v1/health ({probe.status_code}). "
        "The image healthcheck uses 127.0.0.1 INSIDE the container, so it cannot "
        "catch a broken port mapping - this probe is what does."
    )

    assert compose_stack.wait_healthy(timeout=HEALTH_TIMEOUT_S, cid=cid) == "healthy"


# ---------------------------------------------------------------------------
# M-3.2  /api/v1/health over the published port (AC-12)
# ---------------------------------------------------------------------------


def test_health_endpoint_over_the_published_port(cluster: ClusterClient) -> None:
    """
    M-3.2 / AC-12 - 200 with `status == "healthy"`, `database.status ==
    "healthy"`, `syncthing.path == "/tmp/syncthing"`, `syncthing.status ==
    "available"`, and `database.url` ending in
    `/app/data/scientific_home_cluster.db`.

    Protects `backend/main.py:300-368` and `backend/core/config.py:562-567`. Over
    the PUBLISHED port, through the port mapping - a path the image-internal
    healthcheck (which uses `127.0.0.1`) does not cover.

    **`syncthing.status == "available"` is a WEAK assertion and must never be
    cited as evidence the bind mount works.** It is satisfied by a directory the
    backend created for itself at
    `backend/services/syncthing_service.py:192-193` - which is exactly issue
    #104's silent second cluster: healthy cluster, submitted job, no node ever
    claims it. MUST M-4.2 is the scenario that distinguishes them. The two are
    never interchangeable.
    """
    response = cluster.get("/api/v1/health")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "healthy", body
    assert body["database"]["status"] == "healthy", body
    assert body["syncthing"]["path"] == "/tmp/syncthing", (
        f"{body['syncthing']['path']!r} - the CONTAINER path is a literal in "
        "docker-compose.yaml:175-181, not the host value the operator typed"
    )
    assert body["syncthing"]["status"] == "available", body
    assert body["database"]["url"].endswith("/app/data/scientific_home_cluster.db"), (
        f"database.url is {body['database']['url']!r}. The database has to live on "
        "the shc-data volume mounted at /app/data (docker-compose.yaml:231), or "
        "a container replace destroys the cluster."
    )


# ---------------------------------------------------------------------------
# M-3.3 / M-3.4 / M-3.5 / M-3.6  One origin (AC-13)
# ---------------------------------------------------------------------------


def test_root_serves_the_spa_shell(
    cluster: ClusterClient, compose_stack: ComposeStack
) -> None:
    """
    M-3.3 / AC-13 - `GET /` is 200 `text/html`, byte-identical to
    `/app/frontend/_shell.html` read via `docker compose exec cat`, and NOT the
    JSON root.

    Protects `backend/main.py:293-297` ("in a container the page at `/` is the
    app, not this JSON") and `backend/core/frontend.py:141-151`.

    The comparison is against the BYTES INSIDE THE CONTAINER, not against a local
    `frontend/.output/public`. The developer's local build is a different build
    - measured, it has no `_shell.html` at all - and `.dockerignore:58` excludes
    it from the build context, so a local comparison would be testing the wrong
    artifact.
    """
    expected = compose_stack.exec("cat", "/app/frontend/_shell.html").stdout
    assert expected.strip(), "/app/frontend/_shell.html is empty in the container"

    response = cluster.get("/")
    assert response.status_code == 200, response.text
    assert "text/html" in response.headers.get(
        "content-type", ""
    ), f"content-type is {response.headers.get('content-type')!r}"
    assert response.content == expected.encode("utf-8"), (
        "GET / did not return the bytes of /app/frontend/_shell.html. The single-"
        "origin design means the API and the SPA are one process "
        "(backend/core/frontend.py)."
    )
    try:
        body = response.json()
    except ValueError:
        body = None
    assert body != JSON_ROOT_BODY, (
        "GET / returned the API's JSON root. That handler is registered only "
        "when the SPA is NOT being served (backend/main.py:289-297), so "
        "reaching it means SERVE_FRONTEND is off in the image."
    )


def test_client_route_serves_the_same_shell(
    cluster: ClusterClient, compose_stack: ComposeStack
) -> None:
    """
    M-3.4 / AC-13 - `GET /jobs/job-1` is 200 and byte-identical to `GET /`.

    Protects `backend/core/frontend.py:20-33` ("Everything else - the shell
    document. The router hydrates it and renders the real route client-side,
    which is what makes `/jobs/<id>` work despite there being no such file to
    serve"). There is no `/app/frontend/jobs/` directory, so anything other than
    the shell means the fallback broke.
    """
    root = cluster.get("/")
    route = cluster.get("/jobs/job-1")
    assert route.status_code == 200, route.text
    assert route.content == root.content, (
        "a client route returned different bytes than `/`. "
        "backend/core/frontend.py:141-151 serves one prerendered document for "
        "every non-file, non-API path."
    )


def test_unknown_api_route_is_a_json_404(cluster: ClusterClient) -> None:
    """
    M-3.5 / AC-13 - `GET /api/v1/no-such-route` is 404 with a JSON body
    containing no HTML.

    Protects `backend/core/frontend.py:24-28` and `:134-139` ("an unknown API
    route stays a JSON 404 rather than becoming a page of HTML, which would
    otherwise turn a typo'd endpoint into a confusing 200 with the app's markup
    in it") and `docker/README.md:305-306`.

    The non-vacuity half is the negative on `<!doctype html`: a 404 status alone
    would also be satisfied by a proxy error page.
    """
    response = cluster.get("/api/v1/no-such-route")
    assert response.status_code == 404, response.text
    assert "application/json" in response.headers.get("content-type", ""), (
        f"content-type is {response.headers.get('content-type')!r}; an unknown "
        "API route must stay JSON (backend/core/frontend.py:134-139)"
    )
    assert_not_in(
        "<!doctype html",
        response.text.lower(),
        "the unknown API route returned an HTML body. That is the SPA fallback "
        "leaking into the API namespace (backend/core/frontend.py:137-139).",
    )


def test_docs_is_served(cluster: ClusterClient) -> None:
    """
    M-3.6 / AC-13 - `GET /docs` is 200 `text/html` and references swagger-ui.

    Protects `backend/core/frontend.py:66` (`/docs` is in `_reserved_prefixes`)
    and `docker/README.md:73-74`. If `/docs` were not reserved, the catch-all
    would answer it with the SPA shell and the API would look like it had no
    documentation.
    """
    response = cluster.get("/docs")
    assert response.status_code == 200, response.text
    assert "text/html" in response.headers.get("content-type", ""), response.headers
    assert "swagger-ui" in response.text.lower(), (
        "GET /docs did not reference swagger-ui; the SPA shell answered instead "
        "(backend/core/frontend.py:66 reserves /docs from the catch-all)"
    )


# ---------------------------------------------------------------------------
# M-3.7 / M-3.8  Token generation, persistence, and the promise not to print it
# ---------------------------------------------------------------------------


def test_generated_token_is_persisted_and_never_printed(
    compose_stack: ComposeStack,
) -> None:
    """
    M-3.7 / AC-14 - on a FRESH `shc-state` volume: `/app/.shc/shared_token`
    exists and is non-empty; `docker compose logs backend` contains **no
    occurrence of that token's value**; the logs **do** contain a WARNING naming
    `/app/.shc/shared_token`; and the value authenticates (M-3.8 below).

    Protects `backend/core/config.py:283-338` (`_resolve_shared_token`; the
    WARNING at `:335-342` "naming that file - never the value"),
    `docker-compose.yaml:18-21`, `:156-165`, and `docker/README.md:76-83`.

    **Why "fresh" is mandatory and not a convenience**: the WARNING is first-
    start only. `config.py:309` persists the token, and
    `docker/README.md:85-86` says so. A reused volume produces silence, so a
    reused volume makes this scenario vacuous - which is precisely why the
    suite's throwaway volumes (D4) are required rather than optional. The
    evidence is captured in the session fixture immediately after the first
    `up`, so this stays order-independent.

    **No secret is printed.** The absence check goes through
    `assert_not_in`, which cannot be reported by pytest's assertion rewriting
    (that would echo both operands). Two negative controls guard it against being
    vacuous: the log text is non-empty, and it DOES name the file - so "the
    token is absent" cannot be explained by "the logs are empty" or by "nothing
    was logged about the token at all".
    """
    token = compose_stack.first_start_token
    assert token, (
        "/app/.shc/shared_token is empty on a fresh volume. "
        "docker-compose.yaml:14-26 promises the token is generated on first "
        "start with no configuration at all."
    )

    logs = compose_stack.first_start_logs
    assert logs.strip(), (
        "the first start produced no logs at all, so 'the token is not in the "
        "logs' would be trivially true"
    )
    assert "/app/.shc/shared_token" in logs, (
        "the startup WARNING does not name /app/.shc/shared_token. The operator "
        "has to be told WHERE the generated token is - config.py:335-342 names "
        "the file precisely so the value never has to be printed.\n"
        f"(logs, {len(logs)} chars)"
    )
    assert "WARNING" in logs, (
        "the generated-token notice is not at WARNING level "
        "(backend/core/config.py:335): with SHARED_TOKEN unset and a typo, a "
        "cluster with a token nobody else has is otherwise indistinguishable "
        "from a correct one."
    )
    assert_not_in(
        token,
        logs,
        "the generated SHARED_TOKEN's value appears in `docker compose logs "
        "backend`. It is a credential and must never be logged - config.py:331-334 "
        "says so in as many words. (Neither the token nor any part of it is "
        "reproduced here; this is the assertion, reported without the operand.)",
    )


def test_generated_token_mints_a_jwt(compose_stack: ComposeStack) -> None:
    """
    M-3.8 - `POST /api/v1/auth/token` with the generated value returns 200 and a
    three-segment JWT.

    Protects `backend/api/v1/auth.py:57-88` and `docker-compose.yaml:167-173`.

    The control lives in `_mint_jwt`: a WRONG shared token is refused first. That
    is what makes the 200 meaningful - without it, an endpoint that ignored the
    credential entirely would pass. This replaces the earlier
    `${SHARED_TOKEN:?...}` guard, which refused to start: "that was correct as a
    refusal of a *guessable* token and useless as a refusal of an *unconfigured*
    one".
    """
    jwt = _mint_jwt(compose_stack)
    assert jwt and jwt.count(".") == JWT_SEGMENTS - 1, (
        f"expected a three-segment JWT, got a value with {jwt.count('.') + 1} "
        "segments"
    )


# ---------------------------------------------------------------------------
# M-3.9 / M-3.10  The non-localhost auth path, and its control (AC-15)
# ---------------------------------------------------------------------------


def test_missing_token_is_rejected_off_localhost(
    cluster: ClusterClient, compose_stack: ComposeStack
) -> None:
    """
    M-3.9 / AC-15 - with `Host: cluster.example.com` and **no** `Authorization`,
    `GET /api/v1/auth/verify` is 401 with `error_code == "AUTH_TOKEN_MISSING"`.

    Protects `backend/core/deps.py:26-42` and `backend/core/utils.py:24-43`.

    **This is the anti-vacuity control the repository insists on**, and it is
    why the test is one function with three assertions rather than three tests.
    The first request proves the rejection is real; the second, WITHOUT the
    `Host` override, proves the bypass is active on `127.0.0.1` - so the 401 in
    the first is caused by the `Host` header specifically and by nothing else.
    A 401-only test would pass whether or not authentication worked.
    """
    rejected = cluster.get(
        "/api/v1/auth/verify",
        headers=cluster.headers(host=NON_LOCALHOST_HOST_HEADER, token=""),
    )
    assert rejected.status_code == 401, (
        f"expected 401 with Host: {NON_LOCALHOST_HOST_HEADER}, got "
        f"{rejected.status_code}: {rejected.text}"
    )
    assert rejected.json().get("error_code") == "AUTH_TOKEN_MISSING", rejected.text

    bypassed = cluster.get(
        "/api/v1/auth/verify", headers=cluster.headers(host="127.0.0.1", token="")
    )
    assert bypassed.status_code == 200, (
        f"the localhost bypass is NOT active on 127.0.0.1 "
        f"({bypassed.status_code}: {bypassed.text}). Without this control the 401 "
        "above proves nothing, because it would be the same answer either way."
    )
    assert bypassed.json() == {"valid": False}, (
        f"{bypassed.text}. `backend/core/deps.py:26-31` short-circuits before the "
        "token is ever read, which is the trap "
        "test_signing_key_rotation.py:12-19 documents."
    )


def test_minted_jwt_verifies_off_localhost(cluster: ClusterClient) -> None:
    """
    M-3.10 - with `Host: cluster.example.com` and the minted JWT,
    `GET /api/v1/auth/verify` is 200 `{"valid": true}`.

    Protects `backend/core/deps.py:40-42`.

    **The control assertion is present and it is in the previous test's shape**:
    the same request without an `Authorization` header is 401, and the same
    request without the `Host` override is 200 `{"valid": false}`. Both are
    asserted here again rather than relied upon across test functions, because a
    test that depends on another test having run is a test that passes for the
    wrong reason when it is run alone with `-k`.
    """
    verified = cluster.get(
        "/api/v1/auth/verify", headers=cluster.headers(host=NON_LOCALHOST_HOST_HEADER)
    )
    assert verified.status_code == 200, verified.text
    assert verified.json() == {"valid": True}, (
        f"{verified.text} - the JWT minted from the generated shared token did not "
        "verify."
    )

    rejected = cluster.get(
        "/api/v1/auth/verify",
        headers=cluster.headers(host=NON_LOCALHOST_HOST_HEADER, token=""),
    )
    assert rejected.status_code == 401, (
        f"control: the same request without Authorization answered "
        f"{rejected.status_code}, not 401. The `valid: true` above would then "
        "prove nothing about the token."
    )


# ---------------------------------------------------------------------------
# SHOULD S-5  Asset bundles get the immutable cache header
# ---------------------------------------------------------------------------


def test_asset_bundles_get_the_immutable_cache_header(
    cluster: ClusterClient, compose_stack: ComposeStack
) -> None:
    """
    SHOULD S-5 - `GET /assets/<h>.js` answers
    `Cache-Control: public, max-age=31536000, immutable`.

    Protects `backend/core/frontend.py:52-56` and `:144-149`: asset bundles are
    content-hashed, so a given URL's bytes can never change, and serving them
    with a one-year immutable cache is what the emitted `.output/public/_headers`
    already asks for on Cloudflare. That file is inert here, so the intent is
    applied explicitly in Python instead.

    The asset name is read out of the container rather than guessed, and the
    non-asset shell is compared alongside: a header applied to everything would
    satisfy this test and be wrong for `/`, whose content does change per deploy.
    """
    listing = compose_stack.exec(
        "sh", "-c", "ls /app/frontend/assets | grep '\\.js$' | head -n 1"
    ).stdout.strip()
    assert listing, (
        "no .js asset under /app/frontend/assets; the frontend build produced no "
        "bundles (docker/Dockerfile:112-115)"
    )

    response = cluster.get(f"/assets/{listing}")
    assert response.status_code == 200, (
        f"/assets/{listing} answered {response.status_code}; "
        "backend/core/frontend.py:141-151 serves existing files directly"
    )
    assert (
        response.headers.get("cache-control") == "public, max-age=31536000, immutable"
    ), (
        f"cache-control is {response.headers.get('cache-control')!r} for a "
        "content-hashed bundle (backend/core/frontend.py:52-56)"
    )

    shell = cluster.get("/")
    assert "immutable" not in shell.headers.get("cache-control", ""), (
        "the SPA shell is not content-hashed, so it must not get the immutable "
        "header; it would pin a browser to a document that changes per deploy "
        "(backend/core/frontend.py:144-149)"
    )
