'use client';

import * as React from 'react';
import { Loader2, RefreshCw, Sparkles } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { describeError, EmptyState, ErrorState } from '@/components/states';
import { InsightDigestCard } from '@/components/insight-digest-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useInsights, useRefreshInsights } from '@/lib/hooks';
import { cn } from '@/lib/utils';
import type { Insight, InsightSeverity } from '@/lib/types';

/** Reading order for the feed: most urgent first, ties broken by magnitude. */
const SEVERITY_RANK: Record<InsightSeverity, number> = {
  high: 0,
  medium: 1,
  low: 2,
};

const SEVERITY_META: Array<{
  key: InsightSeverity;
  label: string;
  dot: string;
}> = [
  { key: 'high', label: 'High', dot: 'bg-destructive' },
  { key: 'medium', label: 'Medium', dot: 'bg-warning' },
  { key: 'low', label: 'Low', dot: 'bg-muted-foreground/50' },
];

/**
 * A digest is only useful if the worst thing is at the top. The API returns
 * detection order, so the feed is re-sorted here by severity and then by the
 * size of the move — never by recency, which would bury a serious anomaly.
 */
function briefingOrder(items: Insight[]): Insight[] {
  return [...items].sort((a, b) => {
    const bySeverity = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
    if (bySeverity !== 0) return bySeverity;
    return Math.abs(b.change_pct) - Math.abs(a.change_pct);
  });
}

export function InsightsView() {
  const { toast } = useToast();
  const insights = useInsights();
  const refresh = useRefreshInsights();

  const onRefresh = async () => {
    try {
      const page = await refresh.mutateAsync();
      toast({
        title: 'Digest refreshed',
        description: `${page.total} insight${page.total === 1 ? '' : 's'} detected.`,
        variant: 'success',
      });
    } catch (err) {
      toast({
        title: 'Could not refresh',
        description: describeError(err),
        variant: 'destructive',
      });
    }
  };

  const data = insights.data;
  const items = React.useMemo(
    () => briefingOrder(data?.items ?? []),
    [data?.items],
  );

  const counts = React.useMemo(() => {
    const out: Record<InsightSeverity, number> = { high: 0, medium: 0, low: 0 };
    for (const item of items) out[item.severity] += 1;
    return out;
  }, [items]);

  // The window every anomaly was measured over, read off the data itself.
  const periods = React.useMemo(
    () => [...new Set(items.map((i) => i.period))].sort(),
    [items],
  );

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Insights"
        description="Anomalies and their root causes, surfaced automatically over the governed metrics — no question required."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={refresh.isPending}
          >
            {refresh.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" /> Refreshing…
              </>
            ) : (
              <>
                <RefreshCw className="size-4" /> Refresh
              </>
            )}
          </Button>
        }
      />

      {/* Briefing strip: what was found, how bad, over which period. */}
      {data && items.length > 0 ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border bg-card px-4 py-3 shadow-soft">
          <p className="text-sm text-foreground">
            <span className="text-lg font-semibold tabular-nums">{data.total}</span>{' '}
            <span className="text-muted-foreground">
              anomal{data.total === 1 ? 'y' : 'ies'} flagged
            </span>
          </p>
          <ul className="flex flex-wrap items-center gap-3">
            {SEVERITY_META.filter(({ key }) => counts[key] > 0).map(
              ({ key, label, dot }) => (
                <li
                  key={key}
                  className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <span className={cn('size-2 rounded-full', dot)} aria-hidden />
                  <span className="font-medium tabular-nums text-foreground">
                    {counts[key]}
                  </span>
                  {label}
                </li>
              ),
            )}
          </ul>
          {periods.length ? (
            <span className="text-xs tabular-nums text-muted-foreground">
              Period {periods.join(', ')}
            </span>
          ) : null}
          <Badge
            variant="muted"
            className="ml-auto"
            title="Which store answered this request"
          >
            source: {data.backend}
          </Badge>
        </div>
      ) : null}

      {insights.isLoading ? (
        <div className="space-y-4" aria-busy="true">
          <span className="sr-only">Loading the insight digest…</span>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-44 w-full" />
          ))}
        </div>
      ) : insights.isError ? (
        <ErrorState error={insights.error} onRetry={() => void insights.refetch()} />
      ) : items.length > 0 ? (
        <div className="stagger space-y-4">
          {items.map((insight) => (
            <InsightDigestCard key={insight.id} insight={insight} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No anomalies right now"
          description="Nothing crossed the detection threshold for the latest period. Run a refresh after the next warehouse build to check again."
          icon={<Sparkles className="size-5" />}
          action={
            <Button variant="outline" size="sm" onClick={onRefresh} disabled={refresh.isPending}>
              <RefreshCw className="size-4" /> Run detection now
            </Button>
          }
        />
      )}
    </div>
  );
}
