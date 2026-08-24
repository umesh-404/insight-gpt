'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ChartRenderer } from '@/components/chart-renderer';
import type { ChartSpec } from '@/lib/types';

interface TrendChartProps {
  title: string;
  spec?: ChartSpec | null;
  loading?: boolean;
  height?: number;
}

/** Card-wrapped time series driven by a ChartSpec. */
export function TrendChart({ title, spec, loading, height = 260 }: TrendChartProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[260px] w-full" />
        ) : spec ? (
          <ChartRenderer spec={spec} height={height} />
        ) : (
          <div
            className="flex items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground"
            style={{ height }}
          >
            No data for the selected range.
          </div>
        )}
      </CardContent>
    </Card>
  );
}
