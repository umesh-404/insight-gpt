import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn, formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
import type { GoodDirection } from '@/lib/metrics';
import type { MetricSummary, MetricUnit } from '@/lib/types';

function formatMetric(value: number, unit: MetricUnit): string {
  switch (unit) {
    case 'currency':
      return formatCurrency(value);
    case 'ratio':
      return formatPercent(value);
    case 'duration':
      return `${formatNumber(value)}s`;
    default:
      return formatNumber(value);
  }
}

/**
 * Which direction of movement is *good* for this metric.
 *
 * Sign alone is not meaning: revenue falling is bad, return rate falling is
 * good. Every tile states its own direction so the colour is a judgement about
 * the business, not about the arithmetic. The arrow still follows the sign, so
 * the two are never conflated.
 */
export type { GoodDirection } from '@/lib/metrics';

/** Trend line drawn behind the tile — shape only, never a source of numbers. */
function TileSparkline({
  points,
  tone,
}: {
  points: number[];
  tone: 'good' | 'bad' | 'flat';
}) {
  if (points.length < 3) return null;
  const width = 120;
  const height = 34;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const coords = points.map((v, i) => [
    i * step,
    height - ((v - min) / span) * (height - 4) - 2,
  ]);
  const line = coords
    .map(([x, y]) => `${(x ?? 0).toFixed(1)},${(y ?? 0).toFixed(1)}`)
    .join(' ');
  const area = `${line} ${width},${height} 0,${height}`;
  const stroke =
    tone === 'good'
      ? 'hsl(var(--chart-positive))'
      : tone === 'bad'
        ? 'hsl(var(--chart-negative))'
        : 'hsl(var(--muted-foreground))';

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="pointer-events-none absolute bottom-0 right-0 h-9 w-2/5 opacity-45"
      aria-hidden
    >
      <polygon points={area} fill={stroke} opacity={0.12} />
      <polyline
        points={line}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export interface StatTileProps {
  label: string;
  /** Derived client-side from a current + prior-period governed query. */
  summary?: MetricSummary | null;
  /** Direction of movement that counts as an improvement for this metric. */
  goodDirection?: GoodDirection;
  /** Per-period values over the selected window; drives the sparkline only. */
  points?: number[];
  /** Human label for the comparison window, e.g. "vs. prior 30 days". */
  comparisonLabel?: string;
  loading?: boolean;
}

export function StatTile({
  label,
  summary,
  goodDirection = 'up',
  points,
  comparisonLabel = 'vs. prior period',
  loading,
}: StatTileProps) {
  if (loading) {
    return (
      <Card>
        <CardContent className="p-5">
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="mt-3.5 h-8 w-28" />
          <Skeleton className="mt-3.5 h-3.5 w-24" />
        </CardContent>
      </Card>
    );
  }

  // A metric with no rows in the selected window is a real, reportable state —
  // show it rather than spinning forever.
  if (!summary) {
    return (
      <Card>
        <CardContent className="p-5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </p>
          <p className="mt-2 text-3xl font-semibold text-muted-foreground">—</p>
          <p className="mt-2 text-xs text-muted-foreground">No data in range</p>
        </CardContent>
      </Card>
    );
  }

  const { value, unit, delta_pct: delta, previous } = summary;
  const hasDelta = typeof delta === 'number' && Number.isFinite(delta);
  const rose = hasDelta && delta > 0;
  const fell = hasDelta && delta < 0;

  const good =
    goodDirection === 'neutral' ? false : goodDirection === 'up' ? rose : fell;
  const bad =
    goodDirection === 'neutral' ? false : goodDirection === 'up' ? fell : rose;

  // Spoken form of the same judgement the colour encodes, so the meaning does
  // not depend on seeing green or red.
  const verdict = good ? 'improved' : bad ? 'worsened' : 'unchanged';

  return (
    <Card interactive className="relative overflow-hidden">
      <CardContent className="relative p-5">
        {points && points.length > 2 ? (
          <TileSparkline
            points={points}
            tone={good ? 'good' : bad ? 'bad' : 'flat'}
          />
        ) : null}

        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="mt-2 text-3xl font-semibold tabular-nums text-foreground">
          {formatMetric(value, unit)}
        </p>

        {hasDelta ? (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span
              className={cn(
                'inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-medium tabular-nums',
                good && 'bg-success/12 text-success',
                bad && 'bg-destructive/12 text-destructive',
                !good && !bad && 'bg-muted text-muted-foreground',
              )}
            >
              {rose ? (
                <ArrowUpRight className="size-3.5" aria-hidden />
              ) : fell ? (
                <ArrowDownRight className="size-3.5" aria-hidden />
              ) : (
                <Minus className="size-3.5" aria-hidden />
              )}
              {formatPercent(Math.abs(delta))}
              <span className="sr-only"> {verdict}</span>
            </span>
            <span className="text-muted-foreground">{comparisonLabel}</span>
            {typeof previous === 'number' ? (
              <span className="basis-full text-2xs tabular-nums text-muted-foreground">
                was {formatMetric(previous, unit)}
              </span>
            ) : null}
          </div>
        ) : (
          <p className="mt-2.5 text-xs text-muted-foreground">
            No prior-period baseline
          </p>
        )}
      </CardContent>
    </Card>
  );
}
