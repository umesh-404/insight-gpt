'use client';

import * as React from 'react';
import { Loader2, RefreshCw, Sparkles, TriangleAlert } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { describeError, EmptyState, ErrorState } from '@/components/states';
import { InsightDigestCard } from '@/components/insight-digest-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useInsights, useRefreshInsights } from '@/lib/hooks';

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
  const items = data?.items ?? [];
  const highCount = items.filter((i) => i.severity === 'high').length;

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

      {/* Summary strip */}
      {data && items.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <Sparkles className="size-4 text-primary" aria-hidden />
            <span className="font-medium text-foreground">{data.total}</span> anomalies flagged
          </span>
          {highCount > 0 ? (
            <span className="inline-flex items-center gap-1.5">
              <TriangleAlert className="size-4 text-destructive" aria-hidden />
              <span className="font-medium text-foreground">{highCount}</span> high severity
            </span>
          ) : null}
          <Badge variant="muted" className="ml-auto" title="Which store answered this request">
            source: {data.backend}
          </Badge>
        </div>
      ) : null}

      {insights.isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : insights.isError ? (
        <ErrorState error={insights.error} onRetry={() => void insights.refetch()} />
      ) : items.length > 0 ? (
        <div className="space-y-4">
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
