import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn, formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
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

export interface StatTileProps {
  label: string;
  /** Derived client-side from a current + prior-period governed query. */
  summary?: MetricSummary | null;
  /** For return rate, a rise is bad — invert the color semantics. */
  invertDelta?: boolean;
  loading?: boolean;
}

export function StatTile({ label, summary, invertDelta, loading }: StatTileProps) {
  if (loading) {
    return (
      <Card>
        <CardContent className="p-5">
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="mt-3 h-8 w-28" />
          <Skeleton className="mt-3 h-3.5 w-24" />
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
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-tight text-muted-foreground">
            —
          </p>
          <p className="mt-2 text-xs text-muted-foreground">No data in range</p>
        </CardContent>
      </Card>
    );
  }

  const { value, unit, delta_pct: delta } = summary;
  const hasDelta = typeof delta === 'number' && Number.isFinite(delta);
  const positive = hasDelta && delta > 0;
  const negative = hasDelta && delta < 0;
  // "Good" direction depends on the metric (revenue up = good; returns up = bad).
  const good = invertDelta ? negative : positive;
  const bad = invertDelta ? positive : negative;

  return (
    <Card className="transition-shadow hover:shadow-md">
      <CardContent className="p-5">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">
          {formatMetric(value, unit)}
        </p>
        {hasDelta ? (
          <div className="mt-2 flex items-center gap-1.5 text-sm">
            <span
              className={cn(
                'inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-medium',
                good && 'bg-success/12 text-success',
                bad && 'bg-destructive/12 text-destructive',
                !good && !bad && 'bg-muted text-muted-foreground',
              )}
            >
              {positive ? (
                <ArrowUpRight className="size-3.5" />
              ) : negative ? (
                <ArrowDownRight className="size-3.5" />
              ) : (
                <Minus className="size-3.5" />
              )}
              {formatPercent(Math.abs(delta))}
            </span>
            <span className="text-muted-foreground">vs. prior period</span>
          </div>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">
            No prior-period baseline
          </p>
        )}
      </CardContent>
    </Card>
  );
}
