'use client';

import { Loader2 } from 'lucide-react';
import { AppShell } from '@/components/layout/app-shell';
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
      <div
        className="flex min-h-screen items-center justify-center bg-background"
        aria-busy="true"
      >
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
        <span className="sr-only">Loading…</span>
      </div>
    );
  }

  return <AppShell>{children}</AppShell>;
}
