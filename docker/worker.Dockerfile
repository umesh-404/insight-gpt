# InsightGPT worker image — APScheduler jobs, ingestion, dbt, and first-run
# bootstrap. Entry point is `python -m worker`; bootstrap is run on demand
# (`make bootstrap` -> `scripts/bootstrap.py`).
#
# Build context is the REPO ROOT (see docker/compose.yml).
#
#   docker build -f docker/worker.Dockerfile -t insightgpt-worker .
#
# Installs services/worker + services/ingestion + services/retrieval and a
# dbt-postgres so the whole ELT + indexing pipeline runs from one container.
# The repo root is on PYTHONPATH so scripts/seed.py can import `services.*`,
# `data.*`, and `scripts.*` exactly as it does on a developer laptop.

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The worker touches the whole pipeline, so it needs the service packages, the
# synthetic-data generator, the dbt project, and the shared scripts/config.
COPY services/worker ./services/worker
COPY services/ingestion ./services/ingestion
COPY services/retrieval ./services/retrieval
COPY services/warehouse ./services/warehouse
COPY data ./data
COPY scripts ./scripts
COPY config ./config

# Editable installs for the clean packages; ingestion is imported by path
# (services.ingestion.*) via PYTHONPATH so we install its runtime deps directly.
# dbt-postgres gives seed.py a `dbt` on PATH targeting the compose Postgres.
RUN uv pip install --system --no-cache \
        -e "./services/worker" \
        -e "./services/retrieval" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.3" \
        "psycopg[binary]>=3.2" \
        "dbt-postgres>=1.8,<2.0"

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    DBT_PROFILES_DIR=/app/services/warehouse

WORKDIR /app

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /app /app

# data/generated (seed output) and data/ingested (the document corpus handed to
# retrieval) are written at runtime and are compose volume mount points. Create
# them here so the named volumes inherit appuser ownership rather than root's.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/generated /app/data/ingested \
    && chown -R appuser:appuser /app
USER appuser

# No published port. The scheduler is the long-running process; the first-run
# bootstrap is invoked separately (make bootstrap).
CMD ["python", "-m", "worker"]
