"""The one error envelope and the typed exceptions that map onto it (doc 06 §5).

Every non-2xx response is the same shape, produced by a single global handler,
so the client has exactly one error path. Handlers never leak stack traces or
SQL — the ``request_id`` correlates the sanitized client message with the full
server-side log line.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("insightgpt.error")


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class APIError(Exception):
    """Base for typed API errors mapped to the envelope by the global handler."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details
        # Extra response headers (e.g. ``Retry-After`` on a 429).
        self.headers = headers or {}


class BadRequestError(APIError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


class UnauthorizedError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimitedError(APIError):
    """Bucket exhausted (doc 06 §8). Always carries a ``Retry-After`` header."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class DependencyUnavailableError(APIError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "dependency_unavailable"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def _envelope(
    code: str, message: str, request_id: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ErrorEnvelope(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details)
    ).model_dump()


def _json(
    status_code: int,
    body: dict[str, Any],
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged = {"X-Request-ID": request_id, **(headers or {})}
    return JSONResponse(status_code=status_code, content=body, headers=merged)


def _log_error(request: Request, rid: str, code: str, status_code: int, detail: str) -> None:
    """One structured server-side line per failure, keyed by the request id."""
    context = {
        "request_id": rid,
        "route": request.url.path,
        "method": request.method,
        "status": status_code,
        "code": code,
    }
    level = logging.ERROR if status_code >= 500 else logging.WARNING
    log.log(level, detail, extra={"context": context})


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    rid = _request_id(request)
    _log_error(request, rid, exc.code, exc.status_code, exc.message)
    return _json(
        exc.status_code,
        _envelope(exc.code, exc.message, rid, exc.details),
        rid,
        getattr(exc, "headers", None),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    rid = _request_id(request)
    details = {"errors": [_slim_error(e) for e in exc.errors()]}
    _log_error(
        request, rid, "validation_error", status.HTTP_422_UNPROCESSABLE_ENTITY,
        "request validation failed",
    )
    body = _envelope("validation_error", "Request validation failed.", rid, details)
    return _json(status.HTTP_422_UNPROCESSABLE_ENTITY, body, rid)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    # Never leak the exception detail to the client; the request_id is the key
    # into the full server-side log line — so that line must actually exist.
    log.error(
        "unhandled exception: %s: %s",
        type(exc).__name__,
        exc,
        exc_info=exc,
        extra={
            "context": {
                "request_id": rid,
                "route": request.url.path,
                "method": request.method,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "code": "internal_error",
            }
        },
    )
    body = _envelope("internal_error", "An unexpected error occurred.", rid)
    return _json(status.HTTP_500_INTERNAL_SERVER_ERROR, body, rid)


def _slim_error(err: dict[str, Any]) -> dict[str, Any]:
    return {
        "loc": list(err.get("loc", [])),
        "msg": err.get("msg", ""),
        "type": err.get("type", ""),
    }
