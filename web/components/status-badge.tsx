import {
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  Loader2,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { RunStatus, ServiceStatus } from '@/lib/types';

interface StatusConfig {
  label: string;
  variant: BadgeProps['variant'];
  icon: React.ReactNode;
  spin?: boolean;
}

const CONFIG: Record<RunStatus | ServiceStatus, StatusConfig> = {
  queued: { label: 'Queued', variant: 'muted', icon: <CircleDashed className="size-3" /> },
  running: { label: 'Running', variant: 'default', icon: <Loader2 className="size-3" />, spin: true },
  success: { label: 'Success', variant: 'success', icon: <CheckCircle2 className="size-3" /> },
  failed: { label: 'Failed', variant: 'destructive', icon: <XCircle className="size-3" /> },
  partial: { label: 'Partial', variant: 'warning', icon: <TriangleAlert className="size-3" /> },
  ok: { label: 'OK', variant: 'success', icon: <CheckCircle2 className="size-3" /> },
  degraded: { label: 'Degraded', variant: 'warning', icon: <TriangleAlert className="size-3" /> },
  error: { label: 'Error', variant: 'destructive', icon: <XCircle className="size-3" /> },
  untested: { label: 'Untested', variant: 'muted', icon: <CircleDashed className="size-3" /> },
  fixture: { label: 'Fixture', variant: 'muted', icon: <FlaskConical className="size-3" /> },
};

export function StatusBadge({
  status,
  className,
}: {
  status: RunStatus | ServiceStatus | string;
  className?: string;
}) {
  // Deployments report statuses this UI has never heard of (e.g. "fixture" from
  // a dev backend). Fall back to a neutral badge rather than crashing on an
  // undefined config lookup.
  const config: StatusConfig = CONFIG[status as RunStatus | ServiceStatus] ?? {
    label: status || 'Unknown',
    variant: 'muted',
    icon: <CircleDashed className="size-3" />,
  };

  return (
    <Badge variant={config.variant} className={cn('capitalize', className)}>
      <span className={cn(config.spin && 'animate-spin')}>{config.icon}</span>
      {config.label}
    </Badge>
  );
}
