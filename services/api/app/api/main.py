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

from ..auth.tokens import verify_jwt_secret
from ..providers.base import ProviderError
from ..providers.factory import verify_provider_config
from .deps import APP_VERSION
from .errors import (
    APIError,
    api_error_handler,
    provider_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .observability import RequestContextMiddleware, configure_logging
from .routers import (
    ask,
    auth,
    forecast,
    insights,
    metrics,
    pipelines,
    reports,
    sources,
    system,
)

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

    # Fail before the first request rather than signing tokens with a secret
    # that is published in the repository (see app/auth/tokens.py).
    verify_jwt_secret()
    # Same contract for the reasoning model: a deployment missing its API key
    # should die on boot naming the variable, not pass health checks for an
    # hour and then 503 the first real question a user asks.
    verify_provider_config()

    app = FastAPI(
        title="InsightGPT API",
        version=APP_VERSION,
        description="Grounded, cited answers over a governed semantic layer + RAG.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )

    # --- middleware (applied once, not per-router) ----------------------------
    cors_origin_regex = os.getenv(
        "CORS_ORIGIN_REGEX",
        r"^https://.*\.vercel\.app$|^https://.*\.hf\.space$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_origin_regex=cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    # --- one error envelope for every failure ---------------------------------
    app.add_exception_handler(APIError, api_error_handler)
    # Registered before the catch-all so a provider outage reports as an
    # upstream 503 rather than an opaque 500 that looks like our own bug.
    app.add_exception_handler(ProviderError, provider_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    # --- versioned application routers ----------------------------------------
    for module in (auth, ask, metrics, sources, pipelines, reports, insights, forecast):
        app.include_router(module.router, prefix=API_PREFIX)

    # --- unversioned operational routes ---------------------------------------
    app.include_router(system.router)

    return app


app = create_app()
