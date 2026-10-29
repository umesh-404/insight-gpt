# InsightGPT API — Hugging Face Spaces & Container Deployment
# Builds the FastAPI engine, semantic layer, and attachment parser.
# Runs as non-root user 1000 on port 7860 (Hugging Face default).

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/api ./services/api
COPY services/retrieval ./services/retrieval
COPY config ./config
COPY data ./data

RUN uv pip install --system --no-cache \
        -e "./services/api[api]" \
        -e "./services/retrieval"

# --- Runtime stage ---
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    PYTHONPATH=/home/user/app/services/api:$PYTHONPATH

# Hugging Face Spaces runs as user 1000 with home /home/user
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR /home/user/app

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build --chown=user:user /app /home/user/app

EXPOSE 7860

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7860/health', timeout=4).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
