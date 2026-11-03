'use client';

import * as React from 'react';
import Link from 'next/link';
import { Clock, Lock, Play, Loader2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { PipelineRunTable } from '@/components/pipeline-run-table';
import { StatusBadge } from '@/components/status-badge';
import { describeError, EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { RelativeTime } from '@/components/relative-time';
import { usePipelines, useRuns, useTriggerRun } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { ApiError, type Pipeline, type PipelineRunSummary } from '@/lib/types';

/** The two non-terminal run states the API treats as "already running". */
function isActive(status: string | undefined): boolean {
  return status === 'running' || status === 'queued';
}

/**
 * `GET /pipelines` and `GET /pipeline-runs` both require the analyst role, so
 * the fetching lives in a child that is only mounted once the signed-in role is
 * known to clear that bar. Calling the hooks at the top of the page meant a
 * viewer fired two analyst-only endpoints on load and collected two 403s before
 * the UI had decided what to show them.
 */
export function PipelinesView() {
  const { hasRole, loading } = useAuth();

  return (
    <div className="space-y-8 p-4 sm:p-6">
      <PageHeader
        title="Pipelines"
        description="Schedules, run history and manual triggers for every ingestion and transform job."
      />

      {loading ? (
        // Deciding access before auth resolves flashes "no access" at a user
        // who has it.
        <div className="space-y-4" aria-busy="true">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : hasRole('analyst') ? (
        <PipelineMonitor canTrigger={hasRole('admin')} />
      ) : (
        <EmptyState
          title="Pipeline monitoring needs the analyst role"
          description="Schedules and run history are readable by analysts and administrators. Ask an administrator to raise your role."
          icon={<Lock className="size-5" aria-hidden />}
        />
      )}
    </div>
  );
}

function PipelineMonitor({ canTrigger }: { canTrigger: boolean }) {
  const { toast } = useToast();
  const pipelines = usePipelines();
  const runs = useRuns();
  const trigger = useTriggerRun();

  // `GET /pipelines` is fetched once; `GET /pipeline-runs` polls every 5s while
  // anything is in flight. Without this overlay a card keeps showing "Running"
  // (and a disabled trigger) after the run history beside it has already moved
  // on to Success. The run list is newest-first, so the first hit per pipeline
  // is that pipeline's latest run.
  const latestRunByPipeline = React.useMemo(() => {
    const out: Record<string, PipelineRunSummary> = {};
    for (const run of runs.data ?? []) {
      if (!(run.pipeline in out)) out[run.pipeline] = run;
    }
    return out;
  }, [runs.data]);

  const polling = (runs.data ?? []).some((run) => isActive(run.status));

  const onRun = (name: string) => {
    trigger.mutate(name, {
      onSuccess: (res) =>
        toast({
          title: `Run queued for ${name}`,
          description: `Run ${res.run_id} is ${res.status}.`,
          variant: 'success',
        }),
      onError: (err) => {
        // 409 (already running) surfaces as a toast linking to the active run.
        if (err instanceof ApiError && err.status === 409) {
          const runId = (err.details?.run_id as string) ?? '';
          toast({
            title: `${name} is already running`,
            description: runId ? (
              <Link href={`/pipelines/runs/${runId}`} className="text-primary underline">
                View the active run
              </Link>
            ) : (
              'A run is already in progress.'
            ),
            variant: 'warning',
          });
        } else {
          toast({
            title: 'Could not trigger the run',
            description: describeError(err),
            variant: 'destructive',
          });
        }
      },
    });
  };

  // Both panels read endpoints that require the analyst role, so a role the
  // server disagrees with gets the same 403 twice, stacked. Say it once — and
  // without a retry, because a 403 is a permanent answer for this token.
  const forbidden =
    pipelines.error instanceof ApiError && pipelines.error.status === 403;
  if (forbidden) return <ErrorState error={pipelines.error} />;

  return (
    <div className="space-y-8">
      <section aria-labelledby="pipelines-heading" className="space-y-3">
        <h2
          id="pipelines-heading"
          className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
        >
          Pipelines
        </h2>

        {pipelines.isLoading ? (
          <div className="grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-40 rounded-none" />
            ))}
          </div>
        ) : pipelines.isError ? (
          <ErrorState error={pipelines.error} onRetry={() => void pipelines.refetch()} />
        ) : pipelines.data && pipelines.data.length ? (
          // One hairline grid rather than four free-floating cards: the gap
          // *is* the 1px divider, so the panel reads as a single instrument
          // at projector distance instead of a scatter of boxes.
          <div className="grid gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-2 xl:grid-cols-3">
            {pipelines.data.map((pipeline) => (
              <PipelineTile
                key={pipeline.name}
                pipeline={pipeline}
                lastRun={latestRunByPipeline[pipeline.name] ?? pipeline.last_run}
                canTrigger={canTrigger}
                triggering={trigger.isPending && trigger.variables === pipeline.name}
                onRun={onRun}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title="No pipelines registered"
            description="This deployment has no pipeline definitions yet."
          />
        )}
      </section>

      <section aria-labelledby="runs-heading" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2
            id="runs-heading"
            className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
          >
            Run history
          </h2>
          {/* Only claim live updates while the poll is actually running — the
              query stops refetching once every run reaches a terminal state. */}
          {polling ? (
            <p className="text-xs text-muted-foreground" aria-live="polite">
              A run is in flight — this list refreshes every 5 seconds.
            </p>
          ) : runs.data?.length ? (
            <p className="text-xs text-muted-foreground">
              {runs.data.length} most recent {runs.data.length === 1 ? 'run' : 'runs'}
            </p>
          ) : null}
        </div>

        {runs.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
        ) : (
          <PipelineRunTable runs={runs.data ?? []} />
        )}
      </section>
    </div>
  );
}

function PipelineTile({
  pipeline,
  lastRun,
  canTrigger,
  triggering,
  onRun,
}: {
  pipeline: Pipeline;
  lastRun: PipelineRunSummary | null | undefined;
  canTrigger: boolean;
  triggering: boolean;
  onRun: (name: string) => void;
}) {
  const running = isActive(lastRun?.status);
  // `queued` is as active as `running` — the backend refuses a second trigger
  // for either (409), so the button must not invite the click. Only this
  // pipeline's own pending mutation disables it; `trigger.isPending` alone
  // greyed out all four buttons at once.
  const disabled = triggering || running;
  const noteId = `${pipeline.name}-trigger-note`;
  const note = triggering
    ? 'Sending the trigger…'
    : running
      ? 'A run is already in progress. Triggering is available again when it finishes.'
      : null;

  return (
    <div className="flex flex-col gap-4 bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <h3 className="min-w-0 break-words text-base font-semibold leading-tight">
          {pipeline.name}
        </h3>
        {lastRun ? <StatusBadge status={lastRun.status} className="shrink-0" /> : null}
      </div>

      <p className="text-sm leading-relaxed text-muted-foreground">
        {pipeline.description}
      </p>

      <dl className="mt-auto space-y-1.5 border-t pt-3 text-xs">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="flex items-center gap-1.5 text-muted-foreground">
            <Clock className="size-3.5 shrink-0" aria-hidden /> Schedule
          </dt>
          {/* A cron expression is a single token. Rendered as a pill it wrapped
              mid-expression — the minute field on one line, the rest on the
              next — which is unreadable from the back of a room. So it sits on
              its own baseline, unboxed, and is allowed to overflow-scroll
              rather than break. */}
          <dd className="min-w-0 overflow-x-auto scrollbar-thin whitespace-nowrap font-mono text-[11px] text-foreground">
            {pipeline.schedule || 'Manual only'}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-muted-foreground">Last run</dt>
          <dd className="whitespace-nowrap text-foreground">
            {lastRun ? <RelativeTime iso={lastRun.started_at} /> : 'Never'}
          </dd>
        </div>
      </dl>

      {canTrigger ? (
        <div className="space-y-1.5">
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            disabled={disabled}
            aria-describedby={note ? noteId : undefined}
            onClick={() => onRun(pipeline.name)}
          >
            {triggering ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Play className="size-3.5" aria-hidden />
            )}
            {triggering ? 'Starting…' : 'Run now'}
          </Button>
          {/* A disabled control with no explanation reads as a broken one. */}
          {note ? (
            <p id={noteId} className="text-xs text-muted-foreground" aria-live="polite">
              {note}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
