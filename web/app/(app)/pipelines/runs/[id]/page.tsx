'use client';

import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { EmptyState, ErrorState } from '@/components/states';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useRun } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { ApiError } from '@/lib/types';
import { formatDateTime, formatDuration, formatNumber } from '@/lib/utils';

function BackLink() {
  return (
    <Button asChild variant="ghost" size="sm" className="-ml-2">
      <Link href="/pipelines">
        <ArrowLeft className="size-4" aria-hidden /> Back to pipelines
      </Link>
    </Button>
  );
}

export default function RunDetailPage({ params }: { params: { id: string } }) {
  const { hasRole, loading } = useAuth();

  if (loading) {
    return (
      <div className="space-y-6 p-4 sm:p-6" aria-busy="true">
        <BackLink />
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  // `GET /pipeline-runs/{id}` is analyst-gated. Mounting the query for a viewer
  // fired a request that can only ever 403, so the role is checked before the
  // fetch exists rather than after it fails.
  if (!hasRole('analyst')) {
    return (
      <div className="space-y-6 p-4 sm:p-6">
        <BackLink />
        <EmptyState
          title="Run detail needs the analyst role"
          description="Pipeline runs are readable by analysts and administrators. Ask an administrator to raise your role."
        />
      </div>
    );
  }

  return <RunDetail id={params.id} />;
}

function RunDetail({ id }: { id: string }) {
  const { data: run, isLoading, isError, error, refetch } = useRun(id);

  if (isLoading) {
    return (
      <div className="space-y-6 p-4 sm:p-6" aria-busy="true">
        <BackLink />
        <Skeleton className="h-10 w-72" />
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-none" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
        <span className="sr-only">Loading run detail…</span>
      </div>
    );
  }

  if (isError || !run) {
    const status = error instanceof ApiError ? error.status : undefined;
    // A mistyped or expired run id must still offer a way back; without the
    // back link the failure panel was the only thing on the page.
    return (
      <div className="space-y-6 p-4 sm:p-6">
        <BackLink />
        {status === 404 ? (
          // 404 and 403 are both permanent answers for this URL and this token,
          // so neither gets a "Try again" that can only fail again.
          <EmptyState
            title="Run not found"
            description="This run id does not exist. It may have been trimmed from the history, or the link may be mistyped."
            action={
              <Button asChild variant="outline" size="sm">
                <Link href="/pipelines">See recent runs</Link>
              </Button>
            }
          />
        ) : (
          <ErrorState
            error={error}
            onRetry={status === 403 ? undefined : () => void refetch()}
          />
        )}
      </div>
    );
  }

  // `simulated` is a bookkeeping flag the offline store stamps on runs it
  // advances itself, not a row total — rendering it as a headline figure puts
  // an internal marker on screen beside the real counts.
  const rowCounts = Object.entries(run.row_counts ?? {}).filter(
    ([key]) => key !== 'simulated',
  );

  const duration = run.finished_at
    ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
    : null;

  return (
    <div className="space-y-8 p-4 sm:p-6">
      <BackLink />

      <PageHeader
        title={run.pipeline}
        description={`Run ${run.id}`}
        actions={<StatusBadge status={run.status} />}
      />

      {/* Facts first, in one hairline strip rather than four bordered cards. */}
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-4">
        <SummaryStat
          label="Trigger"
          value={
            <Badge variant="outline" className="capitalize">
              {run.trigger}
            </Badge>
          }
        />
        <SummaryStat label="Started" value={formatDateTime(run.started_at)} />
        <SummaryStat
          label="Finished"
          value={run.finished_at ? formatDateTime(run.finished_at) : 'In progress'}
        />
        <SummaryStat label="Duration" value={formatDuration(duration)} />
      </dl>

      {run.error ? (
        <div
          role="alert"
          className="rounded-lg border border-destructive/30 bg-destructive/5 p-4"
        >
          <p className="text-sm font-medium text-destructive">This run failed</p>
          <p className="mt-1.5 break-words font-mono text-xs text-destructive">
            {run.error}
          </p>
        </div>
      ) : null}

      <section aria-labelledby="stages-heading" className="space-y-3">
        <h2
          id="stages-heading"
          className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
        >
          Stages
        </h2>
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <caption className="sr-only">
              Per-stage row counts and timings for run {run.id}.
            </caption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col" className="min-w-[180px]">
                  Stage
                </TableHead>
                <TableHead scope="col" className="w-[110px] text-right">
                  Rows in
                </TableHead>
                <TableHead scope="col" className="w-[110px] text-right">
                  Rows out
                </TableHead>
                <TableHead scope="col" className="w-[110px] text-right">
                  Duration
                </TableHead>
                <TableHead scope="col" className="min-w-[160px]">
                  Notes
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {/* A table body with no rows renders as a header over nothing,
                  which reads as a loading failure. Say which of the two
                  reasons applies instead. */}
              {run.stages.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-10 text-center">
                    <p className="text-sm font-medium">No stage detail</p>
                    <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
                      {run.status === 'queued' || run.status === 'running'
                        ? 'Stages appear here as the run reports them. This page refreshes on its own.'
                        : 'This run finished without reporting per-stage detail.'}
                    </p>
                  </TableCell>
                </TableRow>
              ) : null}
              {run.stages.map((stage) => (
                <TableRow key={stage.name}>
                  <TableCell className="font-medium">{stage.name}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {formatNumber(stage.rows_in)}
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {formatNumber(stage.rows_out)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right tabular-nums text-muted-foreground">
                    {stage.ms ? formatDuration(stage.ms) : '—'}
                  </TableCell>
                  <TableCell>
                    {stage.error ? (
                      <span className="text-xs text-destructive">{stage.error}</span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>

      {rowCounts.length ? (
        <section aria-labelledby="counts-heading" className="space-y-3">
          <h2
            id="counts-heading"
            className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
          >
            Row counts
          </h2>
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-4">
            {rowCounts.map(([key, value]) => (
              <div key={key} className="bg-card p-4">
                <dt className="truncate text-xs text-muted-foreground" title={key}>
                  {key.replace(/_/g, ' ')}
                </dt>
                <dd className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">
                  {formatNumber(value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
    </div>
  );
}

function SummaryStat({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="bg-card p-4">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm font-medium">{value}</dd>
    </div>
  );
}
