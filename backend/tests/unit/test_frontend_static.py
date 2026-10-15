"""
Tests for static SPA serving (backend/core/frontend.py).

The module is tested against a *fresh* FastAPI app rather than the global one.
That is deliberate: `backend.main` calls `register_frontend(app)` once at import,
when SERVE_FRONTEND is still False, so the shared app has no catch-all for the
whole suite. Building a throwaway app per test also means these tests cannot
leak a `/{full_path:path}` route into the 588 others - which would silently turn
every unmatched path in the suite into a page of HTML.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import frontend as frontend_module
from backend.core.config import settings
from backend.core.frontend import (
    FRONTEND_SHELL_FILENAME,
    _resolve_within,
    register_frontend,
    serving_frontend,
)

SHELL_BODY = "<!doctype html><title>shell</title>"


@pytest.fixture
def built_frontend(tmp_path) -> Path:
    """A directory shaped like `.output/public` after a successful SPA build.

    The shell file name is the one the build actually produces from
    `spa.prerender.outputPath` in frontend/vite.config.ts, which the build
    appends `.html` to. An asset file stands in for the hashed bundles.
    """
    directory = tmp_path / "public"
    (directory / "assets").mkdir(parents=True)
    (directory / FRONTEND_SHELL_FILENAME).write_text(SHELL_BODY, encoding="utf-8")
    (directory / "assets" / "index-abc123.js").write_text("console.log(1)")
    (directory / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    return directory


@pytest.fixture
def serving(monkeypatch, built_frontend) -> Path:
    """Point the settings singleton at `built_frontend` and enable serving."""
    monkeypatch.setattr(settings, "SERVE_FRONTEND", True)
    monkeypatch.setattr(settings, "FRONTEND_DIR", str(built_frontend))
    return built_frontend


def _client_with_frontend() -> TestClient:
    app = FastAPI()
    register_frontend(app)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_serving_is_off_by_default():
    """The backend is an API server until something asks it to be more.

    Off by default is what keeps `docker/`, local development and the test
    suite from depending on a frontend build that may not exist.
    """
    assert settings.SERVE_FRONTEND is False
    assert serving_frontend() is False


def test_disabled_registers_no_routes():
    """A no-op registration must not install a catch-all.

    `register_frontend` is called unconditionally from backend/main.py, so the
    disabled path is the one that runs in every non-container deployment.
    """
    app = FastAPI()
    register_frontend(app)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/{full_path:path}" not in paths


def test_enabled_but_unbuilt_raises(monkeypatch, tmp_path):
    """Refuse to start rather than serve an API whose every page 404s.

    An image asked to serve the frontend and holding no frontend is broken, and
    the quiet version of that failure looks healthy until someone opens a
    browser.
    """
    monkeypatch.setattr(settings, "SERVE_FRONTEND", True)
    monkeypatch.setattr(settings, "FRONTEND_DIR", str(tmp_path / "absent"))

    with pytest.raises(RuntimeError, match="_shell.html"):
        register_frontend(FastAPI())


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_root_serves_the_shell(serving):
    """`/` is the app in a container, not the JSON welcome."""
    response = _client_with_frontend().get("/")

    assert response.status_code == 200
    assert response.text == SHELL_BODY
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    "route",
    [
        "/jobs",
        "/jobs/some-id",
        "/nodes",
        "/login",
        "/settings",
        "/profiles/new",
    ],
)
def test_client_side_routes_fall_back_to_the_shell(serving, route):
    """Routes with no file on disk are the router's job, not a 404.

    These are the paths that make SPA mode worth having: `/jobs/<id>` is built
    from a runtime id, so it can never be a prerendered document.
    """
    assert _client_with_frontend().get(route).text == SHELL_BODY


def test_asset_is_served_with_an_immutable_cache_header(serving):
    """Asset bundles are content-hashed, so their bytes can never change.

    This is what the emitted `.output/public/_headers` asks for on Cloudflare;
    that file is inert when Python serves the bytes, so the intent is applied
    here instead.
    """
    response = _client_with_frontend().get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert response.text == "console.log(1)"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_public_file_is_served_without_that_header(serving):
    """`favicon.ico` is not content-hashed and must stay revalidated."""
    response = _client_with_frontend().get("/favicon.ico")

    assert response.status_code == 200
    assert "cache-control" not in response.headers


def test_unknown_api_route_stays_a_json_404(serving):
    """A typo'd endpoint must not come back as the app's HTML with a 200.

    The catch-all is registered after the API routers, so `/api/v1/does-not-exist`
    reaches it having been declined by them. Answering with the shell would turn
    a misspelled path into a page that looks like it loaded and then silently
    failed to fetch anything.
    """
    response = _client_with_frontend().get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert SHELL_BODY not in response.text


def test_docs_prefix_is_not_swallowed(serving):
    """`/docs` and its subpaths belong to FastAPI, never to the SPA."""
    client = _client_with_frontend()

    # A fresh app here has no docs routes registered of its own, so the point is
    # only that these paths do not resolve to the shell.
    assert client.get("/docs").text != SHELL_BODY
    assert client.get("/docs/oauth2-redirect").text != SHELL_BODY


def test_directory_request_falls_back_to_the_shell(serving):
    """`/assets` is a directory, so it is not a file to serve."""
    assert _client_with_frontend().get("/assets").text == SHELL_BODY


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attempt",
    [
        "../secret",
        "../../secret",
        "assets/../../secret",
        "./../secret",
        "..",
    ],
)
def test_resolve_within_rejects_escapes(built_frontend, attempt):
    """Nothing outside the frontend directory may be readable.

    `full_path` arrives straight off the URL, so `..` is an ordinary input
    rather than an attack to be detected later.
    """
    assert _resolve_within(built_frontend, attempt) is None


@pytest.mark.parametrize(
    "attempt", ["/etc/passwd", "\\etc\\passwd", "C:/Windows/win.ini"]
)
def test_resolve_within_rejects_absolute_paths(built_frontend, attempt):
    """An absolute or drive-qualified path would make the join discard the base.

    Checked before joining, because `Path("/app") / "/etc/passwd"` is
    `/etc/passwd` - the containment test afterwards never gets a chance to fail.
    """
    assert _resolve_within(built_frontend, attempt) is None


def test_resolve_within_accepts_a_real_child(built_frontend):
    """The positive case, so the guard above is not just rejecting everything."""
    resolved = _resolve_within(built_frontend, "assets/index-abc123.js")

    assert resolved is not None
    assert resolved.is_relative_to(built_frontend.resolve())
    assert resolved.is_file()


def test_resolve_within_rejects_the_root_itself(built_frontend):
    """The bare directory is not a servable file either."""
    assert _resolve_within(built_frontend, "") is None


def test_traversal_request_does_not_leak_a_file(serving, tmp_path):
    """End to end: an escaping path yields the shell, never foreign bytes.

    The client normalises `..` out of a URL before it is sent, so the HTTP-level
    check cannot prove much on its own; it is here to pin that whatever reaches
    the handler resolves to the shell rather than to a file outside the
    directory.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    response = _client_with_frontend().get("/../secret.txt")

    assert "TOP SECRET" not in response.text


def test_reserved_prefixes_cover_the_api_prefix():
    """The guard list is derived from settings, not hardcoded.

    Hardcoding `/api/v1` would drift from API_V1_STR, and the failure would be
    silent: API 404s quietly turning into HTML.

    The normalisation asserted here is what makes the handler's comparison
    correct. It tests `path == prefix or path.startswith(prefix + "/")` against
    a leading-slash path, so a prefix must keep its single leading slash and
    must not keep a trailing one - `"api/v1"` would match nothing, and
    `"/api/v1/"` would miss `/api/v1` itself.
    """
    prefixes = frontend_module._reserved_prefixes()

    assert settings.API_V1_STR in prefixes
    for prefix in prefixes:
        assert prefix.startswith("/"), prefix
        assert not prefix.startswith("//"), prefix
        assert not prefix.endswith("/"), prefix
