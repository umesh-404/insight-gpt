import { cn } from '@/lib/utils';
import type { InsightSeverity } from '@/lib/types';

/**
 * Severity is carried by a marker plus a word, not by a filled capsule.
 *
 * A digest of ten anomalies with a solid red or amber pill on each one turns
 * the page into a traffic light: every card shouts, so the ordering the feed
 * worked out stops being readable. The dot keeps the status hue (and is never
 * the only signal — the word is right beside it), while the text stays in
 * ordinary muted ink so rows of cards read as a list again.
 */
const DOT: Record<InsightSeverity, string> = {
  high: 'bg-destructive',
  medium: 'bg-warning',
  low: 'bg-muted-foreground/45',
};

const LABEL: Record<InsightSeverity, string> = {
  high: 'High severity',
  medium: 'Medium severity',
  low: 'Low severity',
};

/** Marks an insight's magnitude — severity, not good/bad direction. */
export function InsightSeverityBadge({
  severity,
  className,
}: {
  severity: InsightSeverity;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground',
        className,
      )}
    >
      <span
        className={cn('size-2 shrink-0 rounded-full', DOT[severity])}
        aria-hidden
      />
      {LABEL[severity]}
    </span>
  );
}
