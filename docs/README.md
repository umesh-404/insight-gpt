# InsightGPT — Documentation

InsightGPT is an end-to-end, AI-powered business-intelligence platform that
turns raw multi-source data (databases, spreadsheets, and documents such as
tickets, reviews, and reports) into **explainable, cited answers** to
natural-language questions. It lands raw data, transforms it with dbt into a
modeled PostgreSQL warehouse with a governed **semantic layer**, indexes
business documents in Qdrant for semantic retrieval, and answers questions by
combining **semantic-layer-grounded text-to-SQL** with **retrieval-augmented
generation** — every answer showing the SQL that produced a number and the
documents behind a claim. The whole stack is containerized (Docker Compose),
runs local-first on a modest laptop with a pluggable path to cloud reasoning
models, and is exposed through a FastAPI backend and a Next.js frontend.

> **This `docs/` folder is the source of truth for the project.** Design
> decisions live here first; implementation follows the approved plan in
> [`11-roadmap.md`](11-roadmap.md). Where code and docs disagree, treat it as a
> bug in one of them and reconcile against these documents.

## Table of contents

| # | Document | What it covers |
|---|---|---|
| 00 | [`00-overview.md`](00-overview.md) | The problem, what InsightGPT does, goals, non-goals, personas, and success criteria |
| 01 | [`01-architecture.md`](01-architecture.md) | System design, component map, request flow, tech-stack rationale, deployment topology, repo layout |
| 02 | [`02-data-model.md`](02-data-model.md) | Demo domain, source schemas, warehouse layering, star schema, dbt project, and the semantic metrics layer |
| 03 | [`03-ingestion-etl.md`](03-ingestion-etl.md) | Source connectors, redaction, raw load, dbt transforms, and scheduled pipeline runs |
| 04 | [`04-retrieval-rag.md`](04-retrieval-rag.md) | Document chunking, local embeddings, Qdrant hybrid search, reranking, and retrieval evaluation |
| 05 | [`05-insight-engine.md`](05-insight-engine.md) | NL router, grounded text-to-SQL, SQL guardrails, answer synthesis, and the pluggable LLM provider |
| 06 | [`06-api.md`](06-api.md) | FastAPI endpoints, JWT auth and roles, SSE streaming, pipeline-run tracking, and observability |
| 07 | [`07-frontend.md`](07-frontend.md) | Next.js app: chat/ask, dashboards, source admin, pipeline monitor, and executive reports |
| 08 | [`08-security.md`](08-security.md) | Redaction, read-only analytics role, allow-lists, auth, and prompt-injection defenses |
| 09 | [`09-deployment.md`](09-deployment.md) | Docker Compose bring-up, configuration, and the cloud-portability path |
| 10 | [`10-testing-eval.md`](10-testing-eval.md) | dbt tests, pytest, and the retrieval + text-to-SQL evaluation harnesses |
| 11 | [`11-roadmap.md`](11-roadmap.md) | Phased build plan, vertical-slice definition, risks, and current project state |
| 12 | [`12-mcp.md`](12-mcp.md) | MCP server: governed metrics over the Model Context Protocol, its tool inventory, safety posture, and client setup |
| 13 | [`13-forecasting.md`](13-forecasting.md) | Forecasting governed metrics: method, prediction intervals, when it refuses, the API, and the optional-dependency posture |
| 14 | [`14-demo.md`](14-demo.md) | Five-minute demo walkthrough: what to show, in what order, what to say, and the terminal-only equivalent |

Architecture Decision Records, when written, live under [`adr/`](adr/).

## Start here — reading paths

Read [`00-overview.md`](00-overview.md) and [`01-architecture.md`](01-architecture.md)
first regardless of role; they frame everything else. Then follow the path for
your role:

- **Evaluator / reviewer** (assessing the project) —
  [`00-overview.md`](00-overview.md) →
  [`01-architecture.md`](01-architecture.md) →
  [`05-insight-engine.md`](05-insight-engine.md) (the core thesis: grounded
  answers) →
  [`10-testing-eval.md`](10-testing-eval.md) (quality is measured, not
  asserted) →
  [`11-roadmap.md`](11-roadmap.md) (plan and current state).

- **Developer** (building or extending a service) —
  [`01-architecture.md`](01-architecture.md) →
  [`11-roadmap.md`](11-roadmap.md) (build sequence) →
  [`06-api.md`](06-api.md) →
  [`05-insight-engine.md`](05-insight-engine.md) →
  [`07-frontend.md`](07-frontend.md), then the doc for whichever service you
  touch.

- **Data engineer** (warehouse, pipelines, retrieval) —
  [`02-data-model.md`](02-data-model.md) →
  [`03-ingestion-etl.md`](03-ingestion-etl.md) →
  [`04-retrieval-rag.md`](04-retrieval-rag.md) →
  [`10-testing-eval.md`](10-testing-eval.md) (dbt tests + eval harnesses).

## At a glance — tech stack

| Layer | Choice |
|---|---|
| Warehouse | PostgreSQL |
| Transformations | dbt (dbt-postgres) — star schema + semantic metrics |
| Vector DB | Qdrant (dense + sparse hybrid search) |
| Embeddings + rerank | Ollama (local, CPU-viable) |
| LLM reasoning | Pluggable provider — local default, cloud (OpenAI / Gemini / Groq) optional |
| NL → data | Semantic-layer-grounded text-to-SQL |
| Orchestration | APScheduler worker with pipeline-run tracking |
| API | FastAPI — REST + SSE, JWT auth with roles |
| Frontend | Next.js + TypeScript + Tailwind + shadcn/ui |
| Packaging | Docker Compose |
| Demo domain | Retail / e-commerce |

## Current state

Documentation is being written first and is the approved basis for the build.
Implementation is **pending maintainer approval** of the plan in
[`11-roadmap.md`](11-roadmap.md). See that document for milestones and the
vertical-slice definition.
