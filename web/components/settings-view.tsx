'use client';

import { Monitor, Moon, Sun } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
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
import { cn, formatDateTime, formatNumber } from '@/lib/utils';
import type { ServiceStatus } from '@/lib/types';

export function SettingsView() {
  const { user, hasRole } = useAuth();
  const { theme, setTheme } = useTheme();
  const isAdmin = hasRole('admin');
  const status = useStatus();

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Settings"
        description="Manage your profile, appearance, and — for admins — provider status."
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
                <dd className="text-sm font-medium">{user.name}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Email</dt>
                <dd className="text-sm font-medium">{user.email}</dd>
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
                <Icon className="size-4" />
                {label}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Provider / status (admin only) */}
      {isAdmin ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Monitor className="size-4" /> System status
            </CardTitle>
            <CardDescription>
              Active provider and dependency health from <code>/status</code>.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {status.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : status.data ? (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="rounded-md border bg-muted/30 px-3 py-2">
                    <p className="text-xs text-muted-foreground">LLM provider</p>
                    <p className="text-sm font-medium">
                      {status.data.llm.provider} · {status.data.llm.model}
                    </p>
                  </div>
                  <StatusBadge status={status.data.status} />
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {Object.entries(status.data.services).map(([name, svc]) => (
                    <div
                      key={name}
                      className="flex items-center justify-between rounded-md border p-3"
                    >
                      <div>
                        <p className="text-sm font-medium capitalize">
                          {name.replace(/_/g, ' ')}
                        </p>
                        {typeof svc.latency_ms === 'number' ? (
                          <p className="text-xs text-muted-foreground">
                            {svc.latency_ms}ms
                          </p>
                        ) : null}
                      </div>
                      <StatusBadge status={svc.status as ServiceStatus} />
                    </div>
                  ))}
                </div>

                <dl className="grid grid-cols-2 gap-3 text-sm">
                  <div className="rounded-md border p-3">
                    <dt className="text-xs text-muted-foreground">Warehouse rows</dt>
                    <dd className="font-semibold tabular-nums">
                      {formatNumber(status.data.warehouse.marts_rows)}
                    </dd>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      dbt · {formatDateTime(status.data.warehouse.last_dbt_run)}
                    </p>
                  </div>
                  <div className="rounded-md border p-3">
                    <dt className="text-xs text-muted-foreground">Indexed chunks</dt>
                    <dd className="font-semibold tabular-nums">
                      {formatNumber(status.data.index.collection_size)}
                    </dd>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {formatDateTime(status.data.index.last_index)}
                    </p>
                  </div>
                </dl>
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
