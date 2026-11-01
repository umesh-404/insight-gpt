'use client';

import * as React from 'react';
import { Monitor, Moon, Sun } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { ErrorState } from '@/components/states';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useAuth } from '@/lib/auth';
import { useTheme } from '@/lib/theme';
import { useStatus } from '@/lib/hooks';
import { cn } from '@/lib/utils';
import { displayName } from '@/lib/types';

const THEME_OPTIONS = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
] as const;

export function SettingsView() {
  const { user, hasRole } = useAuth();
  const { theme, setTheme } = useTheme();
  const isAdmin = hasRole('admin');

  // Roving tabindex: a radiogroup is one stop in the tab order, and the arrow
  // keys move between its options — the group was previously two plain tab
  // stops with dead arrow keys.
  const optionRefs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const selectedIndex = Math.max(
    0,
    THEME_OPTIONS.findIndex((option) => option.value === theme),
  );

  const onThemeKeyDown = (event: React.KeyboardEvent) => {
    const delta =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    if (!delta) return;
    event.preventDefault();
    const next =
      (selectedIndex + delta + THEME_OPTIONS.length) % THEME_OPTIONS.length;
    const option = THEME_OPTIONS[next];
    if (!option) return;
    setTheme(option.value);
    optionRefs.current[next]?.focus();
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Settings"
        description={
          isAdmin
            ? 'Manage your profile and appearance, and review live service status.'
            : 'Manage your profile and appearance for this account.'
        }
      />

      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {user ? (
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Name</dt>
                <dd className="truncate text-sm font-medium">{displayName(user)}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Email</dt>
                <dd className="truncate text-sm font-medium" title={user.email || undefined}>
                  {user.email || '—'}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Role</dt>
                <dd>
                  <Badge variant="muted" className="capitalize">
                    {user.role}
                  </Badge>
                </dd>
              </div>
            </dl>
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </CardContent>
      </Card>

      {/* Appearance */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Appearance</CardTitle>
          <CardDescription>Theme preference is saved to this device.</CardDescription>
        </CardHeader>
        <CardContent>
          <div
            role="radiogroup"
            aria-label="Theme"
            onKeyDown={onThemeKeyDown}
            className="inline-flex rounded-lg border bg-muted/40 p-1"
          >
            {THEME_OPTIONS.map(({ value, label, icon: Icon }, index) => (
              <button
                key={value}
                ref={(node) => {
                  optionRefs.current[index] = node;
                }}
                type="button"
                role="radio"
                aria-checked={theme === value}
                tabIndex={index === selectedIndex ? 0 : -1}
                onClick={() => setTheme(value)}
                className={cn(
                  'inline-flex min-h-11 items-center gap-2 rounded-md px-4 py-1.5 text-sm font-medium transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
                  theme === value
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Service status (admin only). Mounted conditionally so a non-admin
          never fires the admin-only /status probe and collects a 403. */}
      {isAdmin ? (
        <SystemStatusCard />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Monitor className="size-4" aria-hidden /> System status
            </CardTitle>
            <CardDescription>
              Warehouse, index, and model-provider health are available to
              administrators. Ask an administrator if you need the current status.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  );
}

function SystemStatusCard() {
  const status = useStatus();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Monitor className="size-4" aria-hidden /> System status
        </CardTitle>
        <CardDescription>
          Active provider and dependency health from <code>/status</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {status.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : status.isError ? (
          <ErrorState error={status.error} onRetry={() => void status.refetch()} />
        ) : status.data ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 rounded-md border bg-muted/30 px-3 py-2">
                <p className="text-xs text-muted-foreground">Model provider</p>
                <p className="truncate text-sm font-medium">
                  {status.data.llm.provider}
                  {status.data.llm.model ? ` · ${status.data.llm.model}` : ''}
                </p>
              </div>
              <StatusBadge status={status.data.status} />
              {status.data.version ? (
                <Badge variant="muted">v{status.data.version}</Badge>
              ) : null}
              {typeof status.data.uptime_s === 'number' ? (
                <span className="text-xs text-muted-foreground">
                  Up {formatUptime(status.data.uptime_s)}
                </span>
              ) : null}
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {Object.entries(status.data.services).map(([name, svc]) => (
                <div
                  key={name}
                  className="flex items-center justify-between gap-3 rounded-md border p-3"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{serviceLabel(name)}</p>
                    {svc.detail ? (
                      <p className="truncate text-xs text-muted-foreground" title={svc.detail}>
                        {svc.detail}
                      </p>
                    ) : typeof svc.latency_ms === 'number' ? (
                      <p className="text-xs text-muted-foreground">{svc.latency_ms}ms</p>
                    ) : null}
                  </div>
                  <StatusBadge status={svc.status} />
                </div>
              ))}
            </div>

            {/* The warehouse/index payloads differ per deployment mode, so
                they are rendered as whatever labelled facts arrive. */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FactList title="Warehouse" facts={status.data.warehouse} />
              <FactList title="Document index" facts={status.data.index} />
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * Service keys arrive lowercase. Title-casing them turns `llm` into "Llm",
 * which reads as a typo on a projector — the acronyms are spelled out here.
 */
const SERVICE_LABELS: Record<string, string> = {
  llm: 'LLM',
  postgres: 'Postgres',
  qdrant: 'Qdrant',
  worker: 'Worker',
};

function serviceLabel(name: string): string {
  return (
    SERVICE_LABELS[name.toLowerCase()] ??
    name.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
  );
}

/**
 * Process uptime, in the largest two units that matter.
 *
 * `formatDuration` is built for pipeline-run durations and keeps counting in
 * minutes, which renders a two-day-old process as "3526m 32s".
 */
function formatUptime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${total}s`;
}

/** The status payload carries raw booleans; "true" is not a reading. */
function factValue(value: string): string {
  if (value === 'true') return 'Yes';
  if (value === 'false') return 'No';
  return value;
}

function FactList({
  title,
  facts,
}: {
  title: string;
  facts: Array<{ label: string; value: string }>;
}) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      {facts.length ? (
        <dl className="mt-2 space-y-1.5">
          {facts.map((fact) => (
            <div key={fact.label} className="flex items-baseline justify-between gap-3">
              <dt className="min-w-0 truncate text-xs text-muted-foreground">
                {fact.label}
              </dt>
              <dd className="text-sm font-medium tabular-nums">
                {factValue(fact.value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">No statistics reported.</p>
      )}
    </div>
  );
}
