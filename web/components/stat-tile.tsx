import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  cn,
  formatCurrency,
  formatNumber,
  formatPercent,
  isMaterialChange,
} from '@/lib/utils';
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
  const height = 36;
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
  /**
   * `hero` is the one metric the page is *about*. It gets the display figure and
   * a wider box; everything else on the row stays a supporting tile. Four tiles
   * at identical weight make the reader choose where to look, which is the job
   * the dashboard is supposed to have already done.
   */
  emphasis?: 'hero' | 'default';
}

export function StatTile({
  label,
  summary,
  goodDirection = 'up',
  points,
  comparisonLabel = 'vs. prior period',
  loading,
  emphasis = 'default',
}: StatTileProps) {
  const hero = emphasis === 'hero';
  // Proportional figures on the display value: `tabular-nums` pads narrow digits
  // and makes a large "1" sit in a visible gutter. Alignment only matters where
  // numbers stack — the delta, the baseline, table rows and axis ticks.
  //
  // The supporting figure only reaches its full size at `xl`. The KPI band
  // goes to five columns at `lg`, and in the 1024-1279px band a supporting
  // column is ~86px wide while a seven-glyph currency figure (₹90,335) needs
  // ~115px at 30px - so the value was shorn off mid-digit at exactly the
  // resolution a lecture-theatre projector tends to run. Stepping down to
  // 24px there costs nothing: the value still outranks its own label, which
  // is what the hierarchy is for.
  const valueClass = hero
    ? 'text-3xl sm:text-4xl font-semibold leading-none tracking-tight text-foreground'
    : 'text-2xl xl:text-3xl font-semibold leading-none tracking-tight text-foreground';
  const pad = hero ? 'p-5 sm:p-6' : 'p-5';

  if (loading) {
    return (
      <Card>
        <CardContent className={pad}>
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className={cn('mt-4', hero ? 'h-9 w-40' : 'h-8 w-28')} />
          <Skeleton className="mt-4 h-3.5 w-24" />
        </CardContent>
      </Card>
    );
  }

  // A metric with no rows in the selected window is a real, reportable state —
  // show it rather than spinning forever, and never as a confident zero.
  if (!summary) {
    return (
      <Card>
        <CardContent className={pad}>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </p>
          <p className={cn('mt-4 text-muted-foreground', valueClass)}>—</p>
          <p className="mt-4 text-xs text-muted-foreground">No data in range</p>
        </CardContent>
      </Card>
    );
  }

  const { value, unit, delta_pct: delta, previous } = summary;
  const hasDelta = typeof delta === 'number' && Number.isFinite(delta);
  const deltaValue = hasDelta ? delta : 0;
  // A move that rounds to "0.0%" is not a direction. Below the floor the tile
  // reports flat rather than painting an arrow and a verdict on drift.
  const moved = isMaterialChange(delta);
  const rose = moved && deltaValue > 0;
  const fell = moved && deltaValue < 0;

  const good =
    goodDirection === 'neutral' ? false : goodDirection === 'up' ? rose : fell;
  const bad =
    goodDirection === 'neutral' ? false : goodDirection === 'up' ? fell : rose;

  // Spoken form of the same judgement the colour encodes, so the meaning does
  // not depend on seeing green or red.
  const verdict = good ? 'improved' : bad ? 'worsened' : 'unchanged';

  return (
    <Card interactive className="relative overflow-hidden">
      <CardContent className={cn('relative', pad)}>
        {points && points.length > 2 ? (
          <TileSparkline
            points={points}
            tone={good ? 'good' : bad ? 'bad' : 'flat'}
          />
        ) : null}

        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className={cn('mt-4', valueClass)}>{formatMetric(value, unit)}</p>

        {hasDelta ? (
          <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            {/* Colour sits on the glyph and the figure, not on a filled pill:
                a row of four tinted capsules reads as a status board even when
                three of the four movements are unremarkable. */}
            <span
              className={cn(
                'inline-flex items-center gap-1 font-semibold tabular-nums',
                good && 'text-success',
                bad && 'text-destructive',
                !good && !bad && 'text-muted-foreground',
              )}
            >
              {rose ? (
                <ArrowUpRight className="size-4" aria-hidden />
              ) : fell ? (
                <ArrowDownRight className="size-4" aria-hidden />
              ) : (
                <Minus className="size-4" aria-hidden />
              )}
              {moved ? formatPercent(Math.abs(deltaValue)) : 'Flat'}
              <span className="sr-only"> {verdict}</span>
            </span>
            <span className="text-muted-foreground">{comparisonLabel}</span>
            {typeof previous === 'number' ? (
              <span className="basis-full text-2xs tabular-nums text-muted-foreground">
                was {formatMetric(previous, unit)}
                {moved ? null : ' · under 0.1% change'}
              </span>
            ) : null}
          </div>
        ) : (
          <p className="mt-4 text-xs text-muted-foreground">
            No prior-period baseline
          </p>
        )}
      </CardContent>
    </Card>
  );
}
