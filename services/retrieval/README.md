# InsightGPT — Retrieval / RAG service

Hybrid dense + sparse retrieval over business documents (support tickets, product
reviews, reports, emails), with server-side RRF fusion, cross-encoder reranking,
and per-source diversity. This is the unstructured half of InsightGPT; it is the
authoritative implementation of [`docs/04-retrieval-rag.md`](../../docs/04-retrieval-rag.md)
and reuses the retrieval-core patterns from `rememory`.

## What it does

```
document  ──redact──▶ chunk ──▶ embed (dense) + tokenize (sparse) ──▶ Qdrant `documents`
                                                                          │
question ─embed(query)+tokenize─▶ dense & sparse prefetch (4×k, filtered) ─▶ RRF fusion
                                        │                                     │
                                        └──▶ cross-encoder rerank ──▶ per-doc diversity ──▶ cited results
```

- **One `documents` collection.** Document type is the `source_type` payload
  field, not a separate collection — a single query can span types or filter to
  one. Structured facts (orders, revenue) are deliberately **not** here; they
  live in Postgres and are answered by governed SQL.
- **Redaction first.** Secrets and PII (cards via Luhn, emails, phones, tokens,
  private-key blocks) are removed before chunking, embedding, or storage, so a
  credential can never be retrieved into an LLM prompt.
- **Two chunkers.** Whole-record for tickets/reviews (one thought = one chunk);
  heading/section-aware for reports/emails, with a breadcrumb header
  (`Title :: Section › Sub` + date · type · role) prepended to the embedded and
  sparse text **only** — never to the stored content quoted back to the user.
- **Deterministic ids + delete-then-write.** Point id is `uuid5(doc_id:chunk_index)`,
  so re-indexing overwrites a document's own chunks; deleting first means a
  document that shrank leaves no stale tail chunks.
- **Graceful degradation.** Empty-vocabulary (all-stopword) queries fall back to
  dense-only; reranking is an enhancement that returns the RRF order on any
  failure and disables itself for the process on a hard 404.

## Interface compatibility with the engine

`QdrantRetriever.search(query, *, filters, k) -> list[RetrievedDoc]` matches the
shape the insight engine consumes in
[`services/api/app/engine/retrieval.py`](../api/app/engine/retrieval.py). Its
`RetrievedDoc` (in `retrieval/models.py`) carries the exact fields the engine's
own `RetrievedDoc` declares — `doc_id, source_type, title, body, date, score,
metadata` — so the engine can swap `FixtureRetriever → QdrantRetriever` by
configuration with no engine code change.

`filters` is a plain dict: scalars match exactly, lists match any element, and
`date_range: {start, end}` becomes a `created_at` range applied inside both
prefetch branches (so recency is a hard bound before ranking).

## Requirements to run live

- **Qdrant** (vector store) — `docker run -p 6333:6333 qdrant/qdrant`
- **Ollama** (embeddings + reranker) — <https://ollama.com>, then pull the
  models named in `config/retrieval.yaml`:
  ```
  ollama pull nomic-embed-text
  ollama pull dengcao/Qwen3-Reranker-0.6B:F16
  ```

Endpoints default to `http://127.0.0.1:6333` (Qdrant) and
`http://127.0.0.1:11434` (Ollama); override with `QDRANT_URL` / `OLLAMA_HOST`
env vars for containerized deployment. Model names and dimensions stay pinned to
the config file — they must match what is stored in Qdrant.

## Setup

```bash
cd services/retrieval
uv venv --python 3.12
uv pip install -e .            # runtime deps
uv pip install pytest ruff     # dev tooling
```

## CLI

```bash
insight-retrieval setup                         # create the `documents` collection
insight-retrieval index                         # index the built-in sample corpus
insight-retrieval index path/to/docs/           # index a folder of JSON files
insight-retrieval index docs.json               # index one JSON file (object or list)
insight-retrieval search "why are North electronics deliveries late?"
insight-retrieval eval                          # golden-set scoreboard (RRF vs RRF+rerank)
insight-retrieval status                         # collection point count
```

(Also runnable as `python -m retrieval.cli ...`.) Input JSON matches
`services/api/app/fixtures/retail.py::get_sample_documents`; each object needs
`doc_id`, `source_type`, `title`, `body`, and optionally `date`/`created_at`,
`region`, `category`, `product_ref`, `order_ref`, `author_role`, `channel`.

## Evaluation

`insight-retrieval eval` scores a golden `question → expected-doc(s)` set against
the live index, reporting **Recall@1 / Recall@3 / MRR** with reranking off vs.
on (rerank lift), and fails if reranked Recall@3 falls below the floor. Needs
live Qdrant + Ollama. See `retrieval/eval.py`.

## Tests

```bash
pytest -q
```

Offline unit tests (no Qdrant/Ollama needed) cover the pure components:
sparse tokenization, RRF fusion math, chunking + breadcrumb split, redaction,
deterministic id generation, metadata-filter construction, and the
engine-facing `RetrievedDoc` mapping + per-source diversity. The live
end-to-end test (`tests/test_integration.py`) is **skipped** unless
`RETRIEVAL_LIVE=1` is set and both services are reachable.

## Layout

```
services/retrieval/
  config/retrieval.yaml    # models, dimensions, prefixes, chunking, rerank — single source of truth
  retrieval/
    config.py              # pydantic config loader (+ env host overrides)
    models.py              # RetrievedDoc (engine shape), Document, Chunk
    ids.py                 # deterministic uuid5 point ids
    redact.py              # secret + PII redaction (before anything else)
    sparse.py              # CRC32 term-frequency sparse vectors
    chunking.py            # whole-record + heading-aware chunkers, breadcrumb header
    fusion.py              # pure RRF (mirrors Qdrant's server-side fusion; unit-tested)
    embedder.py            # batched Ollama embeddings (asymmetric query/doc prefixes)
    store.py               # Qdrant collection + delete-then-write
    rerank.py              # cross-encoder rerank via Ollama logprobs
    search.py              # hybrid prefetch + server-side RRF + filters + diversity
    retriever.py           # QdrantRetriever — the engine-facing entry point
    indexer.py             # redact → chunk → embed → store orchestration
    sample_docs.py         # small runnable corpus (planted North-electronics story)
    eval.py                # Recall@1/@3 + MRR golden-set harness
    cli.py                 # setup / index / search / eval / status
  tests/                   # offline unit tests + gated live integration test
```

## Assumptions to confirm

- **Embedding model.** `docs/04` names `qwen3-embedding:0.6b` (1024-d) as
  rememory's reference but specifies the `search_query:` / `search_document:`
  prefixes. This config ships `nomic-embed-text` (768-d), which is the model
  those prefixes belong to and a lighter CPU default. Switch the model +
  dimensions + prefixes together in `config/retrieval.yaml` (and re-index) if
  you prefer the qwen embedder.
- **Reranker model.** `dengcao/Qwen3-Reranker-0.6B:F16`, scored via single-token
  logprobs (Ollama has no rerank endpoint). Reranking is optional — the pipeline
  runs on RRF order if it is disabled or unavailable.
- **Payload field names** follow `docs/04` §1.1 (`created_at`, `source_type`,
  `region`, `category`, `product_ref`, `order_ref`, `author_role`, `channel`).
  Both the sample fixture in `services/api` and the sample corpus here use the
  normalized `author_role` enum (`customer`/`agent`/`manager`);
  `Document.from_dict` maps `date → created_at`. Confirm any future ingestion
  layer normalizes incoming roles to the same enum.
```
