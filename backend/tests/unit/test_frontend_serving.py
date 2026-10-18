"""
Routing tests for the SPA fallback (issue #140).

``backend/core/frontend.py`` serves the single-page app from a catch-all
``GET /{full_path:path}``. That catch-all matches every path, and Starlette's
router returns on the first full match - so while it claimed ``/api/**``, the
``redirect_slashes`` fallback could never run, and the dashboard's own requests
404ed against the image the dashboard ships in::

    GET /api/v1/nodes   -> 404        (what frontend/src/services/http.ts sends)
    GET /api/v1/nodes/  -> 200 []     (the route the router actually declares)

``_SpaFallbackRoute`` declines the reserved prefixes instead of answering them,
which restores the redirect. These tests pin that, and pin the three things the
catch-all must keep doing - serving the shell for client routes, serving real
files, and leaving the API namespace JSON.

Why a miniature app rather than ``backend.main.app``
----------------------------------------------------
The property under test is the interaction of a catch-all route with Starlette's
router, and the declaration SHAPE that makes it bite: an ``APIRouter`` mounted
at ``/api/v1/jobs`` whose collection path is ``"/"``. That is reproduced exactly,
with stub handlers, so the test needs no database, no auth token and no
subprocess - it runs in the gate that always executes. The shipped wiring, the
image, the generated token and the ``ErrorResponse`` envelope are the compose
suite's layer (``M-3.3`` .. ``M-3.5``) and are not duplicated here.

The real ``http_exception_handler`` from ``backend.main`` IS installed on the
test app, because the envelope on an unknown API path is a property this change
could plausibly have broken and it is cheap to check for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.core import frontend
from backend.core.config import Settings
from backend.main import http_exception_handler

SHELL_HTML = "<!doctype html><title>spa shell</title>"
ASSET_JS = "console.log('bundle')"

#: The prefix list under test, asserted so a rename cannot silently change what
#: the catch-all declines without this file noticing.
RESERVED = ("/api/v1", "/docs", "/redoc")


@pytest.fixture
def spa_dir(tmp_path: Path) -> Path:
    """A directory shaped like `.output/public`: a shell plus hashed assets."""
    (tmp_path / "assets").mkdir()
    (tmp_path / frontend.FRONTEND_SHELL_FILENAME).write_text(SHELL_HTML, "utf-8")
    (tmp_path / "assets" / "app-abc123.js").write_text(ASSET_JS, "utf-8")
    return tmp_path


@pytest.fixture
def spa_app(spa_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A miniature of the shipped app: the same route shapes, no real handlers.

    `include_router(router, prefix=...)` plus `@router.get("/")` is exactly how
    `backend/main.py` mounts jobs and nodes, and that combination is what makes
    `redirect_slashes` load-bearing.
    """
    monkeypatch.setattr(
        frontend, "settings", Settings(SERVE_FRONTEND=True, FRONTEND_DIR=str(spa_dir))
    )

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    jobs = APIRouter()

    @jobs.get("/")
    async def list_jobs() -> dict:
        return {"items": [], "total": 0}

    @jobs.post("/")
    async def create_job() -> dict:
        return {"job_id": "job-1"}

    @jobs.get("/{job_id}")
    async def get_job(job_id: str) -> dict:
        return {"job_id": job_id}

    nodes = APIRouter()

    @nodes.get("/")
    async def list_nodes() -> list:
        return []

    @nodes.post("/")
    async def create_node() -> dict:
        return {"node_id": "node-01"}

    # POST-only, so `GET /api/v1/auth/token` is a wrong method on a path that
    # exists - the 405 case, which must be distinguishable from 404.
    auth = APIRouter()

    @auth.post("/token")
    async def mint_token() -> dict:
        return {"access_token": "x"}

    app.include_router(jobs, prefix="/api/v1/jobs")
    app.include_router(nodes, prefix="/api/v1/nodes")
    app.include_router(auth, prefix="/api/v1/auth")
    frontend.register_frontend(app)
    return app


@pytest.fixture
def client(spa_app: FastAPI) -> TestClient:
    """A client over the miniature app.

    `follow_redirects` is set PER REQUEST rather than on the constructor:
    Starlette 0.27's TestClient has no such parameter (it predates the httpx
    client-level flag), and the default has to stay on so the "the redirect
    leads somewhere" assertions are a real end-to-end walk.
    """
    return TestClient(spa_app)


@pytest.mark.unit
class TestCollectionRoutesWithoutTheirSlash:
    """#140 - the regression itself."""

    @pytest.mark.parametrize("path", ["/api/v1/jobs", "/api/v1/nodes"])
    def test_it_redirects_rather_than_404ing(self, client: TestClient, path: str):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307, (
            f"GET {path} answered {response.status_code}, not 307. The SPA "
            "catch-all claimed it (backend/core/frontend.py::_SpaFallbackRoute) "
            "and answered instead of letting Starlette redirect - issue #140."
        )
        assert response.headers["location"].endswith(path + "/"), (
            f"GET {path} redirected to {response.headers['location']!r}, "
            f"expected it to end with {path + '/'!r}"
        )

    @pytest.mark.parametrize("path", ["/api/v1/jobs", "/api/v1/nodes"])
    def test_the_redirect_target_answers(self, client: TestClient, path: str):
        """The redirect must lead somewhere, not merely exist."""
        followed = client.get(path)
        assert followed.status_code == 200, (
            f"GET {path} redirected to something that answered "
            f"{followed.status_code}: {followed.text[:200]}"
        )

    def test_a_following_client_sees_200_for_the_whole_chain(self, client: TestClient):
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}

    def test_the_body_is_not_an_spa_shell(self, client: TestClient):
        """The failure mode #140 produced: a 200 carrying the app's markup."""
        response = client.get("/api/v1/jobs")
        assert "<!doctype html" not in response.text.lower(), (
            "GET /api/v1/jobs returned HTML; the SPA shell is leaking into the "
            "API namespace"
        )


@pytest.mark.unit
class TestWrongMethodOnAKnownPath:
    """#139 - the same root cause on the write path.

    A wrong method on a path that EXISTS must be 405, which only the APIRoute's
    own partial match can say. Before the fix the catch-all was the only thing
    that matched at all, so this was indistinguishable from an unknown path.
    """

    def test_get_on_a_post_only_collection_is_405(self, client: TestClient):
        response = client.get("/api/v1/auth/token")
        assert (
            response.status_code == 405
        ), f"expected 405, got {response.status_code}: {response.text[:200]}"

    def test_the_405_still_carries_allow(self, client: TestClient):
        """The one header that tells a client which methods to try.

        Starlette raises the 405 with `headers={"Allow": ...}`, and the
        ErrorResponse handler used to drop `exc.headers` - so routing-level
        answers lost it while application-raised ones kept it.
        """
        response = client.get("/api/v1/auth/token")
        allow = response.headers.get("allow", "")
        assert "POST" in allow, f"Allow is {allow!r} on a 405 for a POST-only path"


@pytest.mark.unit
class TestTheApiNamespaceIsStillJson:
    """The property the old in-handler re-raise existed to protect (M-3.5)."""

    def test_an_unknown_api_path_is_404(self, client: TestClient):
        assert client.get("/api/v1/no-such-route").status_code == 404

    def test_an_unknown_api_path_carries_no_html(self, client: TestClient):
        body = client.get("/api/v1/no-such-route").text.lower()
        assert "<!doctype html" not in body, (
            "an unknown API route returned HTML; the SPA fallback is leaking "
            "into the API namespace"
        )

    def test_an_unknown_api_path_keeps_the_error_response_envelope(
        self, client: TestClient
    ):
        """The project's own handler, so the body shape is unchanged."""
        response = client.get("/api/v1/no-such-route")
        payload = response.json()
        assert payload["status"] == 404
        assert payload["error_code"] == "NOT_FOUND"

    @pytest.mark.parametrize("path", ["/docs", "/redoc"])
    def test_reserved_prefixes_are_not_the_catch_alls(
        self, client: TestClient, path: str
    ):
        response = client.get(path)
        assert response.status_code == 200, (
            f"GET {path} answered {response.status_code}; the SPA catch-all must "
            f"decline {RESERVED!r}, not answer for them"
        )


@pytest.mark.unit
class TestSpaServingIsUnchanged:
    """What the catch-all is FOR. #140 must not have cost it anything."""

    def test_the_root_serves_the_shell(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 200
        assert response.text == SHELL_HTML

    def test_a_client_route_serves_the_same_shell(self, client: TestClient):
        """`/jobs/job-1` is not a file; the SPA routes it client-side."""
        response = client.get("/jobs/job-1")
        assert response.status_code == 200
        assert response.text == SHELL_HTML

    def test_an_api_sub_path_still_belongs_to_the_api(self, client: TestClient):
        """`/api/v1/jobs/job-1` must not fall through to the shell."""
        response = client.get("/api/v1/jobs/job-1")
        assert response.status_code == 200
        assert response.json() == {"job_id": "job-1"}

    def test_a_real_asset_is_served_with_the_immutable_header(self, client: TestClient):
        response = client.get("/assets/app-abc123.js")
        assert response.status_code == 200
        assert response.text == ASSET_JS
        assert response.headers["cache-control"] == frontend._IMMUTABLE_CACHE_CONTROL

    def test_the_shell_itself_is_not_immutable(self, client: TestClient):
        """A header applied to everything would satisfy the test above and be wrong."""
        assert "immutable" not in client.get("/").headers.get("cache-control", "")


@pytest.mark.unit
class TestTheCatchAllDeclinesRatherThanAnswers:
    """The mechanism itself, so a future edit cannot quietly undo it."""

    def test_a_reserved_path_has_no_matching_route(self, spa_app: FastAPI):
        matches = [
            route
            for route in spa_app.router.routes
            if isinstance(route, frontend._SpaFallbackRoute)
        ]
        assert len(matches) == 1, "the SPA fallback must be registered exactly once"
        route = matches[0]

        scope = {"type": "http", "path": "/api/v1/jobs", "method": "GET"}
        match, _ = route.matches(scope)
        assert match.name == "NONE", (
            "the SPA catch-all still matches /api/v1/jobs; it must DECLINE so "
            "Starlette's redirect_slashes can run (issue #140)"
        )

    def test_a_spa_path_still_matches(self, spa_app: FastAPI):
        route = next(
            route
            for route in spa_app.router.routes
            if isinstance(route, frontend._SpaFallbackRoute)
        )
        scope = {"type": "http", "path": "/jobs/job-1", "method": "GET"}
        match, _ = route.matches(scope)
        assert match.name == "FULL"

    def test_the_route_is_registered_last(self, spa_app: FastAPI):
        assert isinstance(spa_app.router.routes[-1], frontend._SpaFallbackRoute), (
            "the SPA catch-all must be registered last, or it would be "
            "considered before the API routers"
        )

    def test_it_is_absent_from_the_openapi_schema(self, spa_app: FastAPI):
        paths = spa_app.openapi()["paths"]
        assert (
            "/{full_path}" not in paths and "/{full_path}/" not in paths
        ), "the SPA catch-all must stay out of the documented API surface"

    def test_the_declared_api_paths_are_unchanged(self, spa_app: FastAPI):
        """The fix must not add, remove or rename a documented operation.

        Read as a record rather than a constant: the collection routes are
        documented WITH their trailing slash, which is the whole reason the
        redirect is load-bearing. Asserting the exact set means a future edit
        that quietly adds the no-slash spelling here has to say so in this file.
        """
        paths = spa_app.openapi()["paths"]
        assert set(paths) == {
            "/api/v1/auth/token",
            "/api/v1/jobs/",
            "/api/v1/jobs/{job_id}",
            "/api/v1/nodes/",
        }, f"the documented operation set changed: {sorted(paths)}"


@pytest.mark.unit
class TestIsReserved:
    """The predicate, at its boundaries."""

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/api/v1", True),
            ("/api/v1/", True),
            ("/api/v1/jobs", True),
            ("/docs", True),
            ("/redoc", True),
            # Boundaries: a prefix must not match a longer word that merely
            # starts with the same characters.
            ("/api/v10/jobs", False),
            ("/api/v2", False),
            ("/docs-archive", False),
            ("/documentation", False),
            ("/jobs/job-1", False),
            ("/assets/app.js", False),
            ("/", False),
            ("", False),
        ],
    )
    def test_boundaries(self, path: str, expected: bool):
        assert frontend._is_reserved(path, RESERVED) is expected
