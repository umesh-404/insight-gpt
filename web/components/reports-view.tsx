'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  CheckCircle2,
  FileText,
  Loader2,
  Lock,
  Plus,
  XCircle,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { describeError, EmptyState, ErrorState } from '@/components/states';
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

const SECTIONS: Array<{ id: ReportSection; label: string; hint: string }> = [
  { id: 'kpis', label: 'Headline KPIs', hint: 'Revenue, orders and margin for the period' },
  { id: 'sales', label: 'Sales decomposition', hint: 'What moved the top line, and by how much' },
  { id: 'inventory', label: 'Inventory health', hint: 'Cover, stock-outs and at-risk lines' },
  {
    id: 'voice_of_customer',
    label: 'Voice of customer',
    hint: 'Themes from support tickets and reviews, cited',
  },
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

/**
 * Reports are analyst-gated end to end: `GET /reports`, `GET /reports/{id}` and
 * `POST /reports` all refuse a viewer. So the role decides whether the queries
 * are mounted at all — listing them for a viewer and then rendering the 403 was
 * a request guaranteed to fail, and copy promising read access sent viewers on
 * to a second 403 on the detail page.
 */
export function ReportsView() {
  const { hasRole, loading } = useAuth();
  const canUseReports = hasRole('analyst');

  return (
    <div className="min-w-0 max-w-full space-y-6 overflow-hidden p-4 sm:p-6">
      <PageHeader
        title="Reports"
        description="Executive narratives built from governed metrics and cited documents, exportable as Markdown or PDF."
      />

      {loading ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]" aria-busy="true">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      ) : canUseReports ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <ReportList />
          <GeneratePanel />
        </div>
      ) : (
        <EmptyState
          title="Reports need the analyst role"
          description="Generating and reading reports is restricted to analysts and administrators. Ask an administrator to raise your role."
          icon={<Lock className="size-5" aria-hidden />}
        />
      )}
    </div>
  );
}

function ReportList() {
  const reports = useReports();

  return (
    <section aria-labelledby="reports-heading" className="min-w-0 max-w-full space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="reports-heading"
          className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
        >
          Your reports
        </h2>
        {reports.data?.length ? (
          <p className="text-xs text-muted-foreground">
            {reports.data.length} {reports.data.length === 1 ? 'report' : 'reports'}
          </p>
        ) : null}
      </div>

      {reports.isLoading ? (
        <div className="space-y-px overflow-hidden rounded-lg border bg-border">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-[72px] rounded-none" />
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
        // A hairline-separated list rather than a stack of lifting cards: these
        // rows are a document index, and four shadows down a column read as
        // four unrelated objects.
        <ul className="divide-y overflow-hidden rounded-lg border bg-card">
          {reports.data.map((report) => (
            <li key={report.id}>
              <Link
                href={`/reports/${report.id}`}
                className="flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              >
                <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{report.title}</p>
                  <p className="text-xs text-muted-foreground">
                    Generated {formatDateTime(report.created_at)}
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
          description="Pick a period and the sections you need, then generate your first executive report."
          icon={<FileText className="size-5" aria-hidden />}
        />
      )}
    </section>
  );
}

function GeneratePanel() {
  const router = useRouter();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [title, setTitle] = React.useState('Executive summary');
  const [range, setRange] = React.useState<RangeKey>('quarter');
  const [selected, setSelected] = React.useState<Set<ReportSection>>(
    new Set(['kpis', 'sales', 'voice_of_customer']),
  );
  const [submitting, setSubmitting] = React.useState(false);

  const coverage = useMetricQueries(COVERAGE_QUERY);
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

  const titleEmpty = !title.trim();
  const noSections = selected.size === 0;

  const onGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (titleEmpty || noSections) return;
    setSubmitting(true);
    try {
      const res = await api.createReport({
        title: title.trim(),
        // The Period control is not decoration: it is sent as the engine's
        // `time_range` override, so the generated report is scoped to the
        // window printed under the control.
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
        description: failed
          ? 'Open the report for the reason it failed.'
          : undefined,
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
    <section
      aria-labelledby="generate-heading"
      className="h-fit min-w-0 max-w-full rounded-lg border bg-card"
    >
      <div className="border-b px-5 py-4">
        <h2 id="generate-heading" className="text-base font-semibold tracking-tight">
          Generate a report
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Every figure is drawn from the governed semantic layer and cited.
        </p>
      </div>

      <form onSubmit={onGenerate} noValidate className="space-y-5 p-5">
        <div className="space-y-1.5">
          <Label htmlFor="report-title">Title</Label>
          <Input
            id="report-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={120}
            aria-invalid={titleEmpty}
            aria-describedby={titleEmpty ? 'report-title-error' : undefined}
          />
          {titleEmpty ? (
            <p id="report-title-error" className="text-xs text-destructive">
              Give the report a title — it becomes the document heading.
            </p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="report-period">Period</Label>
          <Select
            id="report-period"
            options={RANGE_OPTIONS}
            value={range}
            onChange={(e) => setRange(e.target.value as RangeKey)}
            aria-describedby="report-period-window"
          />
          {/* The resolved window stays visible because the relative labels are
              anchored to warehouse coverage, not to today: "this quarter"
              against the browser clock named a quarter the warehouse has no
              rows for and produced an empty report. */}
          <div id="report-period-window" className="rounded-md border bg-muted/40 px-3 py-2">
            <p className="text-xs text-muted-foreground">Resolved window</p>
            <p className="mt-0.5 font-mono text-xs tabular-nums text-foreground">
              {period.start} → {period.end}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {anchor
                ? `Data runs to ${formatDay(anchor)}.`
                : 'Warehouse coverage is unknown, so this window is resolved against today.'}
            </p>
          </div>
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Sections</legend>
          <p className="pb-1 text-xs text-muted-foreground">
            Each one becomes a numbered section in the document.
          </p>
          <div className="divide-y overflow-hidden rounded-md border">
            {SECTIONS.map((section) => (
              <label
                key={section.id}
                className="flex min-h-11 cursor-pointer items-start gap-3 p-3 text-sm transition-colors hover:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-inset has-[:focus-visible]:ring-ring"
              >
                <input
                  type="checkbox"
                  checked={selected.has(section.id)}
                  onChange={() => toggle(section.id)}
                  className="mt-0.5 size-4 shrink-0 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
                />
                <span className="min-w-0">
                  <span className="block font-medium leading-snug">{section.label}</span>
                  <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
                    {section.hint}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {/* A disabled submit with no stated reason reads as a broken button. */}
          <p
            className={
              noSections ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'
            }
            aria-live="polite"
          >
            {noSections
              ? 'Choose at least one section.'
              : `${selected.size} of ${SECTIONS.length} selected.`}
          </p>
        </fieldset>

        <Button
          type="submit"
          className="w-full"
          disabled={submitting || noSections || titleEmpty}
        >
          {submitting ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden /> Generating…
            </>
          ) : (
            <>
              <Plus className="size-4" aria-hidden /> Generate report
            </>
          )}
        </Button>
      </form>
    </section>
  );
}
