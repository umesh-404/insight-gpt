"""The governed semantic layer, loaded from ``config/semantic_layer.yml``.

The catalog is the *only* menu the LLM may choose from. Every metric, dimension,
join, and aggregation the engine can use is declared here — so the model selects
names that exist and a deterministic builder emits correct SQL. See
``docs/05-insight-engine.md`` §3 and ``docs/02-data-model.md`` §6.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Fact(BaseModel):
    name: str
    grain: str
    keys: list[str] = Field(default_factory=list)
    # dim_table -> join column present on both fact and dim
    joins: dict[str, str] = Field(default_factory=dict)


class Dimension(BaseModel):
    name: str
    table: str
    # Non-date dims use a single ``expr``; the date dim uses ``grains``.
    expr: str | None = None
    grains: dict[str, str] = Field(default_factory=dict)
    default_grain: str | None = None
    time_column: str | None = None
    key: str | None = None  # column for id-based entity filters

    def is_date(self) -> bool:
        return bool(self.grains)

    def grain_expr(self, grain: str | None) -> str:
        """Return the SQL column for a date grain (or the plain expr otherwise)."""
        if not self.is_date():
            assert self.expr is not None
            return self.expr
        g = grain or self.default_grain
        if g not in self.grains:
            raise CatalogError(
                f"unknown grain {g!r} for dimension {self.name!r}; "
                f"allowed: {sorted(self.grains)}"
            )
        return self.grains[g]


class Metric(BaseModel):
    name: str
    label: str
    expr: str
    fact: str
    additive: bool = True
    format: str = "number"
    aliases: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)


class CatalogError(ValueError):
    """Raised when a catalog file or a selection references something invalid."""


class SemanticCatalog(BaseModel):
    version: int
    facts: dict[str, Fact]
    dimensions: dict[str, Dimension]
    metrics: dict[str, Metric]
    allow_tables: list[str]
    max_rows: int = 5000
    default_rows: int = 1000
    statement_timeout_ms: int = 10000

    # ---- loading -------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path) -> SemanticCatalog:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        # ``joins`` in yaml is {dim: {column: col}}; flatten to {dim: col} before
        # constructing the model.
        facts = {}
        for name, body in raw["facts"].items():
            joins = {d: j["column"] for d, j in body.get("joins", {}).items()}
            facts[name] = Fact(
                name=name, grain=body["grain"], keys=body.get("keys", []), joins=joins
            )
        dims = {name: Dimension(name=name, **body) for name, body in raw["dimensions"].items()}
        metrics = {name: Metric(name=name, **body) for name, body in raw["metrics"].items()}
        limits = raw.get("limits", {})
        cat = cls(
            version=raw["version"],
            facts=facts,
            dimensions=dims,
            metrics=metrics,
            allow_tables=raw["allow_list"]["tables"],
            max_rows=limits.get("max_rows", 5000),
            default_rows=limits.get("default_rows", 1000),
            statement_timeout_ms=limits.get("statement_timeout_ms", 10000),
        )
        cat._validate_integrity()
        return cat

    # ---- integrity -----------------------------------------------------------
    def _validate_integrity(self) -> None:
        """Fail fast on an internally inconsistent catalog (a config bug)."""
        for m in self.metrics.values():
            if m.fact not in self.facts:
                raise CatalogError(f"metric {m.name!r} references unknown fact {m.fact!r}")
            for d in m.dimensions:
                if d not in self.dimensions:
                    raise CatalogError(f"metric {m.name!r} allows unknown dimension {d!r}")
                dim = self.dimensions[d]
                fact = self.facts[m.fact]
                # every allowed dimension must be reachable from the metric's fact
                if dim.table not in fact.joins and dim.table != fact.name:
                    raise CatalogError(
                        f"dimension {d!r} (table {dim.table!r}) is not joinable "
                        f"from fact {m.fact!r} for metric {m.name!r}"
                    )

    # ---- lookups -------------------------------------------------------------
    @property
    def alias_index(self) -> dict[str, str]:
        idx: dict[str, str] = {}
        for m in self.metrics.values():
            idx[m.name] = m.name
            for a in m.aliases:
                idx[a] = m.name
        return idx

    def resolve_metric(self, name: str) -> Metric:
        canonical = self.alias_index.get(name)
        if canonical is None:
            raise CatalogError(
                f"unknown metric {name!r}; governed metrics: {sorted(self.metrics)}"
            )
        return self.metrics[canonical]

    def resolve_dimension(self, name: str) -> Dimension:
        if name not in self.dimensions:
            raise CatalogError(
                f"unknown dimension {name!r}; governed dimensions: {sorted(self.dimensions)}"
            )
        return self.dimensions[name]

    def metric_names(self) -> list[str]:
        return sorted(self.metrics)

    def dimension_names(self) -> list[str]:
        return sorted(self.dimensions)


def default_catalog_path() -> Path:
    """Locate config/semantic_layer.yml from the repo root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "semantic_layer.yml"
        if candidate.exists():
            return candidate
    raise CatalogError("could not locate config/semantic_layer.yml")


@lru_cache(maxsize=1)
def load_catalog(path: str | None = None) -> SemanticCatalog:
    return SemanticCatalog.from_yaml(path or default_catalog_path())
