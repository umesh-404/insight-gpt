'use client';

import * as React from 'react';
import Link from 'next/link';
import { RotateCcw, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * Route-level error boundary for the signed-in app. Without this, a render-time
 * exception anywhere under (app) takes down the whole shell with a blank page.
 *
 * Two actions, because `reset()` alone is a dead end: a component that throws
 * on every render re-throws immediately, and the user is left clicking a
 * button that visibly does nothing. The second action leaves the broken route.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    // Surface the stack in the console for local debugging.
    console.error('Unhandled error in the app shell:', error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <div className="w-full max-w-md rounded-lg border border-destructive/40 bg-card p-6 shadow-soft">
        <div className="flex size-10 items-center justify-center rounded-md bg-destructive-subtle text-destructive">
          <TriangleAlert className="size-5" aria-hidden />
        </div>
        <h1 className="mt-4 text-lg font-semibold text-foreground">
          This screen ran into a problem
        </h1>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          {error.message ||
            'An unexpected error occurred while rendering this screen.'}
        </p>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          Nothing was changed by the failure. The rest of the workspace is still
          usable.
        </p>
        {/* The digest is the only handle on a production stack trace, so it is
            shown in full and selectable rather than truncated for tidiness. */}
        {error.digest ? (
          <p className="mt-4 break-all rounded-md bg-muted px-2.5 py-1.5 font-mono text-xs text-muted-foreground">
            Reference {error.digest}
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Button variant="default" size="sm" onClick={reset}>
            <RotateCcw className="size-4" aria-hidden /> Retry this screen
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link href="/ask">Go to Ask</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
