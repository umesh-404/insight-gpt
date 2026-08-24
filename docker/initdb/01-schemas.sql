-- InsightGPT — first-boot database bootstrap.
--
-- The official postgres image runs every *.sql in /docker-entrypoint-initdb.d
-- exactly once, on an empty data directory, against POSTGRES_DB as POSTGRES_USER.
-- This file is idempotent anyway (IF NOT EXISTS everywhere) so re-running it by
-- hand is harmless.
--
-- Creates the three schemas the stack expects and the pipeline-run tracking
-- table the worker/APScheduler writes to. See docs/02-data-model.md and
-- docs/09-deployment.md §3.

CREATE SCHEMA IF NOT EXISTS raw;      -- landing zone written by services/ingestion
CREATE SCHEMA IF NOT EXISTS marts;    -- modeled star schema + metrics built by dbt
CREATE SCHEMA IF NOT EXISTS insight;  -- operational metadata (pipeline runs, markers)

-- Pipeline-run ledger. One row per ingestion/transform run, surfaced in the UI
-- and used by bootstrap/worker to record and skip completed work.
CREATE TABLE IF NOT EXISTS insight.pipeline_runs (
    id             text PRIMARY KEY,
    pipeline       text NOT NULL,
    status         text NOT NULL,
    started_at     timestamptz,
    finished_at    timestamptz,
    rows_processed integer,
    error          text,
    triggered_by   text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Helpful for "most recent run per pipeline" lookups.
CREATE INDEX IF NOT EXISTS pipeline_runs_pipeline_started_idx
    ON insight.pipeline_runs (pipeline, started_at DESC);
