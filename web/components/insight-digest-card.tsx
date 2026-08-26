'use client';

import * as React from 'react';
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  FileText,
  Target,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ChartRenderer } from '@/components/chart-renderer';
import { CitationList } from '@/components/citation-list';
import { InsightSeverityBadge } from '@/components/insight-severity-badge';
import { cn, formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
import type {
  ChartSpec,
  Citation,
  ColumnDtype,
  Insight,
  InsightMetricFormat,
} from '@/lib/types';

/** Format a warehouse value using the metric's declared display format. */
export function formatMetricValue(value: number, format: InsightMetricFormat): string {
  switch (format) {
    case 'currency':
      return formatCurrency(value);
    case 'percent':
      return formatPercent(value);
    case 'integer':
      return formatNumber(value, 0);
    default:
      return formatNumber(value, 2);
  }
}

/** A signed value keeps its minus sign for a decline (currency compacts). */
function formatSigned(value: number, format: InsightMetricFormat): string {
  const sign = value < 0 ? '-' : value > 0 ? '+' : '';
  return `${sign}${formatMetricValue(Math.abs(value), format)}`;
}

function dtypeFor(format: InsightMetricFormat): ColumnDtype {
  if (format === 'currency') return 'currency';
  if (format === 'percent') return 'ratio';
  return 'number';
}

/** Build a ChartSpec for the metric's own trend, so ChartRenderer plots it. */
function trendSpec(insight: Insight): ChartSpec | null {
  if (insight.trend.length < 2) return null;
  return {
    kind: insight.trend.length > 3 ? 'area' : 'line',
    x: 'period',
    series: [{ y: 'value', label: insight.metric_label }],
    options: { yFormat: dtypeFor(insight.metric_format) },
    data: insight.trend.map((point) => ({ period: point.period, value: point.value })),
  };
}

/** A tiny dependency-free sparkline for the collapsed card header. */
function Sparkline({ insight }: { insight: Insight }) {
  const values = insight.trend.map((t) => t.value);
  if (values.length < 2) return null;
  const width = 96;
  const height = 32;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / span) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const stroke = insight.direction === 'down' ? 'hsl(var(--destructive))' : 'hsl(var(--success))';
  const last = points[points.length - 1]?.split(',').map(Number) ?? [width, height];
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
      aria-hidden
    >
      <polyline
        points={points.join(' ')}
        fill="none"
        stroke={stroke}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={last[0]} cy={last[1]} r={2.5} fill={stroke} />
    </svg>
  );
}

function evidenceToCitations(insight: Insight): Citation[] {
  return insight.evidence.map((e) => ({
    n: e.n,
    doc_id: e.doc_id,
    source_type: e.source_type,
    title: e.title,
    date: e.date ?? null,
    score: e.score ?? null,
    snippet: e.snippet ?? null,
  }));
}

export function InsightDigestCard({ insight }: { insight: Insight }) {
  const [open, setOpen] = React.useState(false);
  const spec = trendSpec(insight);
  const down = insight.direction === 'down';
  const detailId = `insight-detail-${insight.id}`;

  return (
    <Card className="animate-fade-in transition-shadow hover:shadow-md">
      <CardContent className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          <InsightSeverityBadge severity={insight.severity} />
          <Badge variant="secondary">{insight.metric_label}</Badge>
          <span className="text-xs text-muted-foreground">
            {insight.period} vs {insight.prior_period}
          </span>
          <span
            className={cn(
              'ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium tabular-nums',
              down ? 'bg-destructive/12 text-destructive' : 'bg-success/12 text-success',
            )}
          >
            {down ? (
              <ArrowDownRight className="size-3.5" />
            ) : (
              <ArrowUpRight className="size-3.5" />
            )}
            {formatPercent(insight.change_pct)}
          </span>
        </div>

        <p className="mt-3 text-sm font-medium leading-relaxed text-foreground">
          {insight.headline}
        </p>

        {/* At-a-glance row: sparkline · figures · root cause · evidence count */}
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-3">
            <Sparkline insight={insight} />
            <div className="text-xs text-muted-foreground">
              <span className="tabular-nums text-foreground">
                {formatMetricValue(insight.prior, insight.metric_format)}
              </span>
              {' → '}
              <span className="font-medium tabular-nums text-foreground">
                {formatMetricValue(insight.current, insight.metric_format)}
              </span>
            </div>
          </div>

          {insight.root_cause ? (
            <div className="flex items-center gap-1.5 text-xs">
              <Target className="size-3.5 text-primary" aria-hidden />
              <span className="text-muted-foreground">Root cause</span>
              <Badge variant="outline" className="font-medium">
                {insight.root_cause.segment}
              </Badge>
              <span className="text-muted-foreground">
                ({insight.root_cause.dimension},{' '}
                <span className="tabular-nums">
                  {formatSigned(insight.root_cause.delta, insight.metric_format)}
                </span>
                )
              </span>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">
              Ratio metric — no single-segment attribution
            </span>
          )}

          {insight.evidence.length > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <FileText className="size-3.5" aria-hidden />
              {insight.evidence.length} supporting document
              {insight.evidence.length === 1 ? '' : 's'}
            </span>
          ) : null}
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={detailId}
          className="mt-4 inline-flex items-center gap-1.5 rounded-md text-xs font-medium text-primary transition-colors hover:underline"
        >
          {open ? 'Hide detail' : 'Show root cause & evidence'}
          <ChevronDown
            className={cn('size-3.5 transition-transform', open && 'rotate-180')}
            aria-hidden
          />
        </button>

        {open ? (
          <div id={detailId} className="mt-4 space-y-5 border-t pt-4">
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">How this was flagged:</span>{' '}
              {insight.method}
            </p>

            {spec ? (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  {insight.metric_label} trend
                </p>
                <ChartRenderer spec={spec} height={200} />
              </div>
            ) : null}

            {insight.contributions.length > 0 ? (
              <ContributionTable insight={insight} />
            ) : null}

            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                Supporting documents
              </p>
              <CitationList citations={evidenceToCitations(insight)} />
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ContributionTable({ insight }: { insight: Insight }) {
  const fmt = insight.metric_format;
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        Contribution to the change (deterministic decomposition)
      </p>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Dimension</TableHead>
              <TableHead>Segment</TableHead>
              <TableHead className="text-right">{insight.prior_period}</TableHead>
              <TableHead className="text-right">{insight.period}</TableHead>
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="text-right">Share</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {insight.contributions.map((row, i) => {
              const isRoot =
                insight.root_cause?.dimension === row.dimension &&
                insight.root_cause?.segment === row.segment;
              return (
                <TableRow key={`${row.dimension}-${row.segment}-${i}`}>
                  <TableCell className="text-muted-foreground">{row.dimension}</TableCell>
                  <TableCell className={cn('font-medium', isRoot && 'text-primary')}>
                    {row.segment}
                    {isRoot ? <span className="ml-1 text-xs">(root cause)</span> : null}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMetricValue(row.prior, fmt)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMetricValue(row.current, fmt)}
                  </TableCell>
                  <TableCell
                    className={cn(
                      'text-right tabular-nums',
                      row.delta < 0 ? 'text-destructive' : row.delta > 0 ? 'text-success' : '',
                    )}
                  >
                    {formatSigned(row.delta, fmt)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {row.contribution_pct.toFixed(0)}%
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
