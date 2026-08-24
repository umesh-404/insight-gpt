# 09 — Deployment

This document describes how InsightGPT is packaged, configured, brought up, and
moved to the cloud. The design goal is the one stated in
[`00-overview.md`](00-overview.md): **the full system comes up with a single
`docker compose up`** from the repo root, and the same artifacts deploy to a
cloud host with documented, minimal changes.

Related reading: architecture and topology in
[`01-architecture.md`](01-architecture.md); the security posture that constrains
network exposure and secrets in [`08-security.md`](08-security.md); test/CI in
[`10-testing-eval.md`](10-testing-eval.md).

## 1. Docker Compose topology

Six services, wired on a private compose network. Only `web` and `api` publish
ports to the host; the data/model services are internal only (see
[`08-security.md`](08-security.md) §7).

```mermaid
graph TB
    subgraph Host["Host — published ports"]
        WEB["web · Next.js<br/>:3000 (published)"]
        API["api · FastAPI<br/>:8000 (published)"]
    end
    subgraph Internal["Compose network — internal only"]
        WORKER["worker · APScheduler<br/>(no published port)"]
        PG[("postgres :5432")]
        QD[("qdrant :6333/:6334")]
        OLL["ollama :11434"]
    end

    WEB -->|REST/SSE| API
    API --> PG
    API --> QD
    API --> OLL
    WORKER --> PG
    WORKER --> QD
    WORKER --> OLL

    PGVOL[["volume: pgdata"]] -.-> PG
    QDVOL[["volume: qdrant_storage"]] -.-> QD
    OLLVOL[["volume: ollama_models"]] -.-> OLL
```

### 1.1 Services

| Service | Image / build | Role | Published? |
|---|---|---|---|
| **web** | `docker/web.Dockerfile` (Next.js) | Frontend UI | Yes — `${WEB_PORT}:3000` |
| **api** | `docker/api.Dockerfile` (FastAPI/uvicorn) | REST + SSE, insight engine, auth | Yes — `${API_PORT}:8000` |
| **worker** | `docker/worker.Dockerfile` | APScheduler jobs, pipeline runs, ingestion, dbt invocations | No |
| **postgres** | `postgres:16` | Warehouse (`raw`/`staging`/`marts`) | No |
| **qdrant** | `qdrant/qdrant` | Vector DB | No |
| **ollama** | `ollama/ollama` | Local embeddings, rerank, default LLM | No |

`api` and `worker` are **separate images** built from the same repo root
context. They need genuinely different dependency sets — the API carries the web
stack, the worker carries dbt-postgres, the ingestion package, and the retrieval
indexer — and keeping them apart means neither image ships a dependency it never
calls. What is shared is the source, not the image: `services/ingestion`,
`services/retrieval`, `config/`, and the semantic layer are single definitions
copied into whichever image needs them.

### 1.2 Volumes (persistent state)

| Volume | Backs | Notes |
|---|---|---|
| `pgdata` | Postgres data dir | The warehouse — the durable source of truth |
| `qdrant_storage` | Qdrant storage | Vectors + payloads; rebuildable from documents |
| `ollama_models` | Pulled model blobs | Avoids re-downloading models on every restart |
| `generated_data` | `worker:/app/data/generated` | The synthetic CSVs + document JSON `scripts/seed.py` writes |
| `ingested_data` | `worker:/app/data/ingested` | The redacted document corpus handed to retrieval, plus its index state |

The last two exist because `make bootstrap` and `make seed` run in a throwaway
`docker compose run --rm worker` container, while the scheduler that reindexes
from that corpus is the long-running `worker` service. Without shared volumes
the corpus would die with the container that produced it and every scheduled
`reindex_docs` would fail with "document corpus not found". They are mounted at
the two output directories rather than at `/app/data`, so the baked-in
`data/generator` package stays visible.

`qdrant_storage`, `generated_data`, and `ingested_data` are all **rebuildable**
(re-seed, re-index), but `pgdata` for a real deployment is not — back it up
(Section 6).

### 1.3 Healthchecks & dependencies

Where a healthcheck can be written, dependents wait for *readiness* rather than
container start:

| Service | Healthcheck | Why this probe |
|---|---|---|
| **postgres** | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` | Ships in the image |
| **ollama** | `ollama list` | Same daemon as `GET /api/tags`, and the binary is always present |
| **api** | `python -c` urllib `GET http://localhost:8000/health` | `python` is always in the image; no curl needed |
| **worker** | `python -c` urllib `GET http://localhost:8090/health` | A stdlib health server on an internal-only port |
| **qdrant** | **none** | See below |
| **web** | **none** | Nothing depends on it |

**qdrant deliberately has no healthcheck.** The official image is distroless: it
contains no shell and no curl, so a `CMD-SHELL` or curl probe can never pass and
would leave the container permanently `unhealthy`, blocking every dependent
forever. Dependents therefore use `condition: service_started` and rely on their
own connection retries — plus `scripts/bootstrap.py`, which polls
`GET /readyz` from the worker (where `httpx` *is* installed) before doing
anything that needs Qdrant. That is a real readiness gate; it just lives in the
client rather than in the container.

So: `api` and `worker` wait on `postgres: service_healthy`,
`ollama: service_healthy`, and `qdrant: service_started`; `web` waits on
`api: service_healthy`.

## 2. Configuration

Configuration follows the **config-driven, no-hardcoded-ports** principle from
[`01-architecture.md`](01-architecture.md) §1:

- **`config/*.yaml`** — non-secret, environment-independent settings: semantic
  layer definitions, retrieval parameters (chunk sizes, top-k, rerank), model
  names, pipeline schedules. Checked into the repo.
- **`.env`** (gitignored) — secrets and per-deployment values. A committed
  **`.env.example`** documents every variable. No secrets in the repo or the
  vector store (see [`08-security.md`](08-security.md) §6).
Ports are not hardcoded in any service: `WEB_PORT` and `API_PORT` set what the
host publishes, and `NEXT_PUBLIC_API_URL` tells the frontend where to find the
API (including the `/api/v1` prefix — the value must carry that suffix, and it
does in both `.env.example` and the compose build args).

### 2.1 Reading `.env`: use `make up`, or run compose from the root

Compose derives its **project directory** from the first `-f` file and loads
`.env` from *that* directory. So:

```bash
make up                                       # correct — passes --env-file .env
docker compose up --build                     # correct — root compose.yaml
docker compose -f docker/compose.yml up       # WRONG — reads no .env at all
```

The wrong form does not error. It silently falls back to every in-file default,
so a deployment configured for `LLM_PROVIDER=openai` comes up on ollama with no
message. Two things make it hard to hit: the `Makefile` always passes
`--env-file .env`, and the repo root has a `compose.yaml` that `include:`s
`docker/compose.yml` (pinning the same project name, `insightgpt`, so both
routes manage the same containers and volumes) — which makes the repo root the
project directory and the root `.env` the one that gets read.

### 2.2 Key environment variables (`.env.example`)

```dotenv
# --- Ports (host-published; override to avoid conflicts) ---
WEB_PORT=3000
API_PORT=8000

# --- Database ---
POSTGRES_DSN=postgresql://insight:insight@postgres:5432/insight
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=insight
POSTGRES_USER=insight
POSTGRES_PASSWORD=insight

# --- Auth ---
JWT_SECRET=                        # openssl rand -hex 32

# --- Vector DB ---
RETRIEVER=qdrant
QDRANT_URL=http://qdrant:6333

# --- Local models (Ollama) ---
OLLAMA_HOST=http://ollama:11434
EMBED_MODEL=nomic-embed-text
RERANK_MODEL=dengcao/Qwen3-Reranker-0.6B:F16

# --- LLM provider (chat/reasoning step only) ---
LLM_PROVIDER=ollama                # ollama (default) | openai | groq | gemini*
LLM_MODEL=llama3.1:8b
# OPENAI_API_KEY=                  # only if LLM_PROVIDER=openai
# GROQ_API_KEY=                    # only if LLM_PROVIDER=groq

# --- Pipeline ---
REINDEX_SOURCE=ingested            # ingested | samples | <path>

# --- Frontend ---
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

`.env.example` is the authoritative list; the block above is a summary.

**One database role.** The stack provisions a single `insight` role that owns
`raw`, `marts`, and `insight` and is used by the API, ingestion, and dbt alike —
that is what `docker/initdb/01-schemas.sql`, `docker/compose.yml`, and
`services/warehouse/profiles.yml` all agree on. Splitting into a read-only app
role and a writing ETL role is the right posture for a real deployment and is
described as such in [`08-security.md`](08-security.md) §3, but it is **not**
what this compose stack creates today. The SQL-safety guarantees the system
actually relies on are enforced above the database: the engine never authors
SQL, the query builder compiles only from the governed semantic layer, and the
table allow-list rejects anything outside `marts`.

**Cloud keys** reach the `api` container only if they are in the root `.env`
*and* the stack was started a way that reads it (§2.1). Embeddings and reranking
always use local Ollama models regardless of `LLM_PROVIDER`; only the
chat/reasoning step follows it. `gemini` is declared but not implemented — the
provider factory raises on it rather than pretending.

## 3. One-command bring-up & first-run bootstrap

```bash
make env                  # copies .env.example -> .env (edit secrets)
make up                   # builds and starts the stack
make bootstrap            # first run only: models, warehouse, index
```

Bootstrap is a **separate, explicit command**, not something the worker
entrypoint does on boot. That is deliberate: it pulls gigabytes of models and
rebuilds the warehouse, which is not something a container restart should
silently trigger. It is **idempotent** — every step checks state before acting —
so re-running after a partial failure is the normal recovery path.

The database schemas are not bootstrap's job either: `docker/initdb/01-schemas.sql`
runs once when Postgres initializes an empty volume and creates `raw`, `marts`,
`insight`, and `insight.pipeline_runs`.

```mermaid
graph TB
    START["make bootstrap<br/>(compose run --rm worker)"] --> WAIT["1 · wait for postgres / qdrant / ollama"]
    WAIT --> MODELS["2 · pull Ollama models<br/>embed + rerank always, chat only if LLM_PROVIDER=ollama"]
    MODELS --> SEED["3 · scripts/seed.py --require-postgres<br/>generate → load raw + publish corpus → dbt seed/run/test"]
    SEED --> COLL["4 · create the Qdrant 'documents' collection"]
    COLL --> INDEX["5 · index the published corpus (changed-only)"]
```

The five steps, and what each one really does:

1. **Wait for postgres / qdrant / ollama.** Postgres via a real `SELECT 1`,
   the other two via HTTP polling, each with a 120 s deadline and an actionable
   message on timeout.
2. **Pull Ollama models.** The embedding and reranker models always — retrieval
   has no cloud embedding path. The **chat model only when
   `LLM_PROVIDER=ollama`**, since pulling several gigabytes for a model an
   openai/groq deployment will never call is pure waste. Already-present models
   are skipped. A pull is retried up to three times: long pulls over a flaky
   registry connection die with an incomplete chunked read, and Ollama keeps the
   blobs it already fetched, so a retry resumes rather than restarts. The stream
   is parsed rather than drained — Ollama reports a mid-pull failure as an
   `error` event on a `200` response — and the model must then actually appear
   in `/api/tags` before the step reports success.
3. **Build the warehouse.** Delegates to `scripts/seed.py`, passing
   `--require-postgres` so that a database this step already proved reachable
   cannot be quietly skipped. Seed generates the dataset, loads `raw`, publishes
   the redacted document corpus, then runs dbt `seed` + `run` + `test`
   ([`10-testing-eval.md`](10-testing-eval.md) §3).
4. **Create the Qdrant `documents` collection** — skipped if it exists. An
   unreachable Qdrant fails the step instead of being mistaken for a first run.
5. **Index the corpus step 3 published.** Not the built-in demo documents: the
   real ones. There is no "skip if the collection is non-empty" shortcut here
   because indexing is changed-only — a re-run over an unchanged corpus
   re-embeds nothing anyway, whereas skipping on "has points" would ignore
   documents that *did* change.

There is no `bootstrap_complete` marker; each step's own state check is what
makes a re-run cheap.

## 4. Local development workflow

Docker Compose is the integration path; day-to-day development runs services
individually against the containerized data stores.

- **Python (api, worker, ingestion, retrieval)** — each is its own **uv**
  project with its own `pyproject.toml` and virtualenv, so a change to one
  cannot drag another's dependencies along:

  ```bash
  cd services/api        && uv run uvicorn app.api.main:app --reload
  cd services/worker     && uv run python -m worker          # scheduler loop
  cd services/worker     && uv run python -m worker run reindex_docs   # one job
  cd services/retrieval  && uv run insight-retrieval index   # changed-only
  make test                        # every suite, in order
  make lint                        # ruff over every package
  ```

- **Web (Next.js)** — **pnpm** (npm works too):

  ```bash
  pnpm install
  pnpm dev                         # http://localhost:3000
  ```

- **Data stores only** — run just the backing services while developing app code
  on the host:

  ```bash
  docker compose up postgres qdrant ollama
  ```

  The app reads `POSTGRES_DSN` / `POSTGRES_HOST`, `QDRANT_URL`, and
  `OLLAMA_HOST` from `.env`, so pointing a host-run API at container stores is a
  config change, not a code change. (Only `postgres` publishes a host port, on
  loopback; uncomment the `qdrant` ports block in `docker/compose.yml` if you
  need to reach it from the host too.)

- **dbt** — the project and the profile live in the same directory, and the
  profile reads `POSTGRES_*` from the environment (no secrets on disk):

  ```bash
  dbt build --project-dir services/warehouse --profiles-dir services/warehouse
  ```

  Or, end to end and tracked as a pipeline run:

  ```bash
  python -m worker run dbt_build
  ```

## 5. Cloud portability

The architecture is deliberately **cloud-portable** rather than cloud-specific.
This is a documented path, not a full IaC deliverable. Two shapes are supported.

### 5.1 Single VM (lift-and-shift)

The simplest cloud deployment is the same `docker compose up` on a VM:

- Provision a VM (e.g. 4 vCPU / 8–16 GB RAM), install Docker + Compose, clone the
  repo, set `.env`, run `docker compose up -d`.
- Put a reverse proxy (Caddy/Nginx) in front of `web` and `api` for TLS.
- **GPU note:** without a GPU, the local Ollama reasoning model will be slow.
  Either keep small local models for embeddings/rerank and switch
  `LLM_PROVIDER` to a cloud key for the heavy reasoning step, or size the VM
  accordingly.

### 5.2 PaaS (Render / Railway / Fly)

For a managed platform, decompose the compose file into managed pieces:

| Compose service | On PaaS |
|---|---|
| **postgres** | **Managed Postgres** (the provider's add-on). Point `POSTGRES_HOST` at it; run bootstrap/dbt as a one-off job. |
| **qdrant** | Qdrant as a container service **or** Qdrant Cloud. Point `QDRANT_URL` at it and add an API key (now that it is network-exposed — see [`08-security.md`](08-security.md) §7; the retrieval client does not read one today, so this is a change to make, not a setting to flip). |
| **ollama** | Usually **dropped** on GPU-less PaaS hosts. Set `LLM_PROVIDER` to a cloud key for reasoning; use a hosted/cloud embedding path or a small CPU embedding service for embeddings. |
| **api** | Stateless web service; scale horizontally (Section 7). |
| **worker** | Single background/worker instance (scheduler must be a singleton — Section 7). |
| **web** | Static/Node web service; point it at the public `api` URL. |

**What changes vs. local:** managed Postgres instead of the `postgres`
container; external Qdrant (with an API key) instead of loopback Qdrant; and —
because GPU-less hosts cannot run large local models well — a **cloud LLM key**
for the reasoning step. Everything reachable via config; no code changes.

**Resource notes:** api/worker are modest (CPU-bound on request handling and
embedding I/O). Postgres sizing tracks warehouse size. Qdrant memory tracks
vector count. The heavy compute is the reasoning LLM, which is why offloading it
to a cloud key is the standard PaaS adaptation.

## 6. Backup & restore

Two stores hold state; back up both, and treat backups as sensitive (see
[`08-security.md`](08-security.md) §9).

- **Warehouse (Postgres) — authoritative:**

  ```bash
  # backup
  docker compose exec postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > backup.dump
  # restore
  docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean < backup.dump
  ```

- **Vector data (Qdrant) — rebuildable:** use Qdrant **snapshots**
  (`POST /collections/{name}/snapshots`) for a fast restore, or simply
  **re-index** from `data/ingested/documents.json` (`make reindex`), which is
  literally the source of truth for the vectors. After a restore from elsewhere,
  delete the `.index_state.json` beside the corpus or run
  `insight-retrieval index --full` — the state file describes a collection that
  no longer exists, and changed-only would otherwise skip everything.

- **Config/secrets:** `.env` and `config/*.yaml` are backed up out-of-band by
  the operator (a secret store), never committed.

A restore drill is part of "done" for the deployment milestone: restore into a
clean environment and confirm the demo questions still answer correctly.

## 7. Scaling notes

- **api — horizontal.** The API is **stateless** (JWT auth, no server-side
  session). Run multiple replicas behind a load balancer; SSE streams are
  per-request and do not require sticky routing beyond the life of one stream.
- **worker — singleton.** The APScheduler worker **must run as a single
  instance**; two schedulers would double-fire pipeline jobs. Scale the *work*
  by making individual jobs heavier/parallel internally, not by adding scheduler
  replicas. (If HA is ever required, add a leader-lock — out of scope here.)
- **postgres / qdrant — vertical.** Scale the data stores up (CPU/RAM/disk)
  rather than out for this single-organization scope. Postgres read replicas and
  Qdrant sharding are possible later but unnecessary for the demo domain.
- **ollama / reasoning LLM — the real bottleneck.** On CPU it is the slow step;
  the pluggable provider means quality/throughput can be raised by pointing at a
  cloud key without touching application code.
