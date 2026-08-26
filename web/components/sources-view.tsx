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
  X,
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
import {
  SOURCE_KINDS,
  sourceNeedsDsn,
  sourceNeedsPath,
  type SourceConfig,
  type SourceKind,
} from '@/lib/types';

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

/** What the path field should look like for each file-backed kind. */
const PATH_PLACEHOLDER: Record<SourceKind, string> = {
  postgres: '',
  mysql: '',
  csv: 'data/generated/orders.csv',
  excel: 'data/generated/reviews.xlsx',
  documents: 'data/ingested/documents.json',
};

const DSN_PLACEHOLDER: Record<SourceKind, string> = {
  postgres: 'postgresql://user:password@host:5432/insight',
  mysql: 'mysql://user:password@host:3306/shop',
  csv: '',
  excel: '',
  documents: '',
};

/** The API clamps the probe timeout to this window; reject outside it here too. */
const MIN_TIMEOUT_S = 0.5;
const MAX_TIMEOUT_S = 30;

interface FormErrors {
  name?: string;
  path?: string;
  dsn?: string;
  timeout?: string;
}

/**
 * Client-side mirror of the server's `_validate`, so a submit the API would
 * certainly reject never leaves the browser. Anything subtler (an unreadable
 * path, a refused connection) is still the server's call and surfaces as a
 * toast on the response.
 */
function validate(
  kind: SourceKind,
  name: string,
  path: string,
  dsn: string,
  timeout: string,
): FormErrors {
  const errors: FormErrors = {};
  if (!name.trim()) errors.name = 'Give the source a name.';
  if (sourceNeedsPath(kind) && !path.trim()) {
    errors.path = `A ${KIND_LABEL[kind]} source needs a file or folder path.`;
  }
  if (sourceNeedsDsn(kind) && !dsn.trim()) {
    errors.dsn = `A ${KIND_LABEL[kind]} source needs a connection string.`;
  }
  if (timeout.trim()) {
    const parsed = Number(timeout);
    if (!Number.isFinite(parsed) || parsed < MIN_TIMEOUT_S || parsed > MAX_TIMEOUT_S) {
      errors.timeout = `Use a number between ${MIN_TIMEOUT_S} and ${MAX_TIMEOUT_S} seconds.`;
    }
  }
  return errors;
}

export function SourcesView() {
  const { toast } = useToast();
  const { hasRole } = useAuth();
  const sources = useSources();
  const testSource = useTestSource();
  const createSource = useCreateSource();
  const deleteSource = useDeleteSource();

  const [name, setName] = React.useState('');
  const [kind, setKind] = React.useState<SourceKind>('csv');
  const [path, setPath] = React.useState('');
  const [dsn, setDsn] = React.useState('');
  const [timeoutS, setTimeoutS] = React.useState('');
  const [errors, setErrors] = React.useState<FormErrors>({});
  const [confirmingId, setConfirmingId] = React.useState<string | null>(null);
  const isAdmin = hasRole('admin');

  // A pending "really delete?" is abandoned if the row it belongs to is gone.
  React.useEffect(() => {
    if (confirmingId && !sources.data?.some((s) => s.id === confirmingId)) {
      setConfirmingId(null);
    }
  }, [confirmingId, sources.data]);

  const onTest = (id: string, sourceName: string) => {
    testSource.mutate(id, {
      onSuccess: (res) =>
        toast({
          title: res.ok ? `${sourceName} reachable` : `${sourceName} failed`,
          description: `${res.message} · ${res.tables_seen} object(s) · ${Math.round(res.latency_ms)}ms`,
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
    setConfirmingId(null);
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

  const onKindChange = (next: SourceKind) => {
    setKind(next);
    // The fields that no longer apply must not be submitted with the next kind.
    if (sourceNeedsDsn(next)) setPath('');
    else setDsn('');
    setErrors({});
  };

  const onRegister = (e: React.FormEvent) => {
    e.preventDefault();
    const found = validate(kind, name, path, dsn, timeoutS);
    setErrors(found);
    if (Object.keys(found).length) return;

    const options: Record<string, unknown> = {};
    if (sourceNeedsPath(kind)) options.path = path.trim();
    if (timeoutS.trim()) options.timeout_s = Number(timeoutS);

    const config: SourceConfig = {
      name: name.trim(),
      kind,
      dsn: sourceNeedsDsn(kind) ? dsn.trim() : null,
      options,
    };

    createSource.mutate(config, {
      onSuccess: (source) => {
        toast({
          title: `Source “${source.name}” registered`,
          description: 'Run a connectivity test to verify it can actually be read.',
          variant: 'success',
        });
        setName('');
        setPath('');
        setDsn('');
        setTimeoutS('');
        setErrors({});
      },
      onError: (err) =>
        toast({
          title: 'Could not register the source',
          description: describeError(err),
          variant: 'destructive',
        }),
    });
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
        description="Every connector this deployment reads from. Secrets are write-only — never returned in reads."
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
                      const testing =
                        testSource.isPending && testSource.variables === source.id;
                      const deleting =
                        deleteSource.isPending && deleteSource.variables === source.id;
                      const confirming = confirmingId === source.id;
                      return (
                        <TableRow key={source.id}>
                          <TableCell className="font-medium">
                            {source.name}
                            {source.location ? (
                              <span
                                className="mt-0.5 block max-w-[28rem] truncate font-mono text-xs font-normal text-muted-foreground"
                                title={source.location}
                              >
                                {source.location}
                              </span>
                            ) : null}
                          </TableCell>
                          <TableCell>
                            <Badge variant="muted" className="gap-1.5 capitalize">
                              {icon ?? <Database className="size-4" aria-hidden />}
                              {source.kind}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <StatusBadge status={source.status} />
                            {source.detail ? (
                              <span
                                className="mt-1 block max-w-[22rem] truncate text-xs text-muted-foreground"
                                title={source.detail}
                              >
                                {source.detail}
                              </span>
                            ) : null}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {source.last_tested_at ? formatDateTime(source.last_tested_at) : 'Never'}
                          </TableCell>
                          <TableCell>
                            <div className="flex justify-end gap-2">
                              {confirming ? (
                                <>
                                  <Button
                                    variant="destructive"
                                    size="sm"
                                    disabled={deleting}
                                    onClick={() => onDelete(source.id, source.name)}
                                  >
                                    Remove
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Keep ${source.name}`}
                                    onClick={() => setConfirmingId(null)}
                                  >
                                    <X className="size-4" aria-hidden />
                                  </Button>
                                </>
                              ) : (
                                <>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={testing}
                                    onClick={() => onTest(source.id, source.name)}
                                  >
                                    {testing ? (
                                      <Loader2
                                        className="size-3.5 animate-spin"
                                        aria-hidden
                                      />
                                    ) : (
                                      <Plug className="size-3.5" aria-hidden />
                                    )}
                                    Test
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Remove ${source.name}`}
                                    disabled={deleting}
                                    onClick={() => setConfirmingId(source.id)}
                                  >
                                    {deleting ? (
                                      <Loader2 className="size-4 animate-spin" aria-hidden />
                                    ) : (
                                      <Trash2 className="size-4" aria-hidden />
                                    )}
                                  </Button>
                                </>
                              )}
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
            <form onSubmit={onRegister} noValidate className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="source-name">Name</Label>
                <Input
                  id="source-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="orders.csv"
                  maxLength={120}
                  aria-invalid={Boolean(errors.name)}
                  aria-describedby={errors.name ? 'source-name-error' : undefined}
                />
                {errors.name ? (
                  <p id="source-name-error" className="text-xs text-destructive">
                    {errors.name}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="source-kind">Kind</Label>
                <Select
                  id="source-kind"
                  options={KIND_OPTIONS}
                  value={kind}
                  onChange={(e) => onKindChange(e.target.value as SourceKind)}
                />
              </div>

              {sourceNeedsPath(kind) ? (
                <div className="space-y-1.5">
                  <Label htmlFor="source-path">Path</Label>
                  <Input
                    id="source-path"
                    value={path}
                    onChange={(e) => setPath(e.target.value)}
                    placeholder={PATH_PLACEHOLDER[kind]}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={Boolean(errors.path)}
                    aria-describedby={
                      errors.path ? 'source-path-error' : 'source-path-hint'
                    }
                  />
                  {errors.path ? (
                    <p id="source-path-error" className="text-xs text-destructive">
                      {errors.path}
                    </p>
                  ) : (
                    <p id="source-path-hint" className="text-xs text-muted-foreground">
                      A file or folder the API process can read. A folder is scanned for
                      matching files.
                    </p>
                  )}
                </div>
              ) : null}

              {sourceNeedsDsn(kind) ? (
                <div className="space-y-1.5">
                  <Label htmlFor="source-dsn">Connection string</Label>
                  <Input
                    id="source-dsn"
                    type="password"
                    value={dsn}
                    onChange={(e) => setDsn(e.target.value)}
                    placeholder={DSN_PLACEHOLDER[kind]}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={Boolean(errors.dsn)}
                    aria-describedby={errors.dsn ? 'source-dsn-error' : 'source-dsn-hint'}
                  />
                  {errors.dsn ? (
                    <p id="source-dsn-error" className="text-xs text-destructive">
                      {errors.dsn}
                    </p>
                  ) : (
                    <p id="source-dsn-hint" className="text-xs text-muted-foreground">
                      Write-only: it is sent once and never returned by any read. Only the
                      host and port are shown back to you.
                    </p>
                  )}
                </div>
              ) : null}

              <div className="space-y-1.5">
                <Label htmlFor="source-timeout">Test timeout (seconds)</Label>
                <Input
                  id="source-timeout"
                  type="number"
                  inputMode="decimal"
                  min={MIN_TIMEOUT_S}
                  max={MAX_TIMEOUT_S}
                  step="0.5"
                  value={timeoutS}
                  onChange={(e) => setTimeoutS(e.target.value)}
                  placeholder="5"
                  aria-invalid={Boolean(errors.timeout)}
                  aria-describedby={errors.timeout ? 'source-timeout-error' : undefined}
                />
                {errors.timeout ? (
                  <p id="source-timeout-error" className="text-xs text-destructive">
                    {errors.timeout}
                  </p>
                ) : null}
              </div>

              <Button type="submit" className="w-full" disabled={createSource.isPending}>
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
