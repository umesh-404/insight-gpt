/**
 * Status vocabulary and progress bar for an uploaded dataset.
 *
 * Deliberately *not* routed through `components/status-badge.tsx`. That file
 * keeps one component per vocabulary on purpose — a run is
 * success/failed/running, a report is Ready/Generating/Failed — and a dataset
 * is a third vocabulary: **Queued / Indexing / Ready / Failed**. Passing
 * `ready` to `StatusBadge` would trip its own cross-vocabulary dev warning,
 * and passing `pending` would render the pipeline word "Queued" from a table
 * that does not describe this object.
 */
import { CircleCheck, CircleDashed, Loader2, XCircle } from 'lucide-react';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { DatasetStatus } from '@/lib/types';

interface DatasetStatusConfig {
  label: string;
  variant: BadgeProps['variant'];
  icon: React.ReactNode;
  spin?: boolean;
  hint: string;
}

const CONFIG: Record<DatasetStatus, DatasetStatusConfig> = {
  pending: {
    label: 'Queued',
    variant: 'muted',
    icon: <CircleDashed className="size-3" aria-hidden />,
    hint: 'Accepted and stored, waiting for the indexer. Nothing is searchable yet.',
  },
  indexing: {
    label: 'Indexing',
    variant: 'default',
    icon: <Loader2 className="size-3" aria-hidden />,
    spin: true,
    hint: 'Being parsed, redacted, chunked and embedded. Partial results are not searchable.',
  },
  ready: {
    label: 'Ready',
    variant: 'success',
    icon: <CircleCheck className="size-3" aria-hidden />,
    hint: 'Indexed. Questions in Ask can now retrieve from this file.',
  },
  failed: {
    label: 'Failed',
    variant: 'destructive',
    icon: <XCircle className="size-3" aria-hidden />,
    hint: 'Indexing stopped on an error. Nothing from this file is searchable.',
  },
};

export function DatasetStatusBadge({
  status,
  size,
  className,
}: {
  status: DatasetStatus;
  size?: BadgeProps['size'];
  className?: string;
}) {
  const config = CONFIG[status] ?? CONFIG.indexing;
  return (
    <Badge
      variant={config.variant}
      size={size}
      title={config.hint}
      className={cn('whitespace-nowrap', className)}
    >
      {/* The glyph repeats what the colour says, so the chip survives
          greyscale and colour-blind viewing. */}
      <span className={cn('inline-flex', config.spin && 'animate-spin')}>
        {config.icon}
      </span>
      {config.label}
    </Badge>
  );
}

/** One-line description of a status, for places a chip is too terse. */
export function datasetStatusHint(status: DatasetStatus): string {
  return (CONFIG[status] ?? CONFIG.indexing).hint;
}

/**
 * Determinate progress bar.
 *
 * A native `<progress>` rather than a div: it carries `role="progressbar"` and
 * its value is announced without a hand-rolled ARIA triple. It is always
 * determinate here — there is a real number at every moment, bytes sent while
 * the file is in flight and the indexer's own fraction afterwards — so none of
 * the indeterminate-animation machinery is needed, and a bar that *could* be
 * mistaken for a decorative spinner never appears.
 *
 * `appearance-none` is mandatory before any of the fill styling applies, and
 * the WebKit and Firefox fill pseudo-elements must stay in separate rules;
 * Tailwind emits one rule per utility, which is exactly that.
 */
export function DatasetProgress({
  value,
  label,
  className,
}: {
  /** 0..1. */
  value: number;
  /** Accessible name — what this bar is measuring. */
  label: string;
  className?: string;
}) {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  return (
    <progress
      value={clamped}
      max={1}
      aria-label={label}
      className={cn(
        'h-1.5 w-full appearance-none rounded-full border-0 border-solid bg-muted [overflow:clip]',
        '[&::-webkit-progress-bar]:bg-transparent',
        '[&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary',
        '[&::-webkit-progress-value]:transition-[inline-size] [&::-webkit-progress-value]:duration-300',
        '[&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-primary',
        className,
      )}
    />
  );
}

/** The percentage, as text, for the figure beside a bar. */
export function formatProgressPercent(value: number): string {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  return `${Math.round(clamped * 100)}%`;
}
