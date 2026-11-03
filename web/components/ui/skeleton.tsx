import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Loading placeholder with a travelling sheen.
 *
 * The sheen (rather than a flat opacity pulse) is what makes a skeleton read as
 * "data is on its way" instead of "this element is disabled". It is a pure
 * decoration: the reduced-motion rule in globals.css stops it, leaving a plain
 * muted block.
 */
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('relative overflow-hidden rounded-md bg-muted', className)}
      aria-hidden
      {...props}
    >
      <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-foreground/[0.06] to-transparent" />
    </div>
  );
}

/**
 * Announces that a region is loading, and hides the skeleton geometry from
 * assistive tech.
 *
 * A grid of `aria-hidden` blocks is silence to a screen reader — the page just
 * appears empty until data lands. One polite live region per loading area says
 * it once, in words.
 */
function LoadingRegion({
  label,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { label: string }) {
  return (
    <div role="status" aria-live="polite" className={className} {...props}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

/**
 * Paragraph-shaped placeholder: full-width lines with a short last line, which
 * is what prose actually looks like. A single tall grey block is the tell of a
 * skeleton that was never matched to its content.
 */
function SkeletonText({
  lines = 3,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { lines?: number }) {
  return (
    <div className={cn('space-y-2', className)} {...props}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton
          key={i}
          className={cn('h-3.5', i === lines - 1 ? 'w-[55%]' : 'w-full')}
        />
      ))}
    </div>
  );
}

/**
 * KPI-tile placeholder: small label, large value, optional delta. Matching the
 * value's height is what stops the number landing with a layout jump — the
 * worst loading artefact on a dashboard, because every tile shifts at once.
 */
function SkeletonStat({
  withDelta = true,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { withDelta?: boolean }) {
  return (
    <div className={cn('space-y-2.5', className)} {...props}>
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-7 w-32" />
      {withDelta ? <Skeleton className="h-3 w-16" /> : null}
    </div>
  );
}

/**
 * Table-shaped placeholder. `numericFrom` marks the first right-aligned
 * column so the placeholder bars sit where the figures will, rather than
 * drifting left and then jumping right on load.
 */
function SkeletonTable({
  rows = 5,
  columns = 4,
  numericFrom = 2,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  rows?: number;
  columns?: number;
  numericFrom?: number;
}) {
  return (
    <div className={cn('overflow-hidden rounded-md border', className)} {...props}>
      <div className="flex items-center gap-3 border-b bg-elevated px-3 py-2.5">
        {Array.from({ length: columns }, (_, c) => (
          <Skeleton
            key={c}
            className={cn('h-2.5 flex-1', c >= numericFrom && 'max-w-16')}
          />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div
          key={r}
          className="flex items-center gap-3 border-b px-3 py-3 last:border-b-0"
        >
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton
              key={c}
              className={cn(
                'h-3.5 flex-1',
                c >= numericFrom && 'max-w-16 self-end',
              )}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * Chart-shaped placeholder: a y-axis gutter, plot area and x-axis labels. The
 * axis gutter matters — without it the chart shifts sideways the moment real
 * tick labels are measured.
 */
function SkeletonChart({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('flex h-56 w-full gap-3', className)} {...props}>
      <div className="flex w-10 shrink-0 flex-col justify-between py-1">
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-2.5 w-full" />
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Skeleton className="flex-1 rounded-md" />
        <div className="flex justify-between">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-2.5 w-8" />
          ))}
        </div>
      </div>
    </div>
  );
}

export {
  Skeleton,
  LoadingRegion,
  SkeletonText,
  SkeletonStat,
  SkeletonTable,
  SkeletonChart,
};
