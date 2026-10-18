"""
Static frontend serving.

Lets this process answer for the single-page app as well as the API, so a
deployment is one origin on one port instead of a Node server and a Python
server side by side.

What is actually being served
-----------------------------
The frontend is a TanStack Start app. Left alone, `npm run build` emits client
assets into `.output/public` and *no HTML whatsoever* - every document is
rendered on request by the nitro server. Copying that directory into an image
and mounting it here would therefore serve the JS/CSS bundles and 404 on every
route.

So the build is run in SPA mode (see `frontend/vite.config.ts`), which
prerenders exactly one document at build time and writes it into the same
directory. That is what `FRONTEND_SHELL_FILENAME` names.

Routing
-------
Three kinds of request, in the order they are resolved:

1. ``/api/v1/**``, ``/docs``, ``/redoc`` - never this module's business, and
   not merely by convention. The catch-all below matches EVERY path, so if it
   also *claimed* these it would record a full match for a path the API routers
   declare with a trailing slash, and Starlette's ``redirect_slashes`` fallback
   would never run - see :class:`_SpaFallbackRoute` and issue #140. The catch-all
   therefore DECLINES to match them, which hands the request back to the router
   and restores the three behaviours that were all lost at once: the redirect,
   405 on a wrong method, and a JSON 404 for an unknown API path.
2. An existing file under the directory - the hashed asset bundles, plus
   ``favicon.ico`` and ``robots.txt``.
3. Everything else - the shell document. The router hydrates it and renders the
   real route client-side, which is what makes ``/jobs/<id>`` work despite there
   being no such file to serve.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope

from backend.core.config import settings

logger = logging.getLogger(__name__)

# The prerendered SPA document. Matches `spa.prerender.outputPath` in
# frontend/vite.config.ts; the build appends `.html` to that value itself.
FRONTEND_SHELL_FILENAME = "_shell.html"

# Asset bundles are content-hashed by the bundler, so a given URL's bytes can
# never change. Serving them with a one-year immutable cache is safe and is what
# the emitted `.output/public/_headers` already asks for on Cloudflare - that file
# is inert here, so the intent is applied explicitly instead.
_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _reserved_prefixes() -> tuple[str, ...]:
    """Path prefixes that must never fall through to the SPA shell.

    Expressed with a leading slash and compared against a normalised leading-slash
    path, so ``API_V1_STR = "/api/v1"`` and a request for ``api/v1/jobs`` are
    treated the same way.
    """
    prefixes = [settings.API_V1_STR, "/docs", "/redoc"]
    return tuple(sorted({"/" + p.strip("/") for p in prefixes if p.strip("/")}))


def _is_reserved(path: str, reserved: Tuple[str, ...]) -> bool:
    """Whether ``path`` is one of ``reserved`` or sits under one of them."""
    normalised = "/" + path.lstrip("/")
    return any(
        normalised == prefix or normalised.startswith(prefix + "/")
        for prefix in reserved
    )


class _SpaFallbackRoute(APIRoute):
    """The SPA catch-all, taught NOT to claim the paths it must not answer.

    ``/{full_path:path}`` matches every path, and Starlette's router returns on
    the first FULL match - so a plain catch-all records a full match for
    ``/api/v1/jobs`` and the ``redirect_slashes`` fallback never runs. The API
    routers declare their collection paths WITH a trailing slash (FastAPI
    rejects ``@router.get("")``), so ``/api/v1/jobs`` used to be swallowed here
    and answered with a JSON 404 rather than redirected to the route that exists.

    The shipped SPA requests exactly that spelling -
    ``frontend/src/services/http.ts`` builds ``/jobs`` and ``/nodes`` - so the
    dashboard's job list and node list answered 404 against the image the
    dashboard ships in (issue #140). Declining a match instead of answering one
    restores, for free:

    * ``/api/v1/jobs`` -> 307 -> ``/api/v1/jobs/`` -> 200;
    * a wrong METHOD on a real API path -> 405 rather than 404, because the
      ``APIRoute`` itself reports the partial match. This is the same root cause
      as issue #139's ``POST /api/v1/jobs`` answering 405 with no redirect, so it
      is fixed by the same edit;
    * an unknown API path -> 404 through the app's own ``HTTPException``
      handler, so the body is still the ``ErrorResponse`` envelope and not a page
      of HTML (compose ``M-3.5``).

    ``reserved`` is set after construction because ``APIRoute.__init__`` has no
    parameter for it. There is exactly one instance and it is built in
    :func:`register_frontend`, where the prefixes are already computed - so the
    route and the registration cannot disagree about what is reserved.
    """

    reserved: Tuple[str, ...] = ()

    def matches(self, scope: Scope) -> Tuple[Match, Scope]:
        if scope.get("type") == "http" and _is_reserved(
            scope.get("path", ""), self.reserved
        ):
            # Match.NONE, not an exception: declining is what lets the router
            # reach redirect_slashes, and answering here is the bug.
            return Match.NONE, {}
        return super().matches(scope)


def _resolve_within(directory: Path, relative_path: str) -> Path | None:
    """Resolve ``relative_path`` under ``directory``, or None if it escapes.

    ``directory`` is resolved first so a symlinked directory cannot make the
    containment test vacuous. The request path comes straight off the URL, so
    ``../../etc/passwd`` and ``C:\\Windows\\win.ini`` are ordinary inputs here and
    have to fail closed rather than be read.

    Windows is called out because the resolved path of ``C:\\x`` is absolute
    while the joined one may appear relative, and because a leading drive letter
    is not something :func:`Path.is_relative_to` alone rejects.
    """
    if not relative_path:
        return None

    base = directory.resolve()

    # Reject before joining: a drive letter or UNC prefix would make the join
    # discard `base` entirely, and `..` is collapsed by resolve() below anyway.
    if Path(relative_path).is_absolute() or relative_path.startswith(("/", "\\")):
        return None

    candidate = (base / relative_path).resolve()
    if candidate == base or not candidate.is_relative_to(base):
        return None
    return candidate


def register_frontend(app: FastAPI) -> None:
    """Attach SPA serving to ``app``. A no-op unless SERVE_FRONTEND is set.

    Raises if it is enabled but the build is not there. An image that was asked
    to serve the frontend and cannot is broken, and failing at import makes the
    container fail fast and visibly rather than serve an API whose UI 404s on
    every route - the failure mode that looks healthy until someone opens the
    page.
    """
    if not settings.SERVE_FRONTEND:
        logger.debug("SERVE_FRONTEND is off; not serving static frontend files.")
        return

    directory = Path(settings.FRONTEND_DIR)
    shell = directory / FRONTEND_SHELL_FILENAME

    if not shell.is_file():
        raise RuntimeError(
            f"SERVE_FRONTEND is on but {shell} does not exist. The frontend must be "
            "built with SPA mode enabled (see frontend/vite.config.ts) before the "
            "backend can serve it; `npm run build` writes it into "
            ".output/public. Either build the frontend or set SERVE_FRONTEND=false."
        )

    resolved = directory.resolve()
    reserved = _reserved_prefixes()

    async def serve_frontend(full_path: str) -> Response:
        """Serve a real file if one exists, otherwise the SPA shell.

        Reached only for paths the API routers, the docs routes and the root
        handler all declined, AND that :class:`_SpaFallbackRoute` agreed to claim.
        The reserved prefixes cannot arrive here at all: the route declines them
        so the router can answer them itself. See that class for why, and for
        issue #140.
        """
        path = "/" + full_path.lstrip("/")

        if full_path:
            candidate = _resolve_within(resolved, full_path)
            if candidate is not None and candidate.is_file():
                headers = (
                    {"Cache-Control": _IMMUTABLE_CACHE_CONTROL}
                    if path.startswith("/assets/")
                    else None
                )
                return FileResponse(candidate, headers=headers)

        return FileResponse(shell, media_type="text/html")

    route = _SpaFallbackRoute(
        "/{full_path:path}",
        serve_frontend,
        methods=["GET"],
        include_in_schema=False,
        name="serve_frontend",
        # `app.dependency_overrides` IS the provider dict `APIRouter` holds for
        # its own routes; wiring the catch-all to the same object keeps
        # `app.dependency_overrides[...]` working for this route too.
        dependency_overrides_provider=app.dependency_overrides,
    )
    route.reserved = reserved

    # Appended rather than decorated with `@app.get(...)`, because the decorator
    # builds a plain APIRoute and would put the catch-all back into the shape that
    # defeats the redirect. Registered last, so everything above sees a request
    # first - `register_frontend` is called at the very bottom of backend/main.py.
    app.router.routes.append(route)


def serving_frontend() -> bool:
    """Whether this process serves the SPA. Consulted before registering `/`."""
    return settings.SERVE_FRONTEND
