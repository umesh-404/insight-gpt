import {
  CheckCircle2,
  CircleCheck,
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
  /** Hover text. A status word nobody can expand is a status word nobody trusts. */
  hint?: string;
}

/**
 * Pipeline-run and service vocabulary. **Not** report vocabulary — see
 * `ReportStatusBadge` below, and the note on why they must stay apart.
 */
const CONFIG: Record<RunStatus | ServiceStatus, StatusConfig> = {
  queued: {
    label: 'Queued',
    variant: 'muted',
    icon: <CircleDashed className="size-3" aria-hidden />,
    hint: 'Accepted and waiting for a worker slot.',
  },
  running: {
    label: 'Running',
    variant: 'default',
    icon: <Loader2 className="size-3" aria-hidden />,
    spin: true,
    hint: 'In progress right now.',
  },
  success: {
    label: 'Success',
    variant: 'success',
    icon: <CheckCircle2 className="size-3" aria-hidden />,
    hint: 'Finished with every step complete.',
  },
  failed: {
    label: 'Failed',
    variant: 'destructive',
    icon: <XCircle className="size-3" aria-hidden />,
    hint: 'Stopped on an error. Nothing downstream was published.',
  },
  partial: {
    label: 'Partial',
    variant: 'warning',
    icon: <TriangleAlert className="size-3" aria-hidden />,
    hint: 'Finished, but at least one step was skipped or degraded.',
  },
  ok: {
    label: 'OK',
    variant: 'success',
    icon: <CheckCircle2 className="size-3" aria-hidden />,
    hint: 'Last probe succeeded.',
  },
  degraded: {
    label: 'Degraded',
    variant: 'warning',
    icon: <TriangleAlert className="size-3" aria-hidden />,
    hint: 'Reachable, but not answering normally.',
  },
  error: {
    label: 'Error',
    variant: 'destructive',
    icon: <XCircle className="size-3" aria-hidden />,
    hint: 'Last probe failed.',
  },
  untested: {
    label: 'Untested',
    variant: 'muted',
    icon: <CircleDashed className="size-3" aria-hidden />,
    hint: 'Never probed — this is not the same as healthy.',
  },
  fixture: {
    label: 'Fixture',
    variant: 'muted',
    icon: <FlaskConical className="size-3" aria-hidden />,
    hint: 'Served from a local fixture, not a live backend.',
  },
};

/**
 * Report vocabulary. A report is **Ready / Generating / Failed** — never the
 * pipeline word "Success".
 *
 * These two vocabularies are kept in separate components, with separate prop
 * types, on purpose. Routing both through one `StatusBadge` is what produced a
 * report chip reading "Success" on a projector: `success` is not a state a
 * report can be in, and `ready` is not a state a run can be in, but a single
 * lookup table happily renders either word for either thing.
 */
const REPORT_CONFIG = {
  ready: {
    label: 'Ready',
    variant: 'success',
    icon: <CircleCheck className="size-3" aria-hidden />,
    hint: 'Rendered and available to open or export.',
  },
  generating: {
    label: 'Generating',
    variant: 'default',
    icon: <Loader2 className="size-3" aria-hidden />,
    spin: true,
    hint: 'Being rendered now.',
  },
  failed: {
    label: 'Failed',
    variant: 'destructive',
    icon: <XCircle className="size-3" aria-hidden />,
    hint: 'Rendering stopped on an error. Nothing was produced.',
  },
} satisfies Record<string, StatusConfig>;

/** The statuses a report can hold — structurally `Report['status']`. */
export type ReportStatus = keyof typeof REPORT_CONFIG;

/** Words that belong to exactly one vocabulary, used for the dev guard below. */
const RUN_ONLY = new Set(['success', 'partial', 'queued']);
const REPORT_ONLY = new Set(['ready', 'generating']);

function warnOnCrossedVocabulary(
  status: string,
  wrong: Set<string>,
  expected: string,
  got: string,
) {
  if (process.env.NODE_ENV !== 'production' && wrong.has(status)) {
    // eslint-disable-next-line no-console
    console.warn(
      `[status-badge] "${status}" is ${got} vocabulary but was rendered by ${expected}. ` +
        'Runs are success/failed/running/queued/partial; reports are Ready/Generating/Failed.',
    );
  }
}

function Chip({
  config,
  size,
  showIcon,
  className,
}: {
  config: StatusConfig;
  size?: BadgeProps['size'];
  showIcon: boolean;
  className?: string;
}) {
  return (
    <Badge
      variant={config.variant}
      size={size}
      title={config.hint}
      className={cn('whitespace-nowrap', className)}
    >
      {showIcon ? (
        // The icon carries the same meaning as the colour, so the chip is
        // still readable in greyscale and to a colour-blind viewer.
        <span className={cn('inline-flex', config.spin && 'animate-spin')}>
          {config.icon}
        </span>
      ) : null}
      {config.label}
    </Badge>
  );
}

export function StatusBadge({
  status,
  className,
  size,
  showIcon = true,
}: {
  status: RunStatus | ServiceStatus | string;
  className?: string;
  size?: BadgeProps['size'];
  /** Drop the glyph where the row is already extremely dense. */
  showIcon?: boolean;
}) {
  warnOnCrossedVocabulary(status, REPORT_ONLY, 'StatusBadge', 'report');

  // Deployments report statuses this UI has never heard of (e.g. "fixture" from
  // a dev backend). Fall back to a neutral badge rather than crashing on an
  // undefined config lookup.
  const config: StatusConfig = CONFIG[status as RunStatus | ServiceStatus] ?? {
    label: status || 'Unknown',
    variant: 'muted',
    icon: <CircleDashed className="size-3" aria-hidden />,
    hint: 'This deployment reported a status this build does not recognise.',
  };

  return (
    <Chip
      config={config}
      size={size}
      showIcon={showIcon}
      className={cn('capitalize', className)}
    />
  );
}

/**
 * Status chip for a generated report. Takes `Report['status']`, so a pipeline
 * status cannot be passed without a type error at the call site.
 */
export function ReportStatusBadge({
  status,
  className,
  size,
  showIcon = true,
}: {
  status: ReportStatus;
  className?: string;
  size?: BadgeProps['size'];
  showIcon?: boolean;
}) {
  warnOnCrossedVocabulary(status, RUN_ONLY, 'ReportStatusBadge', 'pipeline-run');

  const config: StatusConfig = REPORT_CONFIG[status] ?? {
    label: status || 'Unknown',
    variant: 'muted',
    icon: <CircleDashed className="size-3" aria-hidden />,
    hint: 'This deployment reported a report status this build does not recognise.',
  };

  return <Chip config={config} size={size} showIcon={showIcon} className={className} />;
}
