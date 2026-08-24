import {
  CheckCircle2,
  CircleDashed,
  Loader2,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { RunStatus, ServiceStatus } from '@/lib/types';

const CONFIG: Record<
  RunStatus | ServiceStatus,
  { label: string; variant: BadgeProps['variant']; icon: React.ReactNode; spin?: boolean }
> = {
  queued: { label: 'Queued', variant: 'muted', icon: <CircleDashed className="size-3" /> },
  running: { label: 'Running', variant: 'default', icon: <Loader2 className="size-3" />, spin: true },
  success: { label: 'Success', variant: 'success', icon: <CheckCircle2 className="size-3" /> },
  failed: { label: 'Failed', variant: 'destructive', icon: <XCircle className="size-3" /> },
  partial: { label: 'Partial', variant: 'warning', icon: <TriangleAlert className="size-3" /> },
  ok: { label: 'OK', variant: 'success', icon: <CheckCircle2 className="size-3" /> },
  degraded: { label: 'Degraded', variant: 'warning', icon: <TriangleAlert className="size-3" /> },
  error: { label: 'Error', variant: 'destructive', icon: <XCircle className="size-3" /> },
  untested: { label: 'Untested', variant: 'muted', icon: <CircleDashed className="size-3" /> },
};

export function StatusBadge({
  status,
  className,
}: {
  status: RunStatus | ServiceStatus;
  className?: string;
}) {
  const config = CONFIG[status];
  return (
    <Badge variant={config.variant} className={className}>
      <span className={cn(config.spin && 'animate-spin')}>{config.icon}</span>
      {config.label}
    </Badge>
  );
}
