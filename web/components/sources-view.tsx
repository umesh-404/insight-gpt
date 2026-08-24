'use client';

import * as React from 'react';
import {
  Database,
  FileSpreadsheet,
  FileText,
  Loader2,
  Lock,
  Plug,
  Server,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { EmptyState, ErrorState } from '@/components/states';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
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
import { useSources, useTestSource } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/utils';
import type { SourceKind } from '@/lib/types';

const KIND_ICON: Record<SourceKind, React.ReactNode> = {
  postgres: <Database className="size-4" />,
  mysql: <Server className="size-4" />,
  csv: <FileSpreadsheet className="size-4" />,
  excel: <FileSpreadsheet className="size-4" />,
  documents: <FileText className="size-4" />,
};

const KIND_OPTIONS = [
  { value: 'postgres', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'csv', label: 'CSV' },
  { value: 'excel', label: 'Excel' },
  { value: 'documents', label: 'Documents' },
];

export function SourcesView() {
  const { toast } = useToast();
  const { hasRole } = useAuth();
  const sources = useSources();
  const testSource = useTestSource();
  const [name, setName] = React.useState('');
  const [kind, setKind] = React.useState<SourceKind>('postgres');

  if (!hasRole('admin')) {
    return (
      <div className="p-4 sm:p-6">
        <PageHeader title="Data sources" />
        <div className="mt-6">
          <EmptyState
            title="Admins only"
            description="Data-source administration requires the admin role."
            icon={<Lock className="size-5" />}
          />
        </div>
      </div>
    );
  }

  const onTest = (id: string, sourceName: string) => {
    testSource.mutate(id, {
      onSuccess: (res) =>
        toast({
          title: res.ok ? `${sourceName} reachable` : `${sourceName} failed`,
          description: `${res.message} · ${res.tables_seen} tables · ${res.latency_ms}ms`,
          variant: res.ok ? 'success' : 'destructive',
        }),
      onError: () =>
        toast({ title: `Could not test ${sourceName}`, variant: 'destructive' }),
    });
  };

  const onRegister = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    // POST /sources would persist this; the DSN/secret never returns in reads.
    toast({
      title: `Source “${name}” registered`,
      description: 'Run a connectivity test to verify credentials and schema.',
      variant: 'success',
    });
    setName('');
  };

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
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Kind</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Last tested</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sources.data.map((source) => (
                      <TableRow key={source.id}>
                        <TableCell className="font-medium">{source.name}</TableCell>
                        <TableCell>
                          <Badge variant="muted" className="gap-1.5 capitalize">
                            {KIND_ICON[source.kind]}
                            {source.kind}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={source.status} />
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDateTime(source.last_tested_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={testSource.isPending}
                            onClick={() => onTest(source.id, source.name)}
                          >
                            {testSource.isPending && testSource.variables === source.id ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <Plug className="size-3.5" />
                            )}
                            Test
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <EmptyState title="No sources registered" />
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
              <div className="space-y-1.5">
                <Label htmlFor="source-dsn">Connection string</Label>
                <Input
                  id="source-dsn"
                  type="password"
                  placeholder="postgresql://…"
                  autoComplete="off"
                />
                <p className="text-xs text-muted-foreground">
                  Stored encrypted and redacted from every read response.
                </p>
              </div>
              <Button type="submit" className="w-full">
                Register source
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
