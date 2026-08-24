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
                                   │                └─▶ dbt ──▶ star schema
                                   │                            + semantic metrics
                                   └─▶ redacted corpus (content-hashed)
                                            └─▶ chunk + embed, changed-only
                                                     └─▶ Qdrant (documents)

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
| LLM access | **Pluggable provider** for the chat step (local Ollama default; OpenAI / Groq optional). Embeddings and rerank are always local Ollama. |
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

## Run the whole stack

The full system comes up with a single command. The topology is
[`docker/compose.yml`](docker/compose.yml), reached through the root
[`compose.yaml`](compose.yaml); a root [`Makefile`](Makefile) wraps the common
tasks.

> **Run compose from the repo root.** `make up` and a plain
> `docker compose up --build` both work. Do **not** run
> `docker compose -f docker/compose.yml up` directly: compose would take
> `docker/` as its project directory, never read the root `.env`, and silently
> fall back to every built-in default — so your `LLM_PROVIDER` would be ignored
> with no warning. See [`docs/09-deployment.md`](docs/09-deployment.md) §2.1.

**Prerequisites:** Docker + Docker Compose. First run pulls local models into a
named volume, so it takes a while and benefits from a GPU (CPU works, slower —
see [`docs/09-deployment.md`](docs/09-deployment.md) §5).

```bash
make env                    # copy .env.example -> .env
#                             then edit secrets (JWT_SECRET, any cloud LLM keys)
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
`ollama_models`, plus `generated_data` and `ingested_data` for the synthetic
dataset and the redacted document corpus (the seed runs in a throwaway
container; the scheduler that indexes the corpus does not).

`api` and `worker` wait for `postgres` and `ollama` to be **healthy** before
starting, and for `qdrant` to have **started** — the Qdrant image is distroless,
with no shell or curl for a healthcheck to use, so readiness is enforced by the
clients' own retries and by bootstrap polling `/readyz`. `web` waits for `api`
to be healthy.

### Make targets

| Target | What it does |
|---|---|
| `make up` | Build and start the full stack (`docker compose up --build -d`) |
| `make bootstrap` | First-run: pull Ollama models, build the warehouse (generate → load raw + publish the document corpus → dbt), create the Qdrant collection, index that corpus. Idempotent. |
| `make down` | Stop the stack (named volumes preserved) |
| `make logs` | Tail all service logs |
| `make seed` | (Re)build the warehouse and republish the document corpus |
| `make reindex` | Re-embed changed documents into Qdrant, tracked as a pipeline run |
| `make test` | Run every offline test suite (api, retrieval, ingestion, worker, generator, semantic-layer drift) |
| `make lint` | Ruff over every Python package |
| `make clean` | Stop and **delete** all data volumes (destructive) |

### The pipeline, end to end

Ingestion redacts the generated documents and publishes them to
`data/ingested/documents.json` with a content hash per document; the worker's
`reindex_docs` job reads exactly that file and re-embeds only what changed,
deleting the chunks of anything that disappeared upstream. Each job is
individually runnable and records a row in `insight.pipeline_runs`:

```bash
python -m worker run full_ingest        # reload raw.* + republish the corpus
python -m worker run incremental_sync   # changed units only
python -m worker run dbt_build          # staging -> marts + metrics
python -m worker run reindex_docs       # changed documents -> Qdrant
```

Configuration is env-driven ([`.env.example`](.env.example) documents every
variable). The same images deploy to a single cloud VM unchanged, or decompose
onto a managed platform — see [`docs/09-deployment.md`](docs/09-deployment.md) §5.

## License

MIT
