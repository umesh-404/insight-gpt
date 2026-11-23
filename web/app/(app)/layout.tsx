'use client';

import { Loader2 } from 'lucide-react';
import { AppShell } from '@/components/layout/app-shell';
import { Logo } from '@/components/layout/logo';
import { useRequireAuth } from '@/lib/auth';

/**
 * Guarded shell for the (app) route group. In production the session is also
 * validated server-side via the httpOnly refresh cookie (docs/07 §6.2); this
 * client guard is the interactive redirect layer on top of that.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useRequireAuth();

  if (loading || !user) {
    return (
      // The wordmark rather than a bare spinner: this gate sits between the
      // sign-in button and the first screen, and an unbranded spinner there
      // reads as "the page failed" for the second or so that it is up.
      <div
        className="flex min-h-screen flex-col items-center justify-center gap-5 bg-background"
        aria-busy="true"
      >
        <Logo />
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Restoring your session…
        </p>
        <span className="sr-only">Loading…</span>
      </div>
    );
  }

  return <AppShell>{children}</AppShell>;
}
