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
import { RelativeTime } from '@/components/relative-time';
import { formatDuration, formatNumber } from '@/lib/utils';
import type { PipelineRun } from '@/lib/types';

function runDuration(run: PipelineRun): number | null {
  if (!run.finished_at) return null;
  const finished = new Date(run.finished_at).getTime();
  const started = new Date(run.started_at).getTime();
  if (Number.isNaN(finished) || Number.isNaN(started)) return null;
  return finished - started;
}

/**
 * Keys in `row_counts` that are bookkeeping flags, not row totals.
 *
 * The offline store stamps `simulated: 1` on runs it advances itself. Summed
 * blindly, a full reload that moved no rows reports "1 row", which reads as a
 * real (and wrong) figure.
 */
const NON_ROW_KEYS = new Set(['simulated']);

function totalRows(run: PipelineRun): number {
  // `row_counts` is optional on the wire and absent on queued runs.
  const counts = run.row_counts ?? {};
  // `rows_processed` is the run's own total; summing it with the per-stage
  // keys that may sit beside it would double-count the same rows.
  if (typeof counts.rows_processed === 'number') return counts.rows_processed;
  return Object.entries(counts).reduce(
    (a, [key, b]) => a + (!NON_ROW_KEYS.has(key) && typeof b === 'number' ? b : 0),
    0,
  );
}

/**
 * A run that has not finished has moved an unknown number of rows so far, and a
 * queued run has moved none *yet*. Printing a confident `0` in the Rows column
 * for either reads as a finished run that did nothing.
 */
function rowsCell(run: PipelineRun): string {
  if (run.status === 'queued') return '—';
  if (run.status === 'running' && !Object.keys(run.row_counts ?? {}).length) return '—';
  return formatNumber(totalRows(run));
}

/** Run-history table from GET /pipeline-runs (docs/07 §4.3). */
export function PipelineRunTable({ runs }: { runs: PipelineRun[] }) {
  if (!runs.length) {
    return (
      <div className="rounded-lg border border-dashed px-6 py-12 text-center">
        <p className="text-sm font-medium">No runs recorded yet</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
          A run appears here as soon as a schedule fires or someone triggers a
          pipeline manually.
        </p>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border">
      <Table>
        <caption className="sr-only">
          Pipeline runs, most recent first. Each row links to that run’s stage
          detail.
        </caption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col" className="min-w-[200px]">
              Pipeline
            </TableHead>
            <TableHead scope="col" className="w-[120px]">
              Status
            </TableHead>
            <TableHead scope="col" className="w-[110px]">
              Trigger
            </TableHead>
            <TableHead scope="col" className="w-[140px]">
              Started
            </TableHead>
            <TableHead scope="col" className="w-[110px] text-right">
              Duration
            </TableHead>
            <TableHead scope="col" className="w-[100px] text-right">
              Rows
            </TableHead>
            <TableHead scope="col" className="w-10">
              <span className="sr-only">Open</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow key={run.id} className="group">
              <TableCell className="min-w-0">
                {/* One link per row. The chevron used to be a second link to
                    the same place, which doubled the tab stops in a table that
                    can run to fifty rows. */}
                <Link
                  href={`/pipelines/runs/${run.id}`}
                  className="font-medium hover:text-primary hover:underline"
                >
                  {run.pipeline}
                </Link>
                <p
                  className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground"
                  title={run.id}
                >
                  {run.id}
                </p>
                {run.error ? (
                  <p className="mt-1 max-w-sm truncate text-xs text-destructive" title={run.error}>
                    {run.error}
                  </p>
                ) : null}
              </TableCell>
              <TableCell>
                <StatusBadge status={run.status} />
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="capitalize">
                  {run.trigger}
                </Badge>
              </TableCell>
              <TableCell className="whitespace-nowrap text-muted-foreground">
                <RelativeTime iso={run.started_at} />
              </TableCell>
              <TableCell className="whitespace-nowrap text-right tabular-nums text-muted-foreground">
                {formatDuration(runDuration(run))}
              </TableCell>
              <TableCell className="text-right font-medium tabular-nums">
                {rowsCell(run)}
              </TableCell>
              <TableCell className="text-right text-muted-foreground">
                <ChevronRight
                  className="ml-auto size-4 transition-colors group-hover:text-foreground"
                  aria-hidden
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
