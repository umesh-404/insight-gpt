'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LogOut, Menu, Search, X } from 'lucide-react';
import { Logo } from './logo';
import { ThemeToggle } from './theme-toggle';
import { NAV_ITEMS } from './nav';
import { CommandPalette, openCommandPalette } from '@/components/command-palette';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useAuth } from '@/lib/auth';
import { displayName } from '@/lib/types';
import { cn } from '@/lib/utils';

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { hasRole } = useAuth();
  return (
    <nav className="flex flex-1 flex-col gap-0.5 px-3" aria-label="Primary">
      {NAV_ITEMS.filter((item) => hasRole(item.minRole)).map((item) => {
        const active =
          pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? 'page' : undefined}
            title={item.description}
            className={cn(
              // The active item also gets a solid left marker, so the current
              // section is identifiable without relying on the tint alone.
              'group relative flex items-center gap-3 rounded-md py-2 pl-4 pr-3 text-sm font-medium transition-colors duration-150',
              'before:absolute before:left-0 before:top-1/2 before:h-4 before:w-[3px] before:-translate-y-1/2 before:rounded-full before:transition-colors before:content-[""]',
              active
                ? 'bg-primary/10 text-primary before:bg-primary'
                : 'text-muted-foreground before:bg-transparent hover:bg-accent hover:text-foreground',
            )}
          >
            <Icon
              className={cn(
                'size-4 shrink-0 transition-colors',
                active
                  ? 'text-primary'
                  : 'text-muted-foreground group-hover:text-foreground',
              )}
              aria-hidden
            />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function UserCard() {
  const { user, logout } = useAuth();
  const router = useRouter();

  // Sign-out must always land on /login, even if the server call fails —
  // otherwise a network blip leaves an unhandled rejection and a dead shell.
  const onLogout = () => {
    void logout()
      .catch(() => undefined)
      .finally(() => router.replace('/login'));
  };

  if (!user) {
    return (
      <div className="flex items-center gap-3 px-3 py-2">
        <Skeleton className="size-8 rounded-full" />
        <Skeleton className="h-4 w-24" />
      </div>
    );
  }

  // Defensive: `name` and even `email` can be missing on a partial user record,
  // so the label comes from a total helper rather than raw field access.
  const label = displayName(user);
  const initials =
    label
      .split(/[\s._-]+/)
      .map((part) => part[0])
      .filter(Boolean)
      .join('')
      .slice(0, 2)
      .toUpperCase() || 'U';

  return (
    <div className="flex items-center gap-3 rounded-md px-2 py-2">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
        {initials}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{label}</p>
        <Badge variant="muted" className="mt-0.5 capitalize">
          {user.role}
        </Badge>
      </div>
      <Button
        variant="ghost"
        size="icon"
        onClick={onLogout}
        aria-label="Sign out"
        title="Sign out"
      >
        <LogOut className="size-4" />
      </Button>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = React.useState(false);

  // Escape closes the drawer — expected of any modal overlay, and the only way
  // out for a keyboard user who opened it with the menu button.
  React.useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

  return (
    <div className="flex min-h-screen bg-background">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r bg-card lg:flex">
        <div className="flex h-16 items-center border-b px-5">
          <Link
            href="/ask"
            aria-label="InsightGPT home"
            className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          >
            <Logo />
          </Link>
        </div>
        <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto py-4">
          <p className="px-4 pb-2 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
            Workspace
          </p>
          <NavLinks />
        </div>
        <div className="border-t p-3">
          <UserCard />
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
          <div
            className="absolute inset-0 bg-foreground/40 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute left-0 top-0 flex h-full w-72 max-w-[85%] animate-fade-in flex-col border-r bg-card">
            <div className="flex h-16 items-center justify-between px-5">
              <Logo />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setMobileOpen(false)}
                aria-label="Close menu"
              >
                <X className="size-5" />
              </Button>
            </div>
            <div className="flex flex-1 flex-col overflow-y-auto py-4">
              <NavLinks onNavigate={() => setMobileOpen(false)} />
            </div>
            <div className="border-t p-3">
              <UserCard />
            </div>
          </aside>
        </div>
      ) : null}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="size-5" />
          </Button>
          <div className="lg:hidden">
            <Logo />
          </div>

          {/* Reads as a search field, behaves as the palette trigger — the
              shortcut is only discoverable if something on screen shows it. */}
          <button
            type="button"
            onClick={openCommandPalette}
            className="ml-auto hidden h-9 w-64 items-center gap-2 rounded-lg border bg-card px-3 text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground sm:flex"
          >
            <Search className="size-4 shrink-0" aria-hidden />
            <span className="flex-1 text-left">Search or jump to…</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-2xs">⌘K</kbd>
          </button>

          <div className="ml-auto flex items-center gap-1 sm:ml-0">
            <Button
              variant="ghost"
              size="icon"
              className="sm:hidden"
              onClick={openCommandPalette}
              aria-label="Search or jump to a screen"
            >
              <Search className="size-5" />
            </Button>
            <ThemeToggle />
          </div>
        </header>
        <main id="main-content" className="min-w-0 flex-1">
          {children}
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}
