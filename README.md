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

```bash
docker compose up
```

…brings up the warehouse, vector DB, local model runtime, API, worker, and web
app, then bootstraps the demo dataset. Configuration is via `config/*.yaml` and
`.env` (see `.env.example`).

## Run the whole stack

The full system comes up with a single command. Everything is wired in
[`docker/compose.yml`](docker/compose.yml); a root [`Makefile`](Makefile) wraps
the common tasks.

**Prerequisites:** Docker + Docker Compose. First run pulls local models into a
named volume, so it takes a while and benefits from a GPU (CPU works, slower —
see [`docs/09-deployment.md`](docs/09-deployment.md) §5).

```bash
cp .env.example .env        # then edit secrets (JWT_SECRET, any cloud LLM keys)
make up                     # build + start all six services
make bootstrap              # first run only: pull models, build warehouse, index docs
```

Then open:

- **web** — http://localhost:3000
- **api** — http://localhost:8000 (health: `GET /health`)

### Topology

Six services on a private compose network; only `web` and `api` publish ports.

| Service | Image / build | Port | Role |
|---|---|---|---|
| `web` | `docker/web.Dockerfile` | `3000` (published) | Next.js frontend |
| `api` | `docker/api.Dockerfile` | `8000` (published) | FastAPI REST + SSE, insight engine, auth |
| `worker` | `docker/worker.Dockerfile` | — internal | APScheduler jobs, dbt, pipeline runs |
| `postgres` | `postgres:16` | — internal (loopback) | Warehouse (`raw` / `marts` / `insight`) |
| `qdrant` | `qdrant/qdrant` | — internal | Vector DB |
| `ollama` | `ollama/ollama` | — internal | Local embeddings, rerank, default LLM |

Persistent state lives in named volumes: `pgdata`, `qdrant_storage`,
`ollama_models`. `api` and `worker` wait for `postgres`, `qdrant`, and `ollama`
to be **healthy** before starting; `web` waits for `api`.

### Make targets

| Target | What it does |
|---|---|
| `make up` | Build and start the full stack (`docker compose up --build -d`) |
| `make bootstrap` | First-run: pull Ollama models, build the warehouse (generate → load → dbt), create the Qdrant collection, index sample documents. Idempotent. |
| `make down` | Stop the stack (named volumes preserved) |
| `make logs` | Tail all service logs |
| `make seed` | (Re)build the warehouse from synthetic data |
| `make test` | Run every service's offline test suite |
| `make clean` | Stop and **delete** all data volumes (destructive) |

Configuration is env-driven ([`.env.example`](.env.example) documents every
variable). The same images deploy to a single cloud VM unchanged, or decompose
onto a managed platform — see [`docs/09-deployment.md`](docs/09-deployment.md) §5.

## License

MIT
