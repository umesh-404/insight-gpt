"""Data sources (admin) — doc 06 §3.3.

A thin CRUD surface over an in-memory store. Secrets (``dsn``) are accepted on
write but never returned on read. ``DELETE`` is a soft-delete that retains the
record for audit. ``POST /sources/{id}/test`` simulates a connectivity check
(no live connection in the offline demo).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr

from ...auth.roles import Role, require_role
from ..errors import NotFoundError

router = APIRouter(tags=["sources"])

SourceKind = Literal["postgres", "mysql", "csv", "excel", "documents"]


class SourceConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: SourceKind
    dsn: SecretStr | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    id: str
    name: str
    kind: str
    status: Literal["ok", "untested", "error"]
    last_tested_at: datetime | None = None
    active: bool = True


class _StoredSource(Source):
    dsn: SecretStr | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    ok: bool
    latency_ms: float
    tables_seen: int
    message: str


_SOURCES: dict[str, _StoredSource] = {}


def _public(s: _StoredSource) -> Source:
    return Source(
        id=s.id, name=s.name, kind=s.kind, status=s.status,
        last_tested_at=s.last_tested_at, active=s.active,
    )


@router.get("/sources", response_model=list[Source])
async def list_sources(_: object = Depends(require_role(Role.admin))) -> list[Source]:
    return [_public(s) for s in _SOURCES.values() if s.active]


@router.post("/sources", response_model=Source, status_code=201)
async def create_source(
    body: SourceConfig, _: object = Depends(require_role(Role.admin))
) -> Source:
    sid = f"src_{uuid.uuid4().hex[:12]}"
    stored = _StoredSource(
        id=sid, name=body.name, kind=body.kind, status="untested",
        dsn=body.dsn, options=body.options,
    )
    _SOURCES[sid] = stored
    return _public(stored)


@router.post("/sources/{source_id}/test", response_model=TestResult)
async def test_source(
    source_id: str, _: object = Depends(require_role(Role.admin))
) -> TestResult:
    stored = _SOURCES.get(source_id)
    if stored is None or not stored.active:
        raise NotFoundError(f"No source with id {source_id!r}.")
    # Offline demo: simulate a successful connectivity + introspection check.
    started = time.perf_counter()
    tables_seen = 0 if stored.kind == "documents" else 8
    stored.status = "ok"
    stored.last_tested_at = datetime.now(UTC)
    return TestResult(
        ok=True,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        tables_seen=tables_seen,
        message="Connection succeeded (simulated in offline mode).",
    )


@router.delete("/sources/{source_id}", status_code=200)
async def delete_source(
    source_id: str, _: object = Depends(require_role(Role.admin))
) -> dict[str, str]:
    stored = _SOURCES.get(source_id)
    if stored is None or not stored.active:
        raise NotFoundError(f"No source with id {source_id!r}.")
    stored.active = False  # soft-delete, retains history for audit
    return {"status": "deleted", "id": source_id}
