# 00 — Project Overview

## 1. The problem

Organizations generate large volumes of structured data (databases,
spreadsheets, transactional systems) and unstructured data (support tickets,
reviews, emails, reports, PDFs). Decision-makers spend a disproportionate share
of their time **collecting, cleaning, joining, and interpreting** this data
before they can answer even simple business questions. The insight is buried;
the labor is manual; the answers arrive late and are hard to trust.

## 2. What InsightGPT does

InsightGPT is an end-to-end, AI-powered business-intelligence platform that
turns raw multi-source data into **explainable, cited answers** to
natural-language questions.

It:

1. **Ingests** data from multiple sources (CSV/Excel, SQL databases, and
   documents such as tickets, reviews, and reports).
2. Runs a scalable **ELT pipeline** — lands raw data, then transforms it with
   dbt into a clean, modeled warehouse (star schema) with a governed
   **semantic layer** of business metrics.
3. **Indexes** business documents in a **vector database** for semantic
   retrieval, with secrets/PII redacted at ingestion.
4. Uses a **Retrieval-Augmented Generation (RAG)** architecture plus
   **semantic-layer-grounded text-to-SQL** so users can ask questions in plain
   English and get answers that combine hard numbers with document context.
5. Presents results as **interactive dashboards, a conversational analytics
   interface, and exportable executive reports**.

The whole stack is **containerized** and **cloud-portable**, exposing a
**FastAPI** backend and a **Next.js** frontend — a production-shaped Data
Engineering + AI pipeline.

## 3. Example questions it answers

- *"Why did sales decline last quarter?"* → runs governed SQL over the sales
  facts, decomposes the change by region/category/product, and cross-references
  support-ticket and review themes for the same period; returns a narrative
  with the SQL used and the documents cited.
- *"Which products should we restock?"* → joins inventory levels, sell-through
  rate, and lead time; ranks at-risk SKUs.
- *"Summarize customer complaints this month."* → retrieves and clusters recent
  tickets/reviews, summarizes the top themes with representative quotes.

## 4. Goals

- **Trustworthy answers.** Every insight is explainable: the SQL that produced
  a number and the documents behind a claim are shown. Reliability comes from
  grounding text-to-SQL on a governed semantic layer rather than free-form SQL.
- **Multi-source.** Structured + unstructured data answered together.
- **Production-shaped engineering.** Config-driven, tested, containerized,
  observable, documented — not a notebook demo.
- **Runs on modest hardware.** Local-first defaults (embeddings/rerank on
  Ollama) with a pluggable path to cloud LLMs for the heavy reasoning step.
- **Private by default.** Secrets and PII are redacted at ingestion; analytics
  execute under SELECT-only validation with a table allow-list, an enforced row
  limit and a statement timeout. (A separate read-only Postgres role is designed
  in [`08-security.md`](08-security.md) as a defence-in-depth layer and is not
  part of the current deployment — see the status table in the repository
  README.)

## 5. Non-goals (for the project scope)

- Not a general-purpose data platform competing with Snowflake/Databricks; it
  targets a **single well-modeled demo domain (retail/e-commerce)** end to end.
- Not real-time streaming analytics; batch/scheduled ELT is sufficient.
- Not a fine-tuning project; it uses off-the-shelf models via a provider
  abstraction.
- Not multi-tenant SaaS; single-organization deployment with role-based access.

## 6. Personas

| Persona | Needs | Uses |
|---|---|---|
| **Executive** | Fast, trustworthy answers and summaries; exportable reports | Chat, dashboards, executive reports |
| **Business analyst** | Explore metrics, drill down, verify the SQL | Dashboards, chat with SQL/citation reveal |
| **Data/platform engineer** | Configure sources, monitor pipelines, manage models | Data-source admin, pipeline monitor, config |
| **Support/ops manager** | Understand complaint themes and product issues | Chat over documents, review/ticket summaries |

## 7. Success criteria

- A user can ask the three example questions and receive correct, cited answers
  backed by real data in the demo warehouse and document index.
- The ELT pipeline can be run/scheduled and its runs are observable.
- Retrieval and text-to-SQL quality are **measured** by an eval harness, not
  asserted.
- The full system comes up with a single `docker compose up`.
- The UI is polished enough to demo to a non-technical audience.

## 8. Glossary

- **ELT** — Extract, Load, Transform: land raw data first, transform in-warehouse
  (with dbt). Preferred over ETL because warehouse compute is cheap and
  transformations stay versioned and testable.
- **Semantic layer** — a governed definition of business **metrics** and
  **dimensions** (e.g. `revenue`, `orders`, `by_region`) that the LLM maps
  questions onto, so it can't invent bad joins or aggregations.
- **RAG** — Retrieval-Augmented Generation: retrieve relevant documents, then
  have the LLM answer grounded in them (with citations).
- **Hybrid search** — dense (embedding) + sparse (keyword) retrieval fused
  together; more robust than either alone.
- **Reranking** — a cross-encoder re-scores the top candidates for precision
  before they reach the LLM.
- **Star schema** — a warehouse modeling pattern: central **fact** tables
  (events/measures) surrounded by **dimension** tables (context).

## 9. Where to go next

- System design & tech-stack rationale → [`01-architecture.md`](01-architecture.md)
- Data model & warehouse → [`02-data-model.md`](02-data-model.md)
- Ingestion & ELT → [`03-ingestion-etl.md`](03-ingestion-etl.md)
- Retrieval / RAG → [`04-retrieval-rag.md`](04-retrieval-rag.md)
- The insight engine → [`05-insight-engine.md`](05-insight-engine.md)
- Build plan & milestones → [`11-roadmap.md`](11-roadmap.md)
- Seeing it run, in five minutes → [`14-demo.md`](14-demo.md)
