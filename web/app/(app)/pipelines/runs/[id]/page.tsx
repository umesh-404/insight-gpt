'use client';

import Link from 'next/link';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { ErrorState } from '@/components/states';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useRun } from '@/lib/hooks';
import { formatDateTime, formatDuration, formatNumber } from '@/lib/utils';

export default function RunDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const { data: run, isLoading, isError, error, refetch } = useRun(params.id);

  if (isLoading) {
    return (
      <div className="flex h-[60vh] items-center justify-center" aria-busy="true">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (isError || !run) {
    return (
      <div className="p-6">
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  const duration = run.finished_at
    ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
    : null;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <Button asChild variant="ghost" size="sm" className="-ml-2">
        <Link href="/pipelines">
          <ArrowLeft className="size-4" /> Back to pipelines
        </Link>
      </Button>

      <PageHeader
        title={run.pipeline}
        description={`Run ${run.id}`}
        actions={<StatusBadge status={run.status} />}
      />

      {/* Summary */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <SummaryStat label="Trigger" value={<Badge variant="outline">{run.trigger}</Badge>} />
        <SummaryStat label="Started" value={formatDateTime(run.started_at)} />
        <SummaryStat
          label="Finished"
          value={run.finished_at ? formatDateTime(run.finished_at) : 'In progress'}
        />
        <SummaryStat label="Duration" value={formatDuration(duration)} />
      </div>

      {run.error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <p className="font-medium">Run error</p>
          <p className="mt-1 font-mono text-xs">{run.error}</p>
        </div>
      ) : null}

      {/* Stages */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Stages</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Stage</TableHead>
                  <TableHead className="text-right">Rows in</TableHead>
                  <TableHead className="text-right">Rows out</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {run.stages.map((stage) => (
                  <TableRow key={stage.name}>
                    <TableCell className="font-medium">{stage.name}</TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatNumber(stage.rows_in)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatNumber(stage.rows_out)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
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
        </CardContent>
      </Card>

      {/* Row counts */}
      {Object.keys(run.row_counts).length ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Row counts</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3">
              {Object.entries(run.row_counts).map(([key, value]) => (
                <div
                  key={key}
                  className="rounded-md border bg-muted/30 px-3 py-2"
                >
                  <p className="text-xs text-muted-foreground">{key}</p>
                  <p className="text-lg font-semibold tabular-nums">
                    {formatNumber(value)}
                  </p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
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
    <div className="rounded-lg border bg-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}
