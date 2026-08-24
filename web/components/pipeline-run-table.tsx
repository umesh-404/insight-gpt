'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { StatusBadge } from '@/components/status-badge';
import { formatDuration, timeAgo } from '@/lib/utils';
import type { PipelineRun } from '@/lib/types';

function runDuration(run: PipelineRun): number | null {
  if (!run.finished_at) return null;
  return new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
}

function totalRows(run: PipelineRun): number {
  return Object.values(run.row_counts).reduce((a, b) => a + b, 0);
}

/** Run-history table from GET /pipeline-runs (docs/07 §4.3). */
export function PipelineRunTable({ runs }: { runs: PipelineRun[] }) {
  if (!runs.length) {
    return (
      <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        No pipeline runs yet.
      </div>
    );
  }
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Pipeline</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Trigger</TableHead>
            <TableHead>Started</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead className="text-right">Rows</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow key={run.id} className="group">
              <TableCell className="font-medium">
                <Link
                  href={`/pipelines/runs/${run.id}`}
                  className="hover:text-primary hover:underline"
                >
                  {run.pipeline}
                </Link>
                {run.error ? (
                  <p className="mt-0.5 max-w-xs truncate text-xs text-destructive">
                    {run.error}
                  </p>
                ) : null}
              </TableCell>
              <TableCell>
                <StatusBadge status={run.status} />
              </TableCell>
              <TableCell>
                <Badge variant="outline">{run.trigger}</Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {timeAgo(run.started_at)}
              </TableCell>
              <TableCell className="text-right tabular-nums text-muted-foreground">
                {formatDuration(runDuration(run))}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {totalRows(run).toLocaleString()}
              </TableCell>
              <TableCell>
                <Link
                  href={`/pipelines/runs/${run.id}`}
                  aria-label={`View run ${run.id}`}
                  className="flex justify-end text-muted-foreground transition-colors group-hover:text-foreground"
                >
                  <ChevronRight className="size-4" />
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
