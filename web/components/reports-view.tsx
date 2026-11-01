'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { CheckCircle2, FileText, Loader2, Plus, Sparkles, XCircle } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { describeError, EmptyState, ErrorState } from '@/components/states';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useMetricQueries, useReports } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { qk } from '@/lib/hooks';
import { useQueryClient } from '@tanstack/react-query';
import {
  formatDay,
  latestPeriodEnd,
  RANGE_OPTIONS,
  resolveRange,
  type RangeKey,
} from '@/lib/metrics';
import { formatDateTime } from '@/lib/utils';
import {
  ApiError,
  type MetricQuery,
  type ReportSection,
  type ReportSummary,
} from '@/lib/types';

/**
 * How far the warehouse actually runs, read the same way the dashboard reads
 * it. "This quarter" has to mean the quarter the data is in: resolved against
 * the browser clock it named a period the fixture has no rows for, so every
 * section of the report came back empty.
 */
const COVERAGE_QUERY = [
  {
    name: 'coverage',
    query: { metric: 'revenue', dimensions: ['date'], time_grain: 'month' } as MetricQuery,
  },
];

const SECTIONS: Array<{ id: ReportSection; label: string }> = [
  { id: 'kpis', label: 'Headline KPIs' },
  { id: 'sales', label: 'Sales decomposition' },
  { id: 'inventory', label: 'Inventory health' },
  { id: 'voice_of_customer', label: 'Voice of customer' },
];

/**
 * A report's states are ready / generating / failed. Mapping them onto the run
 * vocabulary printed "Success" next to a finished report, which reads as the
 * outcome of a job rather than the state of a document.
 */
function ReportStatusBadge({ status }: { status: ReportSummary['status'] }) {
  if (status === 'failed') {
    return (
      <Badge variant="destructive">
        <XCircle className="size-3" aria-hidden /> Failed
      </Badge>
    );
  }
  if (status === 'generating') {
    return (
      <Badge variant="default">
        <span className="animate-spin">
          <Loader2 className="size-3" aria-hidden />
        </span>
        Generating
      </Badge>
    );
  }
  return (
    <Badge variant="success">
      <CheckCircle2 className="size-3" aria-hidden /> Ready
    </Badge>
  );
}

export function ReportsView() {
  const router = useRouter();
  const { toast } = useToast();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const reports = useReports();
  const canGenerate = hasRole('analyst');

  const [title, setTitle] = React.useState('Executive summary');
  const [range, setRange] = React.useState<RangeKey>('quarter');
  const [selected, setSelected] = React.useState<Set<ReportSection>>(
    new Set(['kpis', 'sales', 'voice_of_customer']),
  );
  const [submitting, setSubmitting] = React.useState(false);

  const coverage = useMetricQueries(COVERAGE_QUERY, canGenerate);
  const anchor = React.useMemo(
    () => latestPeriodEnd(coverage.byName.coverage, 'revenue'),
    [coverage.byName.coverage],
  );
  const period = React.useMemo(
    // Falls back to the wall clock when coverage is unknown — the honest
    // answer for an empty warehouse, not a guessed date.
    () => resolveRange(range, anchor ?? undefined),
    [range, anchor],
  );

  const toggle = (section: ReportSection) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  };

  const onGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || selected.size === 0) return;
    setSubmitting(true);
    try {
      const res = await api.createReport({
        title: title.trim(),
        period,
        sections: Array.from(selected),
      });
      // Generation is synchronous on the offline stack, so the handle already
      // carries the final state: announcing "started" for a report that is
      // already ready — or that failed — misreports what just happened.
      const failed = res.status === 'failed';
      toast({
        title: failed
          ? 'Report generation failed'
          : res.status === 'generating'
            ? 'Report generation started'
            : 'Report ready',
        variant: failed ? 'destructive' : 'success',
      });
      void queryClient.invalidateQueries({ queryKey: qk.reports });
      router.push(`/reports/${res.report_id}`);
    } catch (err) {
      toast({
        title: 'Could not start generation',
        description: describeError(err),
        variant: 'destructive',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6 p-4 sm:p-6 min-w-0 max-w-full overflow-hidden">
      <PageHeader
        title="Reports"
        description="Generate cited, exportable executive narratives over a chosen period."
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px] grid-cols-1">
        {/* Report list */}
        <div className="space-y-3 min-w-0 max-w-full">
          <h2 className="text-lg font-semibold tracking-tight">Your reports</h2>
          {reports.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : reports.isError ? (
            // A 403 is a permanent answer for this role — offering "Try again"
            // invites the reader to click a button that can never succeed.
            <ErrorState
              error={reports.error}
              onRetry={
                reports.error instanceof ApiError && reports.error.status === 403
                  ? undefined
                  : () => void reports.refetch()
              }
            />
          ) : reports.data && reports.data.length ? (
            <ul className="stagger space-y-3">
              {reports.data.map((report) => (
                <li key={report.id}>
                  <Link
                    href={`/reports/${report.id}`}
                    className="flex items-center gap-4 rounded-lg border bg-card p-4 shadow-soft transition-[box-shadow,border-color,transform] duration-200 ease-out hover:-translate-y-px hover:border-primary/40 hover:shadow-raised"
                  >
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <FileText className="size-5" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{report.title}</p>
                      <p className="text-xs text-muted-foreground">
                        Created {formatDateTime(report.created_at)}
                      </p>
                    </div>
                    <ReportStatusBadge status={report.status} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            // The generate panel only sits "on the right" at xl and above;
            // below that it stacks underneath this list, so the copy must not
            // point at a position.
            <EmptyState
              title="No reports yet"
              description="Use the generate panel to build your first executive report."
              icon={<FileText className="size-5" />}
            />
          )}
        </div>

        {/* Generate panel */}
        <Card className="min-w-0 max-w-full h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="size-4 text-primary" /> Generate report
            </CardTitle>
          </CardHeader>
          <CardContent>
            {canGenerate ? (
              <form onSubmit={onGenerate} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="report-title">Title</Label>
                  <Input
                    id="report-title"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="report-period">Period</Label>
                  <Select
                    id="report-period"
                    options={RANGE_OPTIONS}
                    value={range}
                    onChange={(e) => setRange(e.target.value as RangeKey)}
                  />
                  <p className="text-xs text-muted-foreground">
                    {period.start} — {period.end}
                    {anchor ? ` · data runs to ${formatDay(anchor)}` : ''}
                  </p>
                </div>
                <fieldset className="space-y-2">
                  <legend className="text-sm font-medium">Sections</legend>
                  {SECTIONS.map((section) => (
                    <label
                      key={section.id}
                      className="flex cursor-pointer items-center gap-2.5 rounded-md border p-2.5 text-sm transition-colors hover:bg-accent"
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(section.id)}
                        onChange={() => toggle(section.id)}
                        className="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
                      />
                      {section.label}
                    </label>
                  ))}
                </fieldset>
                <Button
                  type="submit"
                  className="w-full"
                  disabled={submitting || selected.size === 0}
                >
                  {submitting ? (
                    <>
                      <Loader2 className="size-4 animate-spin" /> Generating…
                    </>
                  ) : (
                    <>
                      <Plus className="size-4" /> Generate
                    </>
                  )}
                </Button>
              </form>
            ) : (
              // Reports are analyst-gated end to end: GET /reports and
              // GET /reports/{id} both refuse a viewer, so promising read
              // access here sent viewers to a 403.
              <p className="text-sm text-muted-foreground">
                Reports are available to the analyst role and above. Ask an
                administrator to raise your role to generate and read them.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
