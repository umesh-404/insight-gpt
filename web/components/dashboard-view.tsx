'use client';

import * as React from 'react';
import { PackageX } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatTile, type GoodDirection } from '@/components/stat-tile';
import { TrendChart } from '@/components/trend-chart';
import { InventoryAtRiskTable } from '@/components/inventory-at-risk-table';
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
  grainFor,
  previousRange,
  resolveRange,
  seriesValues,
  summarize,
  toMetricChart,
  unitOf,
} from '@/lib/metrics';
import type { Cell, MetricFilter, MetricQuery, MetricUnit } from '@/lib/types';

const TOP_N = 8;

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

export function DashboardView({ title = 'Retail overview' }: { title?: string }) {
  const [filters, setFilters] = React.useState<DashboardFilters>({
    range: 'ytd',
    region: ALL_VALUES,
    category: ALL_VALUES,
  });

  const catalog = useMetricsCatalog();

  const timeRange = React.useMemo(() => resolveRange(filters.range), [filters.range]);
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

  const { byName, isLoading, isError, error, refetch } = useMetricQueries(queries);

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
      />

      {isError || catalogFailed ? (
        <ErrorState
          error={error ?? catalog.error}
          onRetry={() => {
            refetch();
            void catalog.refetch();
          }}
        />
      ) : (
        <>
          {/* KPI tiles */}
          <section
            aria-labelledby="dash-kpis"
            className="stagger grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
          >
            <h2 id="dash-kpis" className="sr-only">
              Key metrics
            </h2>
            {KPIS.filter((kpi) => has(kpi.metric)).map((kpi) => {
              const current = byName[kpi.metric];
              return (
                <StatTile
                  key={kpi.metric}
                  label={kpi.label}
                  summary={summarize(
                    kpi.metric,
                    unitFor(kpi.metric, kpi.unit),
                    current,
                    byName[`${kpi.metric}__prev`],
                    additiveFor(kpi.metric),
                  )}
                  points={seriesValues(byName[`${kpi.metric}__trend`], kpi.metric)}
                  comparisonLabel={comparisonLabelFor(filters.range)}
                  loading={isLoading && !current}
                  goodDirection={kpi.goodDirection}
                />
              );
            })}
          </section>

          {/* Trends */}
          <section aria-labelledby="dash-trends" className="space-y-3">
            <h2
              id="dash-trends"
              className="text-2xs font-medium uppercase tracking-wider text-muted-foreground"
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

          {/* Top products + at-risk inventory */}
          <section
            aria-labelledby="dash-breakdowns"
            className="grid grid-cols-1 gap-4 lg:grid-cols-2"
          >
            <h2 id="dash-breakdowns" className="sr-only">
              Breakdowns
            </h2>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Top products by revenue</CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading && !byName.top_products ? (
                  <Skeleton className="h-[260px] w-full" />
                ) : topProducts ? (
                  <ChartRenderer spec={topProducts} height={260} />
                ) : (
                  <EmptyState
                    title="No revenue in this window"
                    description="Widen the date range or clear a filter."
                  />
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center gap-2 pb-2">
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
        </>
      )}
    </div>
  );
}
