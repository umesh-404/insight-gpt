'use client';

import * as React from 'react';
import { Lock, Moon, Sun } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { ErrorState } from '@/components/states';
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

/**
 * One titled band per concern, separated by a hairline rather than wrapped in a
 * card. Settings is a short read-only page: four bordered boxes stacked down a
 * narrow column spent most of their pixels on their own edges.
 */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  const headingId = React.useId();
  return (
    <section
      aria-labelledby={headingId}
      className="border-t pt-6 first:border-t-0 first:pt-0"
    >
      <h2 id={headingId} className="text-base font-semibold tracking-tight">
        {title}
      </h2>
      {description ? (
        <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      ) : null}
      {children ? <div className="mt-4">{children}</div> : null}
    </section>
  );
}

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

  const selectThemeAt = (index: number) => {
    const option = THEME_OPTIONS[index];
    if (!option) return;
    setTheme(option.value);
    optionRefs.current[index]?.focus();
  };

  const onThemeKeyDown = (event: React.KeyboardEvent) => {
    // Home/End are part of the radiogroup pattern too, not just the arrows.
    if (event.key === 'Home') {
      event.preventDefault();
      selectThemeAt(0);
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      selectThemeAt(THEME_OPTIONS.length - 1);
      return;
    }
    const delta =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    if (!delta) return;
    event.preventDefault();
    selectThemeAt((selectedIndex + delta + THEME_OPTIONS.length) % THEME_OPTIONS.length);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Settings"
        description={
          isAdmin
            ? 'Your profile and appearance, plus live health for every backing service.'
            : 'Your profile and appearance for this account.'
        }
      />

      <div className="space-y-6">
        <Section title="Profile">
          {user ? (
            <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-3">
              <div className="min-w-0 bg-card p-4">
                <dt className="text-xs text-muted-foreground">Name</dt>
                <dd className="mt-1 truncate text-sm font-medium">
                  {displayName(user)}
                </dd>
              </div>
              <div className="min-w-0 bg-card p-4">
                <dt className="text-xs text-muted-foreground">Email</dt>
                <dd
                  className="mt-1 truncate text-sm font-medium"
                  title={user.email || undefined}
                >
                  {user.email || '—'}
                </dd>
              </div>
              <div className="bg-card p-4">
                <dt className="text-xs text-muted-foreground">Role</dt>
                <dd className="mt-1">
                  <Badge variant="muted" className="capitalize">
                    {user.role}
                  </Badge>
                </dd>
              </div>
            </dl>
          ) : (
            <Skeleton className="h-20 w-full" />
          )}
        </Section>

        <Section
          title="Appearance"
          description="Saved to this device only — it does not follow your account to another browser."
        >
          <div
            role="radiogroup"
            aria-label="Theme"
            onKeyDown={onThemeKeyDown}
            className="inline-flex gap-1 rounded-lg border bg-muted/40 p-1"
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
                  // min-h-11/min-w-[7rem] keeps the hit target over 40px in both
                  // axes on a touch screen.
                  'inline-flex min-h-11 min-w-[7rem] items-center justify-center gap-2 rounded-md px-4 text-sm font-medium transition-colors',
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
        </Section>

        {/* Service status (admin only). Mounted conditionally so a non-admin
            never fires the admin-only /status probe and collects a 403. */}
        {isAdmin ? (
          <SystemStatusSection />
        ) : (
          <Section
            title="System status"
            description="Warehouse, index and model-provider health are available to administrators. Ask an administrator if you need the current status."
          >
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Lock className="size-4 shrink-0" aria-hidden />
              Your role does not include service health.
            </p>
          </Section>
        )}
      </div>
    </div>
  );
}

function SystemStatusSection() {
  const status = useStatus();

  return (
    <Section
      title="System status"
      description="Live from the API’s own health probe: the active model provider and every dependency it talks to."
    >
      {status.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : status.isError ? (
        <ErrorState error={status.error} onRetry={() => void status.refetch()} />
      ) : status.data ? (
        <div className="space-y-5">
          {/* Overall verdict and provider identity read first — they are the
              two facts an evaluator asks about from the back of the room. */}
          <div className="rounded-lg border p-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <StatusBadge status={status.data.status} />
              {status.data.version ? (
                <Badge variant="muted">v{status.data.version}</Badge>
              ) : null}
            </div>
            <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="min-w-0">
                <dt className="text-xs text-muted-foreground">Model provider</dt>
                <dd className="mt-0.5 truncate text-sm font-medium">
                  {status.data.llm.provider}
                  {status.data.llm.model ? ` · ${status.data.llm.model}` : ''}
                </dd>
              </div>
              {typeof status.data.uptime_s === 'number' ? (
                <div>
                  <dt className="text-xs text-muted-foreground">Uptime</dt>
                  <dd className="mt-0.5 text-sm font-medium tabular-nums">
                    {formatUptime(status.data.uptime_s)}
                  </dd>
                </div>
              ) : null}
            </dl>
          </div>

          <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-2">
            {Object.entries(status.data.services).map(([name, svc]) => (
              <div
                key={name}
                className="flex items-center justify-between gap-3 bg-card p-4"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium">{serviceLabel(name)}</p>
                  {svc.detail ? (
                    <p className="truncate text-xs text-muted-foreground" title={svc.detail}>
                      {svc.detail}
                    </p>
                  ) : typeof svc.latency_ms === 'number' ? (
                    <p className="text-xs tabular-nums text-muted-foreground">
                      {svc.latency_ms}ms
                    </p>
                  ) : null}
                </div>
                <StatusBadge status={svc.status} />
              </div>
            ))}
          </div>

          {/* The warehouse/index payloads differ per deployment mode, so
              they are rendered as whatever labelled facts arrive. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FactList title="Warehouse" facts={status.data.warehouse} />
            <FactList title="Document index" facts={status.data.index} />
          </div>
        </div>
      ) : null}
    </Section>
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

/**
 * Fact labels arrive as backend keys (`row_count`, `collection_name`). Left raw
 * they read as field names rather than as a reading an evaluator can follow.
 */
function factLabel(label: string): string {
  return label.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

function FactList({
  title,
  facts,
}: {
  title: string;
  facts: Array<{ label: string; value: string }>;
}) {
  return (
    <div className="rounded-lg border p-4">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </p>
      {facts.length ? (
        <dl className="mt-3 divide-y">
          {facts.map((fact) => (
            <div
              key={fact.label}
              className="flex items-baseline justify-between gap-3 py-1.5 first:pt-0 last:pb-0"
            >
              <dt
                className="min-w-0 truncate text-xs text-muted-foreground"
                title={fact.label}
              >
                {factLabel(fact.label)}
              </dt>
              <dd className="shrink-0 text-sm font-medium tabular-nums">
                {factValue(fact.value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          This deployment reported no statistics.
        </p>
      )}
    </div>
  );
}
