'use client';

import * as React from 'react';
import { cn, formatDateTime, timeAgo } from '@/lib/utils';

/**
 * Hydration-safe relative timestamp.
 *
 * "3m ago" depends on `Date.now()`, which differs between the server render and
 * the browser render and would trigger a hydration mismatch. So the first paint
 * shows the absolute (UTC-pinned, deterministic) timestamp, and the relative
 * form takes over once mounted. The absolute value stays available as a
 * tooltip and to assistive tech via `<time datetime>`.
 */
export function RelativeTime({
  iso,
  className,
  emptyLabel = '—',
}: {
  iso: string | null | undefined;
  className?: string;
  /**
   * What to show when there is no timestamp. Defaults to an em dash: a missing
   * time is unknown, and "never" or "just now" would both be claims the data
   * does not support.
   */
  emptyLabel?: string;
}) {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  if (!iso) {
    return (
      <span className={cn('text-muted-foreground', className)}>{emptyLabel}</span>
    );
  }
  const absolute = formatDateTime(iso);
  return (
    <time
      dateTime={iso}
      title={absolute}
      // Tabular figures keep a column of timestamps from shearing, and
      // `whitespace-nowrap` keeps "2 days ago" off two lines in a table cell.
      className={cn('whitespace-nowrap tabular-nums', className)}
    >
      {mounted ? timeAgo(iso) : absolute}
    </time>
  );
}
