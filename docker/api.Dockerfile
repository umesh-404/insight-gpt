# InsightGPT API image — FastAPI + insight engine + retrieval backends.
#
# Build context is the REPO ROOT (see docker/compose.yml) so we can copy several
# service directories into one image.
#
#   docker build -f docker/api.Dockerfile -t insightgpt-api .
#
# Installs services/api[api] (FastAPI surface + real Postgres executor) and
# services/retrieval (editable) so BOTH the offline fixture stack and the real
# Qdrant/Ollama backends are importable at runtime, selected purely by env.

# --- deps stage: resolve + install into a system site-packages (no venv) ------
FROM python:3.12-slim AS build

# uv provides fast, reproducible installs. Copy the static binary from the
# official image rather than curl-installing it.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy only what the API image needs. config/ carries the governed semantic
# layer the engine locates by walking up from the installed package.
COPY services/api ./services/api
COPY services/retrieval ./services/retrieval
COPY config ./config

# Editable installs keep the source on disk (small image, real stack traces).
# [api] pulls FastAPI/uvicorn/pyjwt/psycopg; retrieval pulls qdrant-client/httpx.
RUN uv pip install --system --no-cache \
        -e "./services/api[api]" \
        -e "./services/retrieval"

# --- runtime stage ------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# System site-packages (with the editable *.pth links) and the copied sources.
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /app /app

# Non-root for defense in depth.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Public liveness endpoint used by the compose healthcheck (docs/09 §1.3).
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
