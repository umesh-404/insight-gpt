'use client';

import * as React from 'react';
import { RotateCcw, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * Route-level error boundary for the signed-in app. Without this, a render-time
 * exception anywhere under (app) takes down the whole shell with a blank page.
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
      <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">
        <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <TriangleAlert className="size-5" aria-hidden />
        </div>
        <h1 className="text-base font-medium text-foreground">
          This screen ran into a problem
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {error.message || 'An unexpected error occurred while rendering.'}
        </p>
        {error.digest ? (
          <p className="mt-2 font-mono text-xs text-muted-foreground">
            Reference: {error.digest}
          </p>
        ) : null}
        <Button variant="outline" size="sm" className="mt-4" onClick={reset}>
          <RotateCcw className="size-4" aria-hidden /> Try again
        </Button>
      </div>
    </div>
  );
}
