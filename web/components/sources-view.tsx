'use client';

import * as React from 'react';
import {
  Database,
  FileSpreadsheet,
  FileText,
  Loader2,
  Lock,
  Plug,
  Plus,
  Server,
  Trash2,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { describeError, EmptyState, ErrorState } from '@/components/states';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useToast } from '@/components/ui/toast';
import {
  useCreateSource,
  useDeleteSource,
  useSources,
  useTestSource,
} from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/utils';
import { SOURCE_KINDS, type SourceKind } from '@/lib/types';

const KIND_ICON: Record<SourceKind, React.ReactNode> = {
  postgres: <Database className="size-4" aria-hidden />,
  mysql: <Server className="size-4" aria-hidden />,
  csv: <FileSpreadsheet className="size-4" aria-hidden />,
  excel: <FileSpreadsheet className="size-4" aria-hidden />,
  documents: <FileText className="size-4" aria-hidden />,
};

const KIND_LABEL: Record<SourceKind, string> = {
  postgres: 'PostgreSQL',
  mysql: 'MySQL',
  csv: 'CSV',
  excel: 'Excel',
  documents: 'Documents',
};

const KIND_OPTIONS = SOURCE_KINDS.map((kind) => ({
  value: kind,
  label: KIND_LABEL[kind],
}));

/** Kinds that need a connection string; file/document kinds do not. */
const NEEDS_DSN: SourceKind[] = ['postgres', 'mysql'];

export function SourcesView() {
  const { toast } = useToast();
  const { hasRole } = useAuth();
  const sources = useSources();
  const testSource = useTestSource();
  const createSource = useCreateSource();
  const deleteSource = useDeleteSource();
  const [name, setName] = React.useState('');
  const [kind, setKind] = React.useState<SourceKind>('postgres');
  const [dsn, setDsn] = React.useState('');
  const isAdmin = hasRole('admin');

  const onTest = (id: string, sourceName: string) => {
    testSource.mutate(id, {
      onSuccess: (res) =>
        toast({
          title: res.ok ? `${sourceName} reachable` : `${sourceName} failed`,
          description: `${res.message} · ${res.tables_seen} tables · ${Math.round(res.latency_ms)}ms`,
          variant: res.ok ? 'success' : 'destructive',
        }),
      onError: (err) =>
        toast({
          title: `Could not test ${sourceName}`,
          description: describeError(err),
          variant: 'destructive',
        }),
    });
  };

  const onDelete = (id: string, sourceName: string) => {
    deleteSource.mutate(id, {
      onSuccess: () =>
        toast({ title: `Removed ${sourceName}`, variant: 'success' }),
      onError: (err) =>
        toast({
          title: `Could not remove ${sourceName}`,
          description: describeError(err),
          variant: 'destructive',
        }),
    });
  };

  const onRegister = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    createSource.mutate(
      { name: trimmed, kind, dsn: dsn.trim() || null },
      {
        onSuccess: (source) => {
          toast({
            title: `Source “${source.name}” registered`,
            description: 'Run a connectivity test to verify credentials and schema.',
            variant: 'success',
          });
          setName('');
          setDsn('');
        },
        onError: (err) =>
          toast({
            title: 'Could not register the source',
            description: describeError(err),
            variant: 'destructive',
          }),
      },
    );
  };

  if (!isAdmin) {
    return (
      <div className="p-4 sm:p-6">
        <PageHeader title="Data sources" />
        <div className="mt-6">
          <EmptyState
            title="Admins only"
            description="Data-source administration requires the admin role."
            icon={<Lock className="size-5" aria-hidden />}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Data sources"
        description="Register connectors and verify connectivity. Secrets are write-only — never returned in reads."
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Registered sources</CardTitle>
          </CardHeader>
          <CardContent>
            {sources.isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : sources.isError ? (
              <ErrorState error={sources.error} onRetry={() => void sources.refetch()} />
            ) : sources.data && sources.data.length ? (
              <div className="scrollbar-thin overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Name</TableHead>
                      <TableHead scope="col">Kind</TableHead>
                      <TableHead scope="col">Status</TableHead>
                      <TableHead scope="col">Last tested</TableHead>
                      <TableHead scope="col" className="text-right">
                        Actions
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sources.data.map((source) => {
                      const icon = KIND_ICON[source.kind as SourceKind];
                      return (
                        <TableRow key={source.id}>
                          <TableCell className="font-medium">{source.name}</TableCell>
                          <TableCell>
                            <Badge variant="muted" className="gap-1.5 capitalize">
                              {icon ?? <Database className="size-4" aria-hidden />}
                              {source.kind}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={source.status} />
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {formatDateTime(source.last_tested_at)}
                          </TableCell>
                          <TableCell>
                            <div className="flex justify-end gap-2">
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={
                                  testSource.isPending &&
                                  testSource.variables === source.id
                                }
                                onClick={() => onTest(source.id, source.name)}
                              >
                                {testSource.isPending &&
                                testSource.variables === source.id ? (
                                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                                ) : (
                                  <Plug className="size-3.5" aria-hidden />
                                )}
                                Test
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                aria-label={`Remove ${source.name}`}
                                disabled={
                                  deleteSource.isPending &&
                                  deleteSource.variables === source.id
                                }
                                onClick={() => onDelete(source.id, source.name)}
                              >
                                {deleteSource.isPending &&
                                deleteSource.variables === source.id ? (
                                  <Loader2 className="size-4 animate-spin" aria-hidden />
                                ) : (
                                  <Trash2 className="size-4" aria-hidden />
                                )}
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <EmptyState
                title="No sources registered"
                description="Add a connector with the form on the right, then run a connectivity test."
              />
            )}
          </CardContent>
        </Card>

        <Card className="h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Register a source</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onRegister} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="source-name">Name</Label>
                <Input
                  id="source-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="orders_pg"
                  maxLength={120}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="source-kind">Kind</Label>
                <Select
                  id="source-kind"
                  options={KIND_OPTIONS}
                  value={kind}
                  onChange={(e) => setKind(e.target.value as SourceKind)}
                />
              </div>
              {NEEDS_DSN.includes(kind) ? (
                <div className="space-y-1.5">
                  <Label htmlFor="source-dsn">Connection string</Label>
                  <Input
                    id="source-dsn"
                    type="password"
                    value={dsn}
                    onChange={(e) => setDsn(e.target.value)}
                    placeholder="postgresql://…"
                    autoComplete="off"
                  />
                  <p className="text-xs text-muted-foreground">
                    Stored encrypted and redacted from every read response.
                  </p>
                </div>
              ) : null}
              <Button
                type="submit"
                className="w-full"
                disabled={createSource.isPending || !name.trim()}
              >
                {createSource.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" aria-hidden /> Registering…
                  </>
                ) : (
                  <>
                    <Plus className="size-4" aria-hidden /> Register source
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
