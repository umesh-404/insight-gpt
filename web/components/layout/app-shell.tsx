'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LogOut, Menu, Search, Settings, X } from 'lucide-react';
import { Logo } from './logo';
import { ThemeToggle } from './theme-toggle';
import { NAV_GROUPS, NAV_ITEMS, navItemForPath, type NavItem } from './nav';
import {
  CommandPalette,
  openCommandPalette,
  useCommandShortcutHint,
} from '@/components/command-palette';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useAuth } from '@/lib/auth';
import { displayName } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * One nav row.
 *
 * The active state is carried by four signals at once — a solid left marker,
 * the accent colour, a heavier weight, and an opaque tint — because on a
 * projector in a lit room the tint alone is the first thing to disappear, and
 * colour alone is not a legitimate state indicator anyway.
 *
 * The tint is `bg-primary-subtle`, not `bg-primary/10`: the sidebar sits on
 * `card` on desktop and inside a drawer on mobile, and an alpha tint changes
 * value (and contrast) with whatever is behind it.
 */
function NavRow({
  item,
  active,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={cn(
        // 40px tall: the minimum comfortable touch target, and it is the same
        // row height on desktop so the drawer is not a different component.
        'group relative flex min-h-10 items-center gap-2.5 rounded-md py-2 pl-4 pr-3 text-sm transition-colors duration-150',
        'before:absolute before:left-0 before:top-1/2 before:h-5 before:w-[3px] before:-translate-y-1/2 before:rounded-full before:transition-colors before:content-[""]',
        active
          ? 'bg-primary-subtle font-semibold text-primary-subtle-foreground before:bg-primary'
          : 'font-medium text-muted-foreground before:bg-transparent hover:bg-accent hover:text-foreground',
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
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

/**
 * The grouped primary nav.
 *
 * A group whose every item is hidden by the role gate renders nothing at all —
 * a viewer must not see an empty "Data platform" heading advertising a section
 * they cannot reach.
 */
function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { hasRole } = useAuth();
  const current = navItemForPath(pathname);

  return (
    <nav className="flex flex-1 flex-col gap-5 px-3" aria-label="Primary">
      {NAV_GROUPS.map((group) => {
        const items = NAV_ITEMS.filter(
          (item) => item.group === group.id && hasRole(item.minRole),
        );
        if (items.length === 0) return null;
        return (
          <div key={group.id} className="flex flex-col gap-0.5">
            {group.label ? (
              <p className="px-4 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-muted-foreground">
                {group.label}
              </p>
            ) : null}
            {items.map((item) => (
              <NavRow
                key={item.href}
                item={item}
                active={current?.href === item.href}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        );
      })}
    </nav>
  );
}

/** Settings lives next to the account, not among the work. */
function SettingsRow({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const active = navItemForPath(pathname)?.href === '/settings';
  return (
    <Link
      href="/settings"
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex min-h-10 items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
        active
          ? 'bg-primary-subtle font-semibold text-primary-subtle-foreground'
          : 'font-medium text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      <Settings className="size-4 shrink-0" aria-hidden />
      Settings
    </Link>
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
    <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-subtle text-2xs font-semibold text-primary-subtle-foreground">
        {initials}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium leading-tight">{label}</p>
        {/* The role is load-bearing in this app — half the screens behave
            differently because of it — so it is shown, not hidden in a menu. */}
        <Badge variant="muted" className="mt-1 capitalize">
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

/** Sidebar body, shared by the desktop rail and the mobile drawer. */
function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto py-4">
        <NavLinks onNavigate={onNavigate} />
      </div>
      <div className="border-t p-2">
        <SettingsRow onNavigate={onNavigate} />
        <UserCard />
      </div>
    </>
  );
}

/**
 * The mobile navigation drawer, as a real `<dialog>`.
 *
 * It used to be a `div` with `role="dialog" aria-modal="true"` and nothing
 * behind the claim: Tab walked straight out of it into the header and the main
 * column underneath, the page scrolled behind the dim layer, and closing it
 * dropped focus back to the document body, so a keyboard user landed nowhere.
 *
 * `showModal()` is the fix rather than a hand-rolled trap: the browser makes
 * the rest of the document inert, contains Tab and Shift-Tab, handles Escape,
 * and returns focus to the element that opened it. The scroll lock is CSS
 * (`overscroll-behavior` on the dialog and its `::backdrop`, in globals.css) —
 * deliberately not `overflow: hidden` on `body`, which removes the root
 * scrollbar and shifts the entire layout sideways as the drawer opens.
 */
function MobileNavDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const ref = React.useRef<HTMLDialogElement | null>(null);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  // Crossing into the desktop breakpoint reveals the permanent sidebar, which
  // would leave an invisible modal holding focus captive over a usable page.
  React.useEffect(() => {
    if (!open) return;
    const desktop = window.matchMedia('(min-width: 1024px)');
    const onChange = () => {
      if (desktop.matches) onClose();
    };
    desktop.addEventListener('change', onChange);
    return () => desktop.removeEventListener('change', onChange);
  }, [open, onClose]);

  return (
    <dialog
      ref={ref}
      aria-label="Navigation"
      className="lg:hidden"
      // Fires for Escape and for the mobile back gesture as well as for
      // close(), so React state can never drift out of sync with the dialog.
      onClose={onClose}
      // Light dismiss. `closedby="any"` cannot do this job here because the
      // dialog box *is* the viewport, so the browser considers every click to
      // be inside it; a target check is exact instead, since the element has
      // no padding of its own for a stray click to land on.
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="flex h-full w-[17rem] max-w-[85vw] animate-drawer-in flex-col border-r bg-card">
        <div className="flex h-16 shrink-0 items-center justify-between border-b px-4">
          <Logo />
          <Button
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X className="size-5" />
          </Button>
        </div>
        <SidebarBody onNavigate={onClose} />
      </div>
    </dialog>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const shortcutHint = useCommandShortcutHint();
  const pathname = usePathname();
  const current = navItemForPath(pathname);

  const closeDrawer = React.useCallback(() => setMobileOpen(false), []);

  return (
    <div className="flex min-h-screen max-w-full overflow-x-hidden bg-background">
      {/* Desktop sidebar. `bg-card` against a cooler `bg-background` main
          column is what separates the two — one hairline, no shadow. */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r bg-card lg:flex">
        <div className="flex h-16 shrink-0 items-center border-b px-5">
          <Link
            href="/ask"
            aria-label="InsightGPT home"
            className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          >
            <Logo />
          </Link>
        </div>
        <SidebarBody />
      </aside>

      <MobileNavDrawer open={mobileOpen} onClose={closeDrawer} />

      {/* Main column */}
      <div className="flex min-w-0 max-w-full flex-1 flex-col overflow-x-hidden">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur sm:gap-3 sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
            aria-expanded={mobileOpen}
          >
            <Menu className="size-5" />
          </Button>
          <div className="lg:hidden">
            <Logo />
          </div>

          {/* "You are here", desktop only — the mobile header already shows
              the wordmark, and the screen's own <h1> is a scroll away at most.
              On desktop the sidebar is the primary cue and this is the
              confirmation, which is why it is quiet rather than a title. */}
          {current ? (
            <p className="hidden min-w-0 items-center gap-2 text-sm font-medium text-muted-foreground lg:flex">
              <current.icon className="size-4 shrink-0" aria-hidden />
              <span className="truncate">{current.label}</span>
            </p>
          ) : null}

          {/* Reads as a search field, behaves as the palette trigger — the
              shortcut is only discoverable if something on screen shows it. */}
          <button
            type="button"
            onClick={openCommandPalette}
            className="ml-auto hidden h-9 w-64 items-center gap-2 rounded-md border bg-card px-3 text-sm text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground sm:flex"
          >
            <Search className="size-4 shrink-0" aria-hidden />
            <span className="flex-1 text-left">Search or jump to…</span>
            <kbd className="shrink-0 rounded border bg-muted px-1.5 py-0.5 text-2xs font-medium">
              {shortcutHint}
            </kbd>
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
        <main
          id="main-content"
          className="min-h-0 min-w-0 max-w-full flex-1 overflow-x-hidden"
        >
          {children}
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}
