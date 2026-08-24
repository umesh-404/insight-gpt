'use client';

import * as React from 'react';
import { PackageX } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatTile } from '@/components/stat-tile';
import { TrendChart } from '@/components/trend-chart';
import { InventoryAtRiskTable } from '@/components/inventory-at-risk-table';
import { ChartRenderer } from '@/components/chart-renderer';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  DateRangeFilter,
  type DashboardFilters,
} from '@/components/date-range-filter';
import { useMetricQuery } from '@/lib/hooks';
import { MOCK_AT_RISK, MOCK_TOP_PRODUCTS } from '@/lib/mock';
import type { ChartSpec, MetricResult } from '@/lib/types';

function toTrendSpec(
  result: MetricResult | undefined,
  key: string,
  label: string,
  currency: boolean,
): ChartSpec | undefined {
  if (!result) return undefined;
  const monthIdx = result.columns.findIndex((c) => c.name === 'month');
  const valueIdx = result.columns.findIndex((c) => c.name !== 'month');
  if (monthIdx < 0 || valueIdx < 0 || !result.rows.length) return undefined;
  return {
    kind: 'area',
    x: 'month',
    series: [{ y: key, label }],
    options: currency ? { yFormat: 'currency' } : {},
    data: result.rows.map((row) => ({
      month: String(row[monthIdx]),
      [key]: Number(row[valueIdx]),
    })),
  };
}

export function DashboardView({ title = 'Retail overview' }: { title?: string }) {
  const [filters, setFilters] = React.useState<DashboardFilters>({
    range: 'quarter',
    region: 'all',
    category: 'all',
  });

  // Each control change re-issues governed metric queries (docs/07 §4.2).
  const timeRange = React.useMemo(
    () => ({ grain: 'month' as const, start: '2026-01-01', end: '2026-06-30' }),
    [],
  );
  const baseFilters = React.useMemo(
    () => ({
      ...(filters.region !== 'all' ? { region: filters.region } : {}),
      ...(filters.category !== 'all' ? { category: filters.category } : {}),
    }),
    [filters.region, filters.category],
  );

  const revenue = useMetricQuery({ metric: 'revenue', time_range: timeRange, filters: baseFilters });
  const orders = useMetricQuery({ metric: 'orders', time_range: timeRange, filters: baseFilters });
  const aov = useMetricQuery({ metric: 'aov', time_range: timeRange, filters: baseFilters });
  const returnRate = useMetricQuery({ metric: 'return_rate', time_range: timeRange, filters: baseFilters });

  const revenueSpec = toTrendSpec(revenue.data, 'revenue', 'Revenue', true);
  const ordersSpec = toTrendSpec(orders.data, 'orders', 'Orders', false);

  const topProductsSpec: ChartSpec = {
    kind: 'bar',
    x: 'name',
    series: [{ y: 'revenue', label: 'Revenue' }],
    options: { yFormat: 'currency' },
    data: MOCK_TOP_PRODUCTS,
  };

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title={title}
        description="Governed metrics over the semantic layer — tiles and charts always agree."
      />

      <DateRangeFilter value={filters} onChange={setFilters} />

      {/* KPI tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Revenue" result={revenue.data} loading={revenue.isLoading} />
        <StatTile label="Orders" result={orders.data} loading={orders.isLoading} />
        <StatTile label="Avg. order value" result={aov.data} loading={aov.isLoading} />
        <StatTile
          label="Return rate"
          result={returnRate.data}
          loading={returnRate.isLoading}
          invertDelta
        />
      </div>

      {/* Trends */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TrendChart title="Revenue" spec={revenueSpec} loading={revenue.isLoading} />
        <TrendChart title="Orders" spec={ordersSpec} loading={orders.isLoading} />
      </div>

      {/* Top products + at-risk */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Top products by revenue</CardTitle>
          </CardHeader>
          <CardContent>
            <ChartRenderer spec={topProductsSpec} height={260} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 pb-2">
            <PackageX className="size-4 text-warning" aria-hidden />
            <CardTitle className="text-base">Inventory at risk</CardTitle>
          </CardHeader>
          <CardContent>
            <InventoryAtRiskTable rows={MOCK_AT_RISK} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
