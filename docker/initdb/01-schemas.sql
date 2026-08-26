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

-- ---------------------------------------------------------------------------
-- Read-only analytics role.
--
-- Defence in depth for the text-to-SQL path: the API queries the warehouse as
-- a role that has no privilege to write anything, so even a total failure of
-- the SELECT-only parser, the table allow-list and the query builder cannot
-- mutate data. The ingestion/dbt path keeps the owner role, because it must
-- create and replace tables.
--
-- The password is intentionally the same as the owner's in this local demo
-- stack (both come from POSTGRES_PASSWORD); a real deployment gives this role
-- its own secret. What matters here is the PRIVILEGE separation, not secrecy.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insight_app') THEN
        EXECUTE format(
            'CREATE ROLE insight_app LOGIN PASSWORD %L',
            coalesce(current_setting('custom.app_password', true), 'insight')
        );
    END IF;
END
$$;

-- Connect + read the modeled marts and the operational ledger. Deliberately NO
-- access to `raw`: the API has no business reading unmodeled, pre-redaction
-- landing tables, and the allow-list already forbids naming them.
GRANT CONNECT ON DATABASE insight TO insight_app;
GRANT USAGE ON SCHEMA marts, insight TO insight_app;
GRANT SELECT ON ALL TABLES IN SCHEMA marts, insight TO insight_app;

-- The marts do not exist yet at first boot (dbt builds them later), so the
-- grant above cannot cover them. This makes every future table readable by the
-- app role automatically, whoever creates it.
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO insight_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA insight GRANT SELECT ON TABLES TO insight_app;

-- The one write the app role legitimately needs: triggering a pipeline from the
-- UI enqueues a `queued` row that the worker then picks up and advances. Scoped
-- to this single ledger table -- the warehouse itself stays read-only.
GRANT INSERT, UPDATE ON insight.pipeline_runs TO insight_app;
