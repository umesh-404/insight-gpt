# InsightGPT

**An AI-powered enterprise intelligence platform — ask your business data
questions in plain English and get explainable, cited answers.**

InsightGPT ingests structured and unstructured business data, runs a scalable
ELT pipeline into a modeled warehouse, indexes documents in a vector database,
and uses a Retrieval-Augmented Generation (RAG) architecture together with
**semantic-layer-grounded text-to-SQL** so anyone can ask questions like:

- *"Why did sales decline last quarter?"*
- *"Which products should we restock?"*
- *"Summarize customer complaints this month."*

Every answer shows its work — the SQL that produced a number and the documents
behind a claim — and is presented through interactive dashboards, a
conversational analytics interface, and exportable executive reports.

> Status: **design & documentation complete; implementation follows the phased
> plan in [`docs/11-roadmap.md`](docs/11-roadmap.md).** The `docs/` folder is the
> source of truth.

---

## What's inside

```
your data ──▶ ingestion + redaction ──▶ PostgreSQL (raw)
                                   │
                                   ├─▶ dbt ──▶ star schema + semantic metrics
                                   └─▶ chunk + embed ──▶ Qdrant (documents)

           natural-language question
                    │
            ┌───────▼────────┐
            │  Insight engine │  router → grounded text-to-SQL (structured)
            │                 │         + hybrid retrieval (unstructured)
            └───────┬────────┘         → cited, explainable answer + chart
                    │
        FastAPI (REST + SSE) ──▶ Next.js dashboards, chat, reports
```

## Architecture at a glance

| Layer | Technology |
|---|---|
| Warehouse | PostgreSQL + **dbt** (star schema + semantic metrics) |
| Vector DB / retrieval | **Qdrant** + local embeddings & rerank (**Ollama**), hybrid search |
| Insight engine | Semantic-layer-grounded text-to-SQL + RAG synthesis |
| LLM access | **Pluggable provider** (local Ollama default; OpenAI / Gemini / Groq optional) |
| Orchestration | FastAPI worker + **APScheduler** (pipeline runs & tracking) |
| API | **FastAPI** (Python 3.12), JWT auth + roles, SSE streaming |
| Frontend | **Next.js** + TypeScript + Tailwind + shadcn/ui |
| Packaging | **Docker Compose**, cloud-portable |
| Demo domain | Retail / e-commerce (synthetic) |

Design principle #1: **explainability over cleverness** — the LLM maps
questions onto a governed semantic layer instead of inventing SQL, and reasoning
runs locally by default with an optional cloud upgrade.

## Documentation

Start with the docs index: [`docs/README.md`](docs/README.md).

- [`00-overview.md`](docs/00-overview.md) — problem, goals, personas
- [`01-architecture.md`](docs/01-architecture.md) — system design & rationale
- [`02-data-model.md`](docs/02-data-model.md) — warehouse & semantic layer
- [`03-ingestion-etl.md`](docs/03-ingestion-etl.md) — connectors & ELT
- [`04-retrieval-rag.md`](docs/04-retrieval-rag.md) — vector search pipeline
- [`05-insight-engine.md`](docs/05-insight-engine.md) — the reasoning core
- [`06-api.md`](docs/06-api.md) — FastAPI surface
- [`07-frontend.md`](docs/07-frontend.md) — Next.js app
- [`08-security.md`](docs/08-security.md) — threat model & defenses
- [`09-deployment.md`](docs/09-deployment.md) — Docker Compose & cloud path
- [`10-testing-eval.md`](docs/10-testing-eval.md) — tests & eval harnesses
- [`11-roadmap.md`](docs/11-roadmap.md) — phased build plan

## Quick start

> The runnable stack lands in Phase 0 of the roadmap. Once scaffolded:

```bash
docker compose up
```

…brings up the warehouse, vector DB, local model runtime, API, worker, and web
app, then bootstraps the demo dataset. Configuration is via `config/*.yaml` and
`.env` (see `.env.example`).

## License

MIT
