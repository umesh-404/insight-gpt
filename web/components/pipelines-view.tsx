'use client';

import * as React from 'react';
import Link from 'next/link';
import { Clock, Play, Loader2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { PipelineRunTable } from '@/components/pipeline-run-table';
import { StatusBadge } from '@/components/status-badge';
import { describeError, ErrorState } from '@/components/states';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { RelativeTime } from '@/components/relative-time';
import { usePipelines, useRuns, useTriggerRun } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { ApiError, type PipelineRunSummary } from '@/lib/types';

/** The two non-terminal run states the API treats as "already running". */
function isActive(status: string | undefined): boolean {
  return status === 'running' || status === 'queued';
}

export function PipelinesView() {
  const { toast } = useToast();
  const { hasRole } = useAuth();
  const pipelines = usePipelines();
  const runs = useRuns();
  const trigger = useTriggerRun();
  const isAdmin = hasRole('admin');

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

  // Both panels read endpoints that require the analyst role, so a viewer who
  // reaches this URL gets the same 403 twice, stacked. Say it once.
  const forbidden =
    pipelines.error instanceof ApiError && pipelines.error.status === 403;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Pipelines"
        description="Data-engineering monitor — schedules, run history, and manual triggers."
      />

      {forbidden ? (
        <ErrorState error={pipelines.error} />
      ) : (
      <>
      {/* Pipeline cards */}
      {pipelines.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : pipelines.isError ? (
        <ErrorState error={pipelines.error} onRetry={() => void pipelines.refetch()} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {pipelines.data?.map((pipeline) => {
            const lastRun = latestRunByPipeline[pipeline.name] ?? pipeline.last_run;
            return (
            <Card key={pipeline.name} className="flex flex-col">
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="min-w-0 break-words text-base">
                    {pipeline.name}
                  </CardTitle>
                  {lastRun ? (
                    <StatusBadge status={lastRun.status} className="shrink-0" />
                  ) : null}
                </div>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-4">
                <p className="text-sm text-muted-foreground">
                  {pipeline.description}
                </p>
                <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
                  <Badge variant="muted" className="gap-1">
                    <Clock className="size-3 shrink-0" />
                    {pipeline.schedule ? (
                      // A cron expression is a single token: let it push the
                      // badge wider rather than break "*/30 * * * *" across
                      // two lines, which is unreadable at projector distance.
                      <span className="whitespace-nowrap font-mono text-[11px]">
                        {pipeline.schedule}
                      </span>
                    ) : (
                      'Manual'
                    )}
                  </Badge>
                  {isAdmin ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="shrink-0"
                      // `queued` is as active as `running` — the backend
                      // refuses a second trigger for either (409), so the
                      // button must not invite the click.
                      disabled={
                        (trigger.isPending && trigger.variables === pipeline.name) ||
                        isActive(lastRun?.status)
                      }
                      onClick={() => onRun(pipeline.name)}
                    >
                      {trigger.isPending && trigger.variables === pipeline.name ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Play className="size-3.5" />
                      )}
                      Run now
                    </Button>
                  ) : null}
                </div>
                {lastRun ? (
                  <p className="text-xs text-muted-foreground">
                    Last run <RelativeTime iso={lastRun.started_at} />
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground">Never run</p>
                )}
              </CardContent>
            </Card>
            );
          })}
        </div>
      )}

      {/* Run history */}
      <div>
        <h2 className="mb-3 text-lg font-semibold tracking-tight">Run history</h2>
        {runs.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
        ) : (
          <PipelineRunTable runs={runs.data ?? []} />
        )}
      </div>
      </>
      )}
    </div>
  );
}
