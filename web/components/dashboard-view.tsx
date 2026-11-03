'use client';

import * as React from 'react';
import { PackageX } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatTile, type GoodDirection } from '@/components/stat-tile';
import { TrendChart } from '@/components/trend-chart';
import { InventoryAtRiskTable } from '@/components/inventory-at-risk-table';
import { ForecastPanel } from '@/components/forecast-panel';
import { ChartRenderer } from '@/components/chart-renderer';
import { EmptyState, ErrorState } from '@/components/states';
import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  ALL_VALUES,
  DateRangeFilter,
  type DashboardFilters,
} from '@/components/date-range-filter';
import { useMetricQueries, useMetricsCatalog } from '@/lib/hooks';
import {
  comparisonLabelFor,
  formatDay,
  grainFor,
  latestPeriodEnd,
  previousRange,
  resolveRange,
  seriesValues,
  summarize,
  toMetricChart,
  unitOf,
} from '@/lib/metrics';
import type {
  Cell,
  MetricFilter,
  MetricQuery,
  MetricResult,
  MetricSummary,
  MetricUnit,
} from '@/lib/types';

const TOP_N = 8;

/**
 * One unfiltered, month-grained revenue breakdown whose only job is to tell us
 * how far the warehouse actually runs. Every relative range on this page is
 * resolved against that date instead of the browser clock — see
 * `latestPeriodEnd`.
 */
const COVERAGE_QUERY = [
  {
    name: 'coverage',
    query: { metric: 'revenue', dimensions: ['date'], time_grain: 'month' } as MetricQuery,
  },
];

/**
 * KPI tiles, in display order. `unit` is refined from the live catalog.
 *
 * `goodDirection` encodes business meaning, not arithmetic: a fall in revenue
 * is bad, a fall in return rate is good. Without it every red arrow would say
 * the same thing regardless of what the metric measures.
 */
const KPIS: Array<{
  metric: string;
  label: string;
  unit: MetricUnit;
  goodDirection: GoodDirection;
}> = [
  // Revenue is first and rendered as the hero figure: it is the question this
  // page exists to answer, and four tiles at identical weight leave the reader
  // to decide which one that is.
  { metric: 'revenue', label: 'Revenue', unit: 'currency', goodDirection: 'up' },
  { metric: 'orders', label: 'Orders', unit: 'count', goodDirection: 'up' },
  {
    metric: 'avg_order_value',
    label: 'Avg. order value',
    unit: 'currency',
    goodDirection: 'up',
  },
  {
    metric: 'return_rate',
    label: 'Return rate',
    unit: 'ratio',
    goodDirection: 'down',
  },
];

/** Distinct values of a dimension, read out of a grouped metric result. */
function dimensionValues(
  result: { columns: Array<{ name: string }>; rows: Cell[][] } | undefined,
  dimension: string,
): string[] {
  if (!result) return [];
  const index = result.columns.findIndex((c) => c.name === dimension);
  if (index < 0) return [];
  const seen = new Set<string>();
  for (const row of result.rows) {
    const value = row[index];
    if (typeof value === 'string' && value.trim()) seen.add(value);
  }
  return [...seen].sort((a, b) => a.localeCompare(b));
}

/**
 * Suppress a zero that is an artefact of the aggregate rather than a measurement.
 *
 * The two shapes of "nothing happened in this window" do not come back the same
 * way: a `SUM` over no rows is SQL NULL, so `summarize` already returns null and
 * the tile prints "—"; a `COUNT` over no rows is the number 0, so the Orders
 * tile printed a confident "0" beside three "—" neighbours and claimed to have
 * counted something. The per-period breakdown for the identical window is the
 * evidence: no periods at all means the warehouse holds nothing here, and the
 * zero is reported as unknown. A real zero inside a window that *does* have rows
 * still shows as 0 — that one is a measurement.
 */
function honestSummary(
  summary: MetricSummary | null,
  trend: MetricResult | undefined,
  settled: boolean,
): MetricSummary | null {
  if (!summary || !settled) return summary;
  const hasPeriods = (trend?.rows.length ?? 0) > 0;
  return summary.value === 0 && !hasPeriods ? null : summary;
}

export function DashboardView({
  title = 'Retail overview',
  note,
}: {
  title?: string;
  /** Shown under the header when the page is a preview of something unbuilt. */
  note?: string;
}) {
  const [filters, setFilters] = React.useState<DashboardFilters>({
    range: 'ytd',
    region: ALL_VALUES,
    category: ALL_VALUES,
  });

  const catalog = useMetricsCatalog();

  // How far the data runs. Resolved before anything else, because it is the
  // clock every other query on this page is measured against.
  const coverage = useMetricQueries(COVERAGE_QUERY);
  const anchor = React.useMemo(
    () => latestPeriodEnd(coverage.byName.coverage, 'revenue'),
    [coverage.byName.coverage],
  );
  const anchorKnown = !coverage.isLoading;

  const timeRange = React.useMemo(
    // `resolveRange` falls back to the wall clock when we have no anchor; that
    // is the honest answer for an empty warehouse, not a guessed date.
    () => resolveRange(filters.range, anchor ?? undefined),
    [filters.range, anchor],
  );
  const priorRange = React.useMemo(() => previousRange(timeRange), [timeRange]);
  const grain = grainFor(timeRange);

  // Dimension predicates shared by every query on the page.
  const dimensionFilters = React.useMemo<MetricFilter[]>(() => {
    const list: MetricFilter[] = [];
    if (filters.region !== ALL_VALUES) {
      list.push({ dimension: 'region', op: 'eq', values: [filters.region] });
    }
    if (filters.category !== ALL_VALUES) {
      list.push({ dimension: 'category', op: 'eq', values: [filters.category] });
    }
    return list;
  }, [filters.region, filters.category]);

  const availableMetrics = React.useMemo(
    () => new Set(catalog.data?.metrics.map((m) => m.key) ?? []),
    [catalog.data],
  );
  // Before the catalog resolves, assume the standard set so the first paint
  // still issues queries instead of showing four empty tiles.
  const has = (metric: string) => !catalog.data || availableMetrics.has(metric);

  const queries = React.useMemo(() => {
    const list: Array<{ name: string; query: MetricQuery }> = [];
    const base = { filters: dimensionFilters, time_range: timeRange };

    for (const kpi of KPIS) {
      if (!has(kpi.metric)) continue;
      list.push({ name: kpi.metric, query: { metric: kpi.metric, ...base } });
      list.push({
        name: `${kpi.metric}__prev`,
        query: { metric: kpi.metric, filters: dimensionFilters, time_range: priorRange },
      });
      // One per-period breakdown per KPI: it draws the tile's sparkline, and
      // for revenue/orders it is the same result the big trend charts plot.
      list.push({
        name: `${kpi.metric}__trend`,
        query: {
          metric: kpi.metric,
          dimensions: ['date'],
          time_grain: grain,
          ...base,
        },
      });
    }

    // Top products by revenue.
    if (has('revenue')) {
      list.push({
        name: 'top_products',
        query: {
          metric: 'revenue',
          dimensions: ['product'],
          ...base,
          order_by_metric: 'desc',
          limit: TOP_N,
        },
      });
    }

    // Inventory at risk: the lowest on-hand SKUs. Deliberately not time-bound —
    // the inventory snapshot is a current-state metric.
    if (has('units_on_hand')) {
      list.push({
        name: 'at_risk',
        query: {
          metric: 'units_on_hand',
          dimensions: ['product'],
          filters: dimensionFilters,
          order_by_metric: 'asc',
          limit: TOP_N,
        },
      });
    }

    // Unfiltered breakdowns that populate the filter dropdowns.
    if (has('revenue')) {
      list.push({ name: 'regions', query: { metric: 'revenue', dimensions: ['region'] } });
      list.push({ name: 'categories', query: { metric: 'revenue', dimensions: ['category'] } });
    }
    return list;
    // `has` closes over catalog.data, which is in the dep list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dimensionFilters, timeRange, priorRange, grain, catalog.data]);

  const {
    byName,
    isLoading: queriesLoading,
    isError,
    error,
    refetch,
  } = useMetricQueries(queries, anchorKnown);
  // Until the anchor resolves the queries above are deliberately not issued, so
  // the page must report itself as loading rather than as empty.
  const isLoading = !anchorKnown || queriesLoading;

  // Render hints, in order of authority: the result's own `meta`, then the
  // catalog definition, then the compile-time default.
  const unitFor = React.useCallback(
    (metric: string, fallback: MetricUnit): MetricUnit =>
      unitOf(
        byName[metric] ?? byName[`${metric}__trend`],
        catalog.data?.metrics.find((m) => m.key === metric)?.unit ?? fallback,
      ),
    [byName, catalog.data],
  );

  const dimensionLabel = React.useCallback(
    (key: string, fallback: string): string =>
      catalog.data?.dimensions.find((d) => d.key === key)?.label ?? fallback,
    [catalog.data],
  );

  const additiveFor = React.useCallback(
    (metric: string): boolean | undefined =>
      catalog.data?.metrics.find((m) => m.key === metric)?.additive,
    [catalog.data],
  );

  const regions = dimensionValues(byName.regions, 'region');
  const categories = dimensionValues(byName.categories, 'category');

  const revenueTrend = toMetricChart(
    byName.revenue__trend,
    'revenue',
    'Revenue',
    unitFor('revenue', 'currency'),
    'area',
  );
  const ordersTrend = toMetricChart(
    byName.orders__trend,
    'orders',
    'Orders',
    unitFor('orders', 'count'),
    'area',
  );
  const topProducts = toMetricChart(
    byName.top_products,
    'revenue',
    'Revenue',
    unitFor('revenue', 'currency'),
    'bar',
  );

  const catalogFailed = catalog.isError;

  // Spelled out so nobody has to guess what "this quarter" resolved to. The
  // warehouse is a fixed fixture, so its last day and today's date differ.
  const rangeNote = anchor
    ? `Showing ${formatDay(new Date(`${timeRange.start}T00:00:00Z`))} – ${formatDay(
        new Date(`${timeRange.end}T00:00:00Z`),
      )} · data runs to ${formatDay(anchor)}`
    : null;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title={title}
        description="Governed metrics over the semantic layer — tiles and charts always agree."
      />

      <DateRangeFilter
        value={filters}
        onChange={setFilters}
        regions={regions}
        categories={categories}
        regionLabel={dimensionLabel('region', 'Region')}
        categoryLabel={dimensionLabel('category', 'Category')}
        loading={isLoading}
        resolvedNote={rangeNote}
      />

      {note ? (
        <p className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {note}
        </p>
      ) : null}

      {isError || catalogFailed || coverage.isError ? (
        <ErrorState
          error={error ?? catalog.error ?? coverage.error}
          onRetry={() => {
            refetch();
            coverage.refetch();
            void catalog.refetch();
          }}
        />
      ) : (
        <>
          {/* KPI band. Five columns with a double-width hero: the figure the
              page is about gets the display size, the three supporting metrics
              share one scan line beside it. */}
          <section
            aria-labelledby="dash-kpis"
            // Five columns wait for `xl`, not `lg`. With the sidebar taking
            // 256px, a five-column band at 1024px leaves a supporting column
            // ~86px wide, which is narrower than a seven-glyph currency
            // figure can be drawn at any size that still reads as a KPI.
            // Between `lg` and `xl` the band stays two-up and keeps the
            // figures whole.
            className="stagger grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5 xl:gap-4"
          >
            <h2 id="dash-kpis" className="sr-only">
              Key metrics
            </h2>
            {KPIS.filter((kpi) => has(kpi.metric)).map((kpi, i) => {
              const current = byName[kpi.metric];
              const trend = byName[`${kpi.metric}__trend`];
              const hero = i === 0;
              return (
                <div key={kpi.metric} className={hero ? 'sm:col-span-2' : undefined}>
                  <StatTile
                    label={kpi.label}
                    emphasis={hero ? 'hero' : 'default'}
                    summary={honestSummary(
                      summarize(
                        kpi.metric,
                        unitFor(kpi.metric, kpi.unit),
                        current,
                        byName[`${kpi.metric}__prev`],
                        additiveFor(kpi.metric),
                      ),
                      trend,
                      !isLoading,
                    )}
                    points={seriesValues(trend, kpi.metric)}
                    comparisonLabel={comparisonLabelFor(filters.range)}
                    loading={isLoading && !current}
                    goodDirection={kpi.goodDirection}
                  />
                </div>
              );
            })}
          </section>

          {/* Trends */}
          <section aria-labelledby="dash-trends" className="space-y-3">
            <h2
              id="dash-trends"
              className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Trend over the selected range
            </h2>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <TrendChart
                title="Revenue"
                spec={revenueTrend}
                loading={isLoading && !byName.revenue__trend}
              />
              <TrendChart
                title="Orders"
                spec={ordersTrend}
                loading={isLoading && !byName.orders__trend}
              />
            </div>
          </section>

          {/* Top products + at-risk inventory. `items-start`: the at-risk table
              is far taller than the fixed-height chart, and a stretched grid
              row left a card-height band of dead space under the bars. */}
          <section
            aria-labelledby="dash-breakdowns"
            className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2"
          >
            <h2 id="dash-breakdowns" className="sr-only">
              Breakdowns
            </h2>
            <Card>
              {/* No CardHeader: the figure's own caption row carries the title
                  and the chart/table switch, so a header above it would print
                  the same words twice. */}
              <CardContent className="p-4 sm:p-5">
                {isLoading && !byName.top_products ? (
                  <>
                    <Skeleton className="h-5 w-48" />
                    <Skeleton className="mt-3 h-[300px] w-full" />
                  </>
                ) : topProducts ? (
                  // 300px over eight bands leaves room for a label that wraps
                  // to a second line without the two colliding.
                  <ChartRenderer
                    spec={topProducts}
                    title="Top products by revenue"
                    height={300}
                  />
                ) : (
                  <>
                    <p className="text-base font-semibold leading-none tracking-tight text-foreground">
                      Top products by revenue
                    </p>
                    <div className="mt-3">
                      <EmptyState
                        title="No revenue in this window"
                        description="Widen the date range or clear a filter."
                      />
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center gap-2 pb-3">
                <PackageX className="size-4 text-warning" aria-hidden />
                <CardTitle className="text-base">Inventory at risk</CardTitle>
              </CardHeader>
              <CardContent>
                <InventoryAtRiskTable
                  result={byName.at_risk}
                  loading={isLoading && !byName.at_risk}
                />
              </CardContent>
            </Card>
          </section>

          {/* Looking forward. Deliberately last: the dashboard reports what
              happened before it suggests what might, and on the demo data this
              panel usually explains why it will not project at all. */}
          <section aria-labelledby="forecast-heading">
            <h2 id="forecast-heading" className="sr-only">
              Forecast
            </h2>
            <ForecastPanel />
          </section>
        </>
      )}
    </div>
  );
}
