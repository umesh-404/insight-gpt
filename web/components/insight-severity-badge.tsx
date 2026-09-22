import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { InsightSeverity } from '@/lib/types';

const VARIANT: Record<InsightSeverity, BadgeProps['variant']> = {
  high: 'destructive',
  medium: 'warning',
  low: 'muted',
};

const LABEL: Record<InsightSeverity, string> = {
  high: 'High severity',
  medium: 'Medium severity',
  low: 'Low severity',
};

/** Colour-codes an insight's magnitude — severity, not good/bad direction. */
export function InsightSeverityBadge({
  severity,
  className,
}: {
  severity: InsightSeverity;
  className?: string;
}) {
  return (
    <Badge variant={VARIANT[severity]} className={cn('capitalize', className)}>
      {LABEL[severity]}
    </Badge>
  );
}
