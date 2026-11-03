'use client';

import * as React from 'react';
import {
  Database,
  Eye,
  EyeOff,
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
const DEFAULT_TIMEOUT_S = 5;

interface FormErrors {
  name?: string;
  path?: string;
  dsn?: string;
  timeout?: string;
}

/** Field order in the form, so the error summary lists problems top-to-bottom. */
const FIELD_ORDER: Array<keyof FormErrors> = ['name', 'path', 'dsn', 'timeout'];

const FIELD_ID: Record<keyof FormErrors, string> = {
  name: 'source-name',
  path: 'source-path',
  dsn: 'source-dsn',
  timeout: 'source-timeout',
};

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

/**
 * Show the tail of a source path, not its head.
 *
 * Every registered source in a deployment lives under the same root, so a
 * head-truncated path rendered the same 38 characters on every row
 * ("D:\Projects\insight-gpt\data\generated\…") and clipped the only part that
 * tells the rows apart. Keeping the last two segments makes the column scannable;
 * the full path stays in the `title` tooltip.
 */
function shortenLocation(location: string): string {
  if (location.length <= 40) return location;
  const parts = location.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 2) return location;
  const sep = location.includes('\\') ? '\\' : '/';
  return `…${sep}${parts.slice(-2).join(sep)}`;
}

/**
 * Every `/sources` endpoint — list included — requires the admin role. The
 * fetching therefore lives in a child that is mounted only once the signed-in
 * role is known to be admin: calling `useSources()` at the top of the page meant
 * an analyst or viewer fired an admin-only request on load and took a 403 behind
 * the "Admins only" panel they were already being shown.
 */
export function SourcesView() {
  const { hasRole, loading } = useAuth();

  return (
    <div className="min-w-0 max-w-full space-y-6 overflow-hidden p-4 sm:p-6">
      <PageHeader
        title="Data sources"
        description="Every connector this deployment reads from. Secrets are write-only — no read ever returns them."
      />

      {loading ? (
        <div
          className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]"
          aria-busy="true"
        >
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      ) : hasRole('admin') ? (
        <SourcesAdmin />
      ) : (
        // Deciding this before auth resolves flashed "Admins only" at an
        // administrator for one frame.
        <EmptyState
          title="Data sources need the admin role"
          description="Registering, testing and removing connectors is restricted to administrators. Ask an administrator if you need a source added."
          icon={<Lock className="size-5" aria-hidden />}
        />
      )}
    </div>
  );
}

function SourcesAdmin() {
  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <SourceTable />
      <RegisterPanel />
    </div>
  );
}

function SourceTable() {
  const { toast } = useToast();
  const sources = useSources();
  const testSource = useTestSource();
  const deleteSource = useDeleteSource();
  const [confirmingId, setConfirmingId] = React.useState<string | null>(null);

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
      onSuccess: () => toast({ title: `Removed ${sourceName}`, variant: 'success' }),
      onError: (err) =>
        toast({
          title: `Could not remove ${sourceName}`,
          description: describeError(err),
          variant: 'destructive',
        }),
    });
  };

  return (
    <section aria-labelledby="sources-heading" className="min-w-0 max-w-full space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="sources-heading"
          className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
        >
          Registered sources
        </h2>
        {sources.data?.length ? (
          <p className="text-xs text-muted-foreground">
            {sources.data.length} {sources.data.length === 1 ? 'connector' : 'connectors'}
          </p>
        ) : null}
      </div>

      {sources.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : sources.isError ? (
        <ErrorState error={sources.error} onRetry={() => void sources.refetch()} />
      ) : sources.data && sources.data.length ? (
        <div className="w-full max-w-full overflow-hidden rounded-lg border">
          <Table>
            <caption className="sr-only">
              Registered data sources, with the result of their last connectivity
              test.
            </caption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col" className="min-w-[180px]">
                  Source
                </TableHead>
                <TableHead scope="col" className="w-[120px]">
                  Kind
                </TableHead>
                <TableHead scope="col" className="min-w-[180px]">
                  Last test
                </TableHead>
                <TableHead scope="col" className="w-[130px]">
                  Tested
                </TableHead>
                <TableHead scope="col" className="w-[130px] text-right">
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
                    <TableCell className="min-w-0 font-medium">
                      <span className="block truncate font-semibold">{source.name}</span>
                      {source.location ? (
                        <span
                          className="mt-0.5 block max-w-xs truncate font-mono text-xs font-normal text-muted-foreground sm:max-w-sm md:max-w-md xl:max-w-xs 2xl:max-w-sm"
                          title={source.location}
                        >
                          {shortenLocation(source.location)}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant="muted" className="gap-1.5">
                        {icon ?? <Database className="size-4" aria-hidden />}
                        {KIND_LABEL[source.kind as SourceKind] ?? source.kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="min-w-0">
                      <StatusBadge status={source.status} />
                      {source.detail ? (
                        <span
                          className="mt-1 block max-w-xs truncate text-xs text-muted-foreground sm:max-w-sm md:max-w-md xl:max-w-xs 2xl:max-w-sm"
                          title={source.detail}
                        >
                          {source.detail}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {source.last_tested_at
                        ? formatDateTime(source.last_tested_at)
                        : 'Never'}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        {confirming ? (
                          // Removal is not undoable from the UI, so the row
                          // asks once, in place, and names what goes.
                          <>
                            <Button
                              variant="destructive"
                              size="sm"
                              disabled={deleting}
                              onClick={() => onDelete(source.id, source.name)}
                            >
                              {deleting ? (
                                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                              ) : null}
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
                                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                              ) : (
                                <Plug className="size-3.5" aria-hidden />
                              )}
                              {testing ? 'Testing…' : 'Test'}
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Remove ${source.name}`}
                              disabled={deleting}
                              onClick={() => setConfirmingId(source.id)}
                            >
                              <Trash2 className="size-4" aria-hidden />
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
          description="Register a connector, then run a connectivity test to confirm the API process can actually read it."
        />
      )}
    </section>
  );
}

function RegisterPanel() {
  const { toast } = useToast();
  const createSource = useCreateSource();

  const [name, setName] = React.useState('');
  const [kind, setKind] = React.useState<SourceKind>('csv');
  const [path, setPath] = React.useState('');
  const [dsn, setDsn] = React.useState('');
  const [timeoutS, setTimeoutS] = React.useState('');
  const [errors, setErrors] = React.useState<FormErrors>({});
  const [revealDsn, setRevealDsn] = React.useState(false);
  // Validate a field when the user leaves it, never while they are mid-word:
  // re-validating on every keystroke marks a half-typed path as wrong.
  const [touched, setTouched] = React.useState<Partial<Record<keyof FormErrors, boolean>>>(
    {},
  );
  const summaryRef = React.useRef<HTMLDivElement | null>(null);

  const revalidate = (field: keyof FormErrors) => {
    setTouched((prev) => ({ ...prev, [field]: true }));
    const found = validate(kind, name, path, dsn, timeoutS);
    setErrors((prev) => ({ ...prev, [field]: found[field] }));
  };

  const shown = (field: keyof FormErrors) => (touched[field] ? errors[field] : undefined);
  const listed = FIELD_ORDER.filter((field) => shown(field));

  const onKindChange = (next: SourceKind) => {
    setKind(next);
    // The fields that no longer apply must not be submitted with the next kind.
    if (sourceNeedsDsn(next)) setPath('');
    else setDsn('');
    setErrors({});
    setTouched({});
    setRevealDsn(false);
  };

  const onRegister = (e: React.FormEvent) => {
    e.preventDefault();
    const found = validate(kind, name, path, dsn, timeoutS);
    setErrors(found);
    setTouched({ name: true, path: true, dsn: true, timeout: true });
    if (Object.keys(found).length) {
      // Move the reader to the list of problems rather than leaving them at a
      // submit button that silently did nothing.
      summaryRef.current?.focus();
      return;
    }

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
        setTouched({});
        setRevealDsn(false);
      },
      onError: (err) =>
        toast({
          title: 'Could not register the source',
          description: describeError(err),
          variant: 'destructive',
        }),
    });
  };

  return (
    <section
      aria-labelledby="register-heading"
      className="h-fit min-w-0 max-w-full rounded-lg border bg-card"
    >
      <div className="border-b px-5 py-4">
        <h2 id="register-heading" className="text-base font-semibold tracking-tight">
          Register a source
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Registering records how to reach a connector. It stays untested until you
          probe it.
        </p>
      </div>

      <form onSubmit={onRegister} noValidate className="space-y-5 p-5">
        {/* Several fields are affected on submit, so the problems are also
            summarised in one focusable place at the top of the form. */}
        <div
          ref={summaryRef}
          tabIndex={-1}
          role={listed.length ? 'alert' : undefined}
          aria-live="polite"
          className={
            listed.length
              ? 'rounded-md border border-destructive/30 bg-destructive/5 p-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
              : 'sr-only'
          }
        >
          {listed.length ? (
            <>
              <p className="text-sm font-medium text-destructive">
                {listed.length === 1
                  ? 'One field needs attention'
                  : `${listed.length} fields need attention`}
              </p>
              <ul className="mt-1.5 space-y-1">
                {listed.map((field) => (
                  <li key={field} className="text-xs">
                    <a
                      href={`#${FIELD_ID[field]}`}
                      className="text-destructive underline underline-offset-2"
                    >
                      {shown(field)}
                    </a>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>

        {/* Kind comes first because it decides which fields exist below it —
            asked last, it rearranged a form the reader had already filled in. */}
        <div className="space-y-1.5">
          <Label htmlFor="source-kind">Kind</Label>
          <Select
            id="source-kind"
            options={KIND_OPTIONS}
            value={kind}
            onChange={(e) => onKindChange(e.target.value as SourceKind)}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="source-name">Name</Label>
          <Input
            id="source-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => revalidate('name')}
            placeholder="orders.csv"
            maxLength={120}
            aria-invalid={Boolean(shown('name'))}
            aria-describedby={shown('name') ? 'source-name-error' : 'source-name-hint'}
          />
          {shown('name') ? (
            <p id="source-name-error" className="text-xs text-destructive">
              {shown('name')}
            </p>
          ) : (
            <p id="source-name-hint" className="text-xs text-muted-foreground">
              How this connector is labelled in run logs and the table beside this
              form.
            </p>
          )}
        </div>

        {sourceNeedsPath(kind) ? (
          <div className="space-y-1.5">
            <Label htmlFor="source-path">Path</Label>
            <Input
              id="source-path"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onBlur={() => revalidate('path')}
              placeholder={PATH_PLACEHOLDER[kind]}
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-xs"
              aria-invalid={Boolean(shown('path'))}
              aria-describedby={shown('path') ? 'source-path-error' : 'source-path-hint'}
            />
            {shown('path') ? (
              <p id="source-path-error" className="text-xs text-destructive">
                {shown('path')}
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
            <div className="flex gap-2">
              <Input
                id="source-dsn"
                // Masked by default because this field is filled in while a
                // projector is on, but a mistyped DSN is unfixable blind.
                type={revealDsn ? 'text' : 'password'}
                value={dsn}
                onChange={(e) => setDsn(e.target.value)}
                onBlur={() => revalidate('dsn')}
                placeholder={DSN_PLACEHOLDER[kind]}
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                aria-invalid={Boolean(shown('dsn'))}
                aria-describedby={shown('dsn') ? 'source-dsn-error' : 'source-dsn-hint'}
              />
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-pressed={revealDsn}
                aria-label={
                  revealDsn ? 'Hide the connection string' : 'Show the connection string'
                }
                onClick={() => setRevealDsn((v) => !v)}
              >
                {revealDsn ? (
                  <EyeOff className="size-4" aria-hidden />
                ) : (
                  <Eye className="size-4" aria-hidden />
                )}
              </Button>
            </div>
            {shown('dsn') ? (
              <p id="source-dsn-error" className="text-xs text-destructive">
                {shown('dsn')}
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
          <Label htmlFor="source-timeout">
            Test timeout{' '}
            <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="source-timeout"
            type="number"
            inputMode="decimal"
            min={MIN_TIMEOUT_S}
            max={MAX_TIMEOUT_S}
            step="0.5"
            value={timeoutS}
            onChange={(e) => setTimeoutS(e.target.value)}
            onBlur={() => revalidate('timeout')}
            placeholder={String(DEFAULT_TIMEOUT_S)}
            className="max-w-[9rem] tabular-nums"
            aria-invalid={Boolean(shown('timeout'))}
            aria-describedby={
              shown('timeout') ? 'source-timeout-error' : 'source-timeout-hint'
            }
          />
          {shown('timeout') ? (
            <p id="source-timeout-error" className="text-xs text-destructive">
              {shown('timeout')}
            </p>
          ) : (
            <p id="source-timeout-hint" className="text-xs text-muted-foreground">
              Seconds a connectivity probe may take before it gives up. Left blank, the
              API uses {DEFAULT_TIMEOUT_S}s.
            </p>
          )}
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
    </section>
  );
}
