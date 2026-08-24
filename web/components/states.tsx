import { CloudOff, Inbox, Lock, Timer, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/types';

export function EmptyState({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-16 text-center">
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
}

/** Different failure classes need different words — and different urgency. */
function present(error: unknown): ErrorPresentation {
  if (error instanceof ApiError) {
    if (error.status === 429) {
      return {
        heading: 'Too many requests',
        icon: <Timer className="size-5" aria-hidden />,
        tone: 'warning',
      };
    }
    if (error.status === 503) {
      return {
        heading: 'A backend service is unavailable',
        icon: <CloudOff className="size-5" aria-hidden />,
        tone: 'warning',
      };
    }
    if (error.status === 403) {
      return {
        heading: 'You do not have access to this',
        icon: <Lock className="size-5" aria-hidden />,
        tone: 'warning',
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
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const { heading, icon, tone } = present(error);
  const isWarning = tone === 'warning';
  const requestId = error instanceof ApiError ? error.requestId : undefined;

  return (
    <div
      role="alert"
      className={
        isWarning
          ? 'flex flex-col items-center justify-center rounded-lg border border-warning/30 bg-warning/5 px-6 py-14 text-center'
          : 'flex flex-col items-center justify-center rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-14 text-center'
      }
    >
      <div
        className={
          isWarning
            ? 'mb-3 flex size-11 items-center justify-center rounded-full bg-warning/10 text-warning'
            : 'mb-3 flex size-11 items-center justify-center rounded-full bg-destructive/10 text-destructive'
        }
      >
        {icon}
      </div>
      <p className="text-sm font-medium text-foreground">{heading}</p>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">
        {describeError(error)}
      </p>
      {requestId ? (
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          Request {requestId}
        </p>
      ) : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}
