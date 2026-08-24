'use client';

import * as React from 'react';
import { formatDateTime, timeAgo } from '@/lib/utils';

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
}: {
  iso: string | null | undefined;
  className?: string;
}) {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  if (!iso) return <span className={className}>—</span>;
  const absolute = formatDateTime(iso);
  return (
    <time dateTime={iso} title={absolute} className={className}>
      {mounted ? timeAgo(iso) : absolute}
    </time>
  );
}
