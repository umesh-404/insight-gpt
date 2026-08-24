# InsightGPT — API & Insight Engine

The reasoning core of InsightGPT: an NL question in, a single explainable,
cited **answer envelope** out. It runs **fully offline** on a fixture warehouse
and a deterministic provider, so nothing external is needed to try it.

Design: [`../../docs/05-insight-engine.md`](../../docs/05-insight-engine.md).

## Layout

```
app/
  semantic/        governed catalog (config/semantic_layer.yml) + deterministic SQL builder
  engine/          router · structured path · contribution · retrieval · synthesis · guardrails · envelope
  providers/       pluggable LLM providers (fake · ollama · openai/groq) + factory
  warehouse/       read-only executor (DuckDB fixture now, Postgres later)
  fixtures/        seeded retail star schema + sample documents
tests/             guardrails · query builder · end-to-end engine
```

The reliability keystone: **the LLM never writes SQL and never invents a
number.** It selects governed metrics/dimensions; a deterministic builder emits
parameterized SQL; guardrails parse and gate it; the warehouse returns the
figures; synthesis narrates them with citations.

## Quick start

```bash
uv venv --python 3.12
uv pip install pydantic pydantic-settings pyyaml sqlglot httpx duckdb pytest

# ask a question offline (fixture warehouse + deterministic provider)
LLM_PROVIDER=fake uv run python -m app.cli "Why did sales decline last quarter?"

# run the tests
uv run pytest -q
```

## Using a real model

Embeddings/rerank stay local (Ollama). For the reasoning step, either run a
local model via Ollama (default) or point at a cloud provider with a key in the
environment — never in the repo:

```bash
# local (default)
LLM_PROVIDER=ollama LLM_MODEL=llama3.1:8b uv run python -m app.cli "..."

# cloud (key from env)
LLM_PROVIDER=openai OPENAI_API_KEY=... uv run python -m app.cli "..."
```

## The answer envelope

Every answer is one typed object: `answer`, `route`, `sql[]`, `tables[]`,
`citations[]`, `chart`, `confidence`, `caveats[]`. See
[`app/engine/envelope.py`](app/engine/envelope.py).
