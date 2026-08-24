"""The one error envelope and the typed exceptions that map onto it (doc 06 §5).

Every non-2xx response is the same shape, produced by a single global handler,
so the client has exactly one error path. Handlers never leak stack traces or
SQL — the ``request_id`` correlates the sanitized client message with the full
server-side log line.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


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

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


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


def _json(status_code: int, body: dict[str, Any], request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=body, headers={"X-Request-ID": request_id}
    )


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    rid = _request_id(request)
    return _json(exc.status_code, _envelope(exc.code, exc.message, rid, exc.details), rid)


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    rid = _request_id(request)
    details = {"errors": [_slim_error(e) for e in exc.errors()]}
    body = _envelope("validation_error", "Request validation failed.", rid, details)
    return _json(status.HTTP_422_UNPROCESSABLE_ENTITY, body, rid)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    # Never leak the exception detail to the client; the request_id is the key
    # into the full server-side log line.
    body = _envelope("internal_error", "An unexpected error occurred.", rid)
    return _json(status.HTTP_500_INTERNAL_SERVER_ERROR, body, rid)


def _slim_error(err: dict[str, Any]) -> dict[str, Any]:
    return {
        "loc": list(err.get("loc", [])),
        "msg": err.get("msg", ""),
        "type": err.get("type", ""),
    }
