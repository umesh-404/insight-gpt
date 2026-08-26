"""The FastAPI application (doc 06 §1, §4).

Wires middleware (request id + structured logging), CORS for the web app, the
single error envelope, and the versioned routers under ``/api/v1`` plus the
unversioned operational routes. Run with ``uvicorn app.api.main:app``.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .deps import APP_VERSION
from .errors import (
    APIError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .observability import RequestContextMiddleware, configure_logging
from .routers import ask, auth, insights, metrics, pipelines, reports, sources, system

API_PREFIX = "/api/v1"


#: Dev origins the web app is served from. Both ports are covered so the
#: default `next dev` port and the project's 3020 both work out of the box.
_DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3020",
    "http://127.0.0.1:3020",
)


def _cors_origins() -> list[str]:
    """Explicit origin allow-list.

    ``allow_credentials=True`` is required for the httpOnly refresh cookie, and
    the CORS spec forbids pairing credentials with a ``*`` wildcard — a browser
    rejects the response outright. So a wildcard is dropped rather than honored,
    and the dev defaults are used instead.
    """
    raw = os.getenv("CORS_ORIGINS", ",".join(_DEFAULT_ORIGINS))
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or list(_DEFAULT_ORIGINS)


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title="InsightGPT API",
        version=APP_VERSION,
        description="Grounded, cited answers over a governed semantic layer + RAG.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )

    # --- middleware (applied once, not per-router) ----------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    # --- one error envelope for every failure ---------------------------------
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    # --- versioned application routers ----------------------------------------
    for module in (auth, ask, metrics, sources, pipelines, reports, insights):
        app.include_router(module.router, prefix=API_PREFIX)

    # --- unversioned operational routes ---------------------------------------
    app.include_router(system.router)

    return app


app = create_app()
