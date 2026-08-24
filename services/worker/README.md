# Worker (APScheduler orchestrator)

The lightweight scheduler that runs InsightGPT's ELT / reindex pipelines on a
cadence and records every execution in Postgres `insight.pipeline_runs`.
Implements the orchestration section of
[`docs/03-ingestion-etl.md`](../../docs/03-ingestion-etl.md) §5 and the worker box
of [`docs/01-architecture.md`](../../docs/01-architecture.md).

The worker owns **no business logic** of its own. Each job lazily calls into a
sibling service — [`services/ingestion`](../ingestion),
[`services/retrieval`](../retrieval), or `dbt` over
[`services/warehouse`](../warehouse) — and wraps the call in a tracked run so
status, timings, and row counts are captured even on failure. It is designed to
**run offline**: with no `POSTGRES_DSN` the run store keeps records in memory,
and a job whose dependency is missing fails cleanly instead of crashing the loop.

## Jobs

| Job | Does | Schedule (default) |
|---|---|---|
| `full_ingest` | `services.ingestion` full reload of `raw.*` + republish the document corpus | on-demand |
| `incremental_sync` | `services.ingestion` content-hash sync + republish the document corpus | every 30 min |
| `reindex_docs` | retrieval indexer: re-chunk / re-embed the **changed** documents from that corpus | every 30 min (offset +15) |
| `dbt_build` | `dbt build` over the warehouse project (subprocess) | daily @ 02:00 |

The two 30-minute interval jobs are offset so they do not fire in the same tick
and contend for the box. Each job runs with `max_instances=1` and
`coalesce=True` — the single-instance execution lock docs/03 §5.2 calls for.

### The ingest → reindex chain

`full_ingest` / `incremental_sync` publish every redacted document to
`data/ingested/documents.json`, and `reindex_docs` reads exactly that file. It
re-embeds only documents whose content hash changed and deletes the chunks of
documents that vanished from the corpus, so the half-hourly reindex over an
unchanged corpus costs nothing.

If that corpus does not exist, `reindex_docs` **fails with a message naming the
command that creates it** rather than falling back to the retrieval package's
six built-in demo documents. A reindex that reports success with a healthy chunk
count while the real corpus was never touched is the exact failure this wiring
exists to prevent. The demo set is still reachable, on purpose:
`REINDEX_SOURCE=samples`.

## Run

```bash
# Own venv (Python 3.12)
uv venv --python 3.12 .venv
uv pip install -e .            # + pytest ruff for dev

# Start the scheduler loop + health server (blocks; Ctrl-C / SIGTERM to stop)
python -m worker

# Run a single job once and exit (exit 1 if the run failed)
python -m worker run full_ingest
python -m worker run incremental_sync
python -m worker run dbt_build
python -m worker run reindex_docs
```

`python -m worker` also starts a stdlib health endpoint on port 8090 for compose
to probe:

```bash
curl http://localhost:8090/health      # -> {"status": "ok"}
```

## Configuration (env-driven, no secrets committed)

`worker/config.py` is a `pydantic-settings` model. Key variables:

| Env var | Default | Meaning |
|---|---|---|
| `POSTGRES_DSN` | *(unset)* | Run-tracking DSN; unset ⇒ in-memory store (offline) |
| `QDRANT_URL` | `http://qdrant:6333` | Vector store, passed to retrieval |
| `OLLAMA_HOST` | `http://ollama:11434` | Embeddings host, passed to retrieval |
| `EMBED_MODEL` | `nomic-embed-text` | Embedding model name (768-d) |
| `REINDEX_SOURCE` | `ingested` | `ingested` (the published corpus) / `samples` / an explicit path |
| `DOCUMENT_CORPUS_PATH` | `data/ingested/documents.json` | The ingestion hand-off file |
| `REINDEX_CHANGED_ONLY` | `true` | `false` re-embeds the whole corpus every run |
| `HEALTH_PORT` | `8090` | Health endpoint port |
| `INCREMENTAL_SYNC_MINUTES` | `30` | `incremental_sync` cadence |
| `REINDEX_DOCS_MINUTES` | `30` | `reindex_docs` cadence |
| `REINDEX_DOCS_OFFSET_MINUTES` | `15` | first-fire offset for `reindex_docs` |
| `DBT_BUILD_HOUR` / `DBT_BUILD_MINUTE` | `2` / `0` | daily `dbt_build` clock |
| `DBT_PROJECT_DIR` / `DBT_PROFILES_DIR` | `services/warehouse` | dbt invocation |
| `RUNS_SCHEMA` | `insight` | schema holding `pipeline_runs` |

## Run tracking — `insight.pipeline_runs`

Every job opens a run (`running`) and closes it (`success` / `failed`) with a row
count and, on failure, the captured error. `worker/runs.py` creates the table
defensively if bootstrap has not:

```sql
create table if not exists insight.pipeline_runs (
  id text primary key,
  pipeline text not null,
  status text not null,          -- queued | running | success | failed
  started_at timestamptz,
  finished_at timestamptz,
  rows_processed integer,
  error text,
  triggered_by text,
  created_at timestamptz default now()
);
```

With `POSTGRES_DSN` unset (or psycopg unreachable) the store logs a warning and
degrades to an in-memory dict — the scheduler and the tests still run.

## Layout

```
worker/
  config.py      # pydantic-settings: DSN, service URLs, schedules
  runs.py        # PipelineRunStore: start()/finish(), Postgres + memory fallback
  jobs.py        # the four jobs + run_job() wrapper (lazy imports, clear errors)
  scheduler.py   # APScheduler wiring + graceful shutdown
  health.py      # stdlib http.server /health on 8090
  __main__.py    # `python -m worker` and `python -m worker run <job>`
tests/           # offline: run-store lifecycle, job wrapper, scheduler, health
```

## Tests

```bash
python -m pytest -q      # 19 tests, all offline
ruff check .             # line-length 100; E,F,I,UP,B,SIM
```

Tests stub ingestion + retrieval (via `worker.jobs._JOBS` and `import_module`
monkeypatching), so no live Postgres, Qdrant, or Ollama is required.
