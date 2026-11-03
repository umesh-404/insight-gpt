import { CloudOff, Inbox, Lock, Timer, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/types';

export function EmptyState({
  title,
  description,
  icon,
  action,
  variant = 'panel',
  className,
}: {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  /**
   * `panel` — the dashed box. For an empty *page* or an empty primary region.
   * `inline` — hairline-topped row, no box. For an empty region that already
   * sits inside a card, where a dashed frame inside a bordered frame is just
   * two borders saying the same thing.
   */
  variant?: 'panel' | 'inline';
  className?: string;
}) {
  if (variant === 'inline') {
    return (
      <div className={cn('flex flex-col items-start gap-1 py-6', className)}>
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          <span className="text-muted-foreground">
            {icon ?? <Inbox className="size-4" aria-hidden />}
          </span>
          {title}
        </p>
        {description ? (
          <p className="max-w-prose text-sm text-muted-foreground">
            {description}
          </p>
        ) : null}
        {action ? <div className="mt-2">{action}</div> : null}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-16 text-center',
        className,
      )}
    >
      <div className="mb-3 flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
        {icon ?? <Inbox className="size-5" aria-hidden />}
      </div>
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

interface ErrorPresentation {
  heading: string;
  icon: React.ReactNode;
  tone: 'destructive' | 'warning';
  /**
   * What the reader can actually do about it.
   *
   * This exists so an error with no retry does not end in silence. Callers
   * deliberately withhold `onRetry` for a permanent 403 — a retry that can
   * never succeed is a lie — and without a sentence in its place the panel
   * reads as a broken button rather than a deliberate dead end.
   */
  guidance?: string;
}

/** Different failure classes need different words — and different urgency. */
function present(error: unknown): ErrorPresentation {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return {
        heading: 'Too many requests',
        icon: <Timer className="size-5" aria-hidden />,
        tone: 'warning',
        guidance: 'The rate limit resets on its own — no action needed.',
      };
    }
    if (error.status === 503) {
      return {
        heading: 'A backend service is unavailable',
        icon: <CloudOff className="size-5" aria-hidden />,
        tone: 'warning',
        guidance:
          'The API is up but a dependency it needs is not. Check service health in Settings.',
      };
    }
    if (error.status === 403) {
      return {
        heading: 'You do not have access to this',
        icon: <Lock className="size-5" aria-hidden />,
        tone: 'warning',
        guidance:
          'This needs a role your account does not have. Ask an administrator — retrying will not change the answer.',
      };
    }
  }
  return {
    heading: 'Could not load this view',
    icon: <TriangleAlert className="size-5" aria-hidden />,
    tone: 'destructive',
  };
}

/**
 * Human-readable failure text, shared by panels and toasts so a 429 or a 503
 * reads the same wherever it surfaces.
 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const retryAfter = Number(error.details?.retry_after);
    if (error.status === 429 && Number.isFinite(retryAfter) && retryAfter > 0) {
      return `${error.message} Try again in ${Math.ceil(retryAfter)}s.`;
    }
    return error.message;
  }
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as { message: unknown }).message);
  }
  return 'Something went wrong.';
}

export function ErrorState({
  error,
  onRetry,
  retryLabel = 'Try again',
  guidance,
  variant = 'panel',
  className,
}: {
  error: unknown;
  /**
   * Omit this when retrying cannot possibly help — a permanent 403 is the
   * case this app cares about. The panel then states *why* there is nothing
   * to press instead of just not rendering a button.
   */
  onRetry?: () => void;
  /** Override the retry button's label, e.g. "Reload reports". */
  retryLabel?: string;
  /** Override the next-step sentence chosen from the error's status. */
  guidance?: string;
  /**
   * `panel` — bordered, tinted block. For a failed page or primary region.
   * `inline` — no box. For a failure inside a card that already has a border.
   */
  variant?: 'panel' | 'inline';
  className?: string;
}) {
  const { heading, icon, tone, guidance: defaultGuidance } = present(error);
  const isWarning = tone === 'warning';
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  const nextStep = guidance ?? defaultGuidance;
  // Centred text needs a narrower measure than a left-aligned paragraph before
  // it stops being scannable.
  const measure = variant === 'inline' ? 'max-w-prose' : 'max-w-md';

  const body = (
    <>
      <p className="text-sm font-medium text-foreground">{heading}</p>
      <p className={cn('mt-1 text-sm text-muted-foreground', measure)}>
        {describeError(error)}
      </p>
      {nextStep ? (
        <p className={cn('mt-2 text-sm text-muted-foreground', measure)}>
          {nextStep}
        </p>
      ) : null}
      {requestId ? (
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          Request {requestId}
        </p>
      ) : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </>
  );

  if (variant === 'inline') {
    return (
      <div
        role="alert"
        className={cn('flex items-start gap-3 py-6 text-left', className)}
      >
        <span className={isWarning ? 'mt-0.5 text-warning' : 'mt-0.5 text-destructive'}>
          {icon}
        </span>
        <div className="min-w-0">{body}</div>
      </div>
    );
  }

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border px-6 py-14 text-center',
        isWarning
          ? 'border-warning/30 bg-warning/5'
          : 'border-destructive/30 bg-destructive/5',
        className,
      )}
    >
      <div
        className={cn(
          'mb-3 flex size-11 items-center justify-center rounded-full',
          isWarning ? 'bg-warning/10 text-warning' : 'bg-destructive/10 text-destructive',
        )}
      >
        {icon}
      </div>
      {body}
    </div>
  );
}
