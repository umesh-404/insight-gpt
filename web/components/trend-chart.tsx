'use client';

import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ChartRenderer } from '@/components/chart-renderer';
import type { ChartSpec } from '@/lib/types';

interface TrendChartProps {
  title: string;
  spec?: ChartSpec | null;
  loading?: boolean;
  height?: number;
}

/**
 * Card-wrapped time series driven by a ChartSpec.
 *
 * The title is handed to `ChartRenderer` rather than printed in a `CardHeader`
 * above it: the figure already owns a caption row, so a card header stacked on
 * top produced "Revenue" twice — once as a heading, once as a figcaption — with
 * the chart/table switch orphaned on a line of its own.
 */
export function TrendChart({ title, spec, loading, height = 260 }: TrendChartProps) {
  return (
    <Card>
      <CardContent className="p-4 sm:p-5">
        {loading ? (
          <>
            <Skeleton className="h-5 w-28" />
            <Skeleton className="mt-3 w-full" style={{ height }} />
          </>
        ) : spec ? (
          <ChartRenderer spec={spec} title={title} height={height} />
        ) : (
          <>
            <p className="text-base font-semibold leading-none tracking-tight text-foreground">
              {title}
            </p>
            <div
              className="mt-3 flex items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground"
              style={{ height }}
            >
              No data for the selected range.
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
