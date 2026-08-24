'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ChartRenderer } from '@/components/chart-renderer';
import type { ChartSpec } from '@/lib/types';

interface TrendChartProps {
  title: string;
  spec?: ChartSpec;
  loading?: boolean;
  height?: number;
}

/** Card-wrapped line/area time series driven by a ChartSpec (docs/07 §5). */
export function TrendChart({ title, spec, loading, height = 260 }: TrendChartProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {loading || !spec ? (
          <Skeleton className="h-[260px] w-full" />
        ) : (
          <ChartRenderer spec={spec} height={height} />
        )}
      </CardContent>
    </Card>
  );
}
