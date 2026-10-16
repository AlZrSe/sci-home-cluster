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

1. ``/api/v1/**``, ``/docs``, ``/redoc`` - never this module's business. The API
   routers and FastAPI's own docs routes are registered first and match first;
   this handler only ever sees what they declined. A path under ``/api/v1`` that
   reached here is an unknown API route and must still answer 404 JSON, so it
   is re-raised rather than answered with a page of HTML.
2. An existing file under the directory - the hashed asset bundles, plus
   ``favicon.ico`` and ``robots.txt``.
3. Everything else - the shell document. The router hydrates it and renders the
   real route client-side, which is what makes ``/jobs/<id>`` work despite there
   being no such file to serve.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

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

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> Response:
        """Serve a real file if one exists, otherwise the SPA shell.

        Registered last, so this only sees paths the API routers, the docs
        routes and the root handler all declined.
        """
        path = "/" + full_path.lstrip("/")

        # An unknown API route stays a JSON 404 rather than becoming a page of
        # HTML, which would otherwise turn a typo'd endpoint into a confusing
        # 200 with the app's markup in it.
        for prefix in reserved:
            if path == prefix or path.startswith(prefix + "/"):
                raise HTTPException(status_code=404, detail="Not Found")

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


def serving_frontend() -> bool:
    """Whether this process serves the SPA. Consulted before registering `/`."""
    return settings.SERVE_FRONTEND
