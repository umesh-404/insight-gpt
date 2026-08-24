'use client';

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
import { cn, formatDuration } from '@/lib/utils';
import { displayName } from '@/lib/types';

export function SettingsView() {
  const { user, hasRole } = useAuth();
  const { theme, setTheme } = useTheme();
  const isAdmin = hasRole('admin');
  const status = useStatus();

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Settings"
        description="Manage your profile, appearance, and — for admins — service status."
      />

      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {user ? (
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">Name</dt>
                <dd className="text-sm font-medium">{displayName(user)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Email</dt>
                <dd className="text-sm font-medium">{user.email || '—'}</dd>
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
            className="inline-flex rounded-lg border bg-muted/40 p-1"
          >
            {(
              [
                { value: 'light', label: 'Light', icon: Sun },
                { value: 'dark', label: 'Dark', icon: Moon },
              ] as const
            ).map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={theme === value}
                onClick={() => setTheme(value)}
                className={cn(
                  'inline-flex items-center gap-2 rounded-md px-4 py-1.5 text-sm font-medium transition-colors',
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

      {/* Service status (admin only) */}
      {isAdmin ? (
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
                  <div className="rounded-md border bg-muted/30 px-3 py-2">
                    <p className="text-xs text-muted-foreground">Model provider</p>
                    <p className="text-sm font-medium">
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
                      Up {formatDuration(status.data.uptime_s * 1000)}
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
                        <p className="text-sm font-medium capitalize">
                          {name.replace(/_/g, ' ')}
                        </p>
                        {svc.detail ? (
                          <p className="truncate text-xs text-muted-foreground">
                            {svc.detail}
                          </p>
                        ) : typeof svc.latency_ms === 'number' ? (
                          <p className="text-xs text-muted-foreground">
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
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <FactList title="Warehouse" facts={status.data.warehouse} />
                  <FactList title="Document index" facts={status.data.index} />
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
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
              <dt className="text-xs text-muted-foreground">{fact.label}</dt>
              <dd className="text-sm font-medium tabular-nums">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">No statistics reported.</p>
      )}
    </div>
  );
}
