'use client';

import * as React from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Info, ShieldQuestion, TrendingUp } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/states';
import { useForecast, useForecastMetrics } from '@/lib/hooks';
import type { ForecastPoint, ForecastResult, MetricFormat } from '@/lib/types';
import { formatCurrency, formatNumber, formatPercent } from '@/lib/utils';

/**
 * Projection of a governed metric, and — just as often — an explanation of why
 * the system will not project it.
 *
 * The demo warehouse holds two quarters, and the engine refuses below four, so
 * the refusal is not an edge case here: it is the common path. It is therefore
 * designed as a real state (observed history still plotted, the reason stated
 * plainly) rather than an error or an empty box. A forecast the data cannot
 * support is the one output this product must never produce.
 */

const GRAINS = ['quarter', 'month'] as const;
type Grain = (typeof GRAINS)[number];

function formatValue(value: number, format: MetricFormat): string {
  switch (format) {
    case 'currency':
      return formatCurrency(value);
    case 'percent':
      return formatPercent(value);
    case 'integer':
      return formatNumber(value);
    default:
      return formatNumber(value, 2);
  }
}

interface ChartRow {
  period: string;
  actual: number | null;
  projected: number | null;
  /** Recharts stacks an Area from a base, so the band is [lower, span]. */
  band: [number, number] | null;
}

/**
 * History and projection share one x-axis, so they must be one row list. The
 * seam period appears in both series, otherwise the solid and dashed lines
 * render as two disconnected fragments.
 */
function toRows(result: ForecastResult): ChartRow[] {
  const rows: ChartRow[] = result.history.map((point) => ({
    period: point.period,
    actual: point.value,
    projected: null,
    band: null,
  }));

  const lastActual = result.history[result.history.length - 1];
  if (lastActual && result.forecast.length > 0) {
    const seam = rows[rows.length - 1];
    if (seam) seam.projected = lastActual.value;
  }

  for (const point of result.forecast) {
    const lower = point.lower ?? point.value;
    const upper = point.upper ?? point.value;
    rows.push({
      period: point.period,
      actual: null,
      projected: point.value,
      band: [lower, Math.max(0, upper - lower)],
    });
  }
  return rows;
}

function confidenceTone(result: ForecastResult): {
  label: string;
  variant: 'default' | 'muted' | 'success' | 'warning' | 'destructive';
} {
  switch (result.confidence) {
    case 'high':
      return { label: 'High confidence', variant: 'success' };
    case 'medium':
      return { label: 'Medium confidence', variant: 'default' };
    case 'low':
      return { label: 'Low confidence', variant: 'warning' };
    default:
      return { label: 'Not forecast', variant: 'muted' };
  }
}

function ForecastTooltip({
  active,
  payload,
  label,
  format,
  intervalLevel,
}: {
  active?: boolean;
  payload?: Array<{ dataKey?: string | number; value?: unknown; payload?: ChartRow }>;
  label?: string | number;
  format: MetricFormat;
  intervalLevel: number;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  const value = row.actual ?? row.projected;
  if (value == null) return null;
  const isProjection = row.actual == null;

  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="font-medium text-popover-foreground">{String(label)}</p>
      <p className="mt-1 text-popover-foreground">
        {formatValue(value, format)}
        {isProjection ? (
          <span className="ml-1 text-muted-foreground">(projected)</span>
        ) : null}
      </p>
      {isProjection && row.band ? (
        <p className="mt-1 text-muted-foreground">
          {Math.round(intervalLevel * 100)}% interval:{' '}
          {formatValue(row.band[0], format)} – {formatValue(row.band[0] + row.band[1], format)}
        </p>
      ) : null}
    </div>
  );
}

function ForecastChart({ result }: { result: ForecastResult }) {
  const rows = React.useMemo(() => toRows(result), [result]);
  const seam = result.history[result.history.length - 1]?.period;

  return (
    <div className="h-[280px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
          <XAxis
            dataKey="period"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 12, fill: 'hsl(var(--muted-foreground))' }}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={72}
            tick={{ fontSize: 12, fill: 'hsl(var(--muted-foreground))' }}
            tickFormatter={(v: number) => formatValue(v, result.format)}
          />
          <Tooltip
            content={
              <ForecastTooltip
                format={result.format}
                intervalLevel={result.interval_level}
              />
            }
          />
          {/* Invisible base so the visible band starts at `lower`. */}
          <Area
            dataKey={(row: ChartRow) => row.band?.[0] ?? null}
            stackId="interval"
            stroke="none"
            fill="none"
            isAnimationActive={false}
            legendType="none"
          />
          <Area
            dataKey={(row: ChartRow) => row.band?.[1] ?? null}
            stackId="interval"
            stroke="none"
            fill="hsl(var(--chart-1))"
            fillOpacity={0.16}
            isAnimationActive={false}
            legendType="none"
          />
          {seam ? (
            <ReferenceLine
              x={seam}
              stroke="hsl(var(--muted-foreground))"
              strokeDasharray="2 4"
              label={{
                value: 'now',
                position: 'top',
                fontSize: 11,
                fill: 'hsl(var(--muted-foreground))',
              }}
            />
          ) : null}
          <Line
            type="monotone"
            dataKey="actual"
            stroke="hsl(var(--chart-1))"
            strokeWidth={2}
            dot={{ r: 2.5 }}
            connectNulls={false}
            isAnimationActive={false}
            name="Observed"
          />
          {/* Dashed, so a projection is never mistaken for a measurement. */}
          <Line
            type="monotone"
            dataKey="projected"
            stroke="hsl(var(--chart-1))"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={{ r: 2.5 }}
            connectNulls={false}
            isAnimationActive={false}
            name="Projected"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** History with no projection: the honest refusal, designed as a real state. */
function RefusedPanel({ result }: { result: ForecastResult }) {
  return (
    <div className="space-y-4">
      <div className="flex gap-3 rounded-md border border-dashed bg-muted/40 p-4">
        <ShieldQuestion className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium">No projection — not enough history</p>
          <p className="text-sm text-muted-foreground">
            {result.caveats[0]
              ?? `Only ${result.n_history} period(s) of ${result.grain} history are available.`}
          </p>
        </div>
      </div>
      {result.history.length > 0 ? (
        <>
          <p className="text-xs text-muted-foreground">
            The observed history is still shown — only the projection is withheld.
          </p>
          <ForecastChart result={result} />
        </>
      ) : null}
    </div>
  );
}

function ForecastBody({ result }: { result: ForecastResult }) {
  const refused = result.forecast.length === 0;
  const tone = confidenceTone(result);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={tone.variant}>{tone.label}</Badge>
        <Badge variant="muted">
          {result.n_history} {result.grain}
          {result.n_history === 1 ? '' : 's'} of history
        </Badge>
        {!refused ? (
          <Badge variant="muted">
            {Math.round(result.interval_level * 100)}% interval
          </Badge>
        ) : null}
      </div>

      {result.headline ? (
        <p className="text-sm text-foreground">{result.headline}</p>
      ) : null}

      {refused ? <RefusedPanel result={result} /> : <ForecastChart result={result} />}

      {/* The method is part of the answer: a reader must be able to judge how
          much the projection is worth, not just read the number. */}
      <div className="space-y-2 border-t pt-3">
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            Method: <span className="font-medium text-foreground">{result.method}</span>
          </span>
        </p>
        {(refused ? result.caveats.slice(1) : result.caveats).map((caveat) => (
          <p key={caveat} className="pl-[1.375rem] text-xs text-muted-foreground">
            {caveat}
          </p>
        ))}
      </div>
    </div>
  );
}

export function ForecastPanel({ defaultMetric = 'revenue' }: { defaultMetric?: string }) {
  const [grain, setGrain] = React.useState<Grain>('quarter');
  const [metric, setMetric] = React.useState(defaultMetric);

  const capabilities = useForecastMetrics(grain);
  const forecast = useForecast(metric, grain, 4);

  const options = capabilities.data?.metrics ?? [];

  return (
    <Card>
      <CardHeader className="gap-3 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-base">
            <TrendingUp className="size-4 text-muted-foreground" aria-hidden />
            Forecast
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            A projection of a governed metric — or the reason there isn&apos;t one.
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="forecast-metric" className="text-2xs uppercase tracking-wide">
              Metric
            </Label>
            <Select
              id="forecast-metric"
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
              className="w-44"
              options={
                options.length === 0
                  ? [{ value: metric, label: metric }]
                  : options.map((option) => ({
                      value: option.metric,
                      label: option.label,
                    }))
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="forecast-grain" className="text-2xs uppercase tracking-wide">
              Grain
            </Label>
            <Select
              id="forecast-grain"
              value={grain}
              onChange={(e) => setGrain(e.target.value as Grain)}
              className="w-32"
              options={GRAINS.map((g) => ({ value: g, label: g }))}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {forecast.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-6 w-56" />
            <Skeleton className="h-[280px] w-full" />
          </div>
        ) : forecast.isError ? (
          <ErrorState error={forecast.error} onRetry={() => void forecast.refetch()} />
        ) : forecast.data ? (
          <ForecastBody result={forecast.data} />
        ) : null}
      </CardContent>
    </Card>
  );
}

export type { ForecastPoint };
