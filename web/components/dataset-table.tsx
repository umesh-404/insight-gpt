'use client';

import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { FileSpreadsheet, FileText, ShieldCheck, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useToast } from '@/components/ui/toast';
import { RelativeTime } from '@/components/relative-time';
import {
  DatasetProgress,
  DatasetStatusBadge,
  formatProgressPercent,
} from '@/components/dataset-status';
import { describeError } from '@/components/states';
import {
  datasetQk,
  useDatasetJob,
  useDeleteDataset,
  useMediaQuery,
} from '@/lib/hooks';
import { cn, formatNumber } from '@/lib/utils';
import {
  formatDatasetBytes,
  isTerminalDatasetStatus,
  type Dataset,
} from '@/lib/types';

/**
 * Below this the row becomes a card.
 *
 * Seven columns of file metadata cannot be read in a 343px-wide column: the
 * table was 873px against a 718px container even on a 1024px laptop, so the
 * "Added" column and the remove button sat off-screen behind a scrollbar, and
 * the full-width delete confirmation — a sentence about an irreversible
 * action — opened already scrolled halfway out of view. Stacking is the fix;
 * squeezing the columns is not.
 */
const TABLE_QUERY = '(min-width: 768px)';

/**
 * Strip the exception wrapper off an indexing failure.
 *
 * The API reports `DatasetParseError: (Could not parse Excel spreadsheet:
 * File is not a zip file)`. The sentence inside is genuinely useful; the class
 * name and the parentheses around it are the indexer's internals, and showing
 * them to a reader who uploaded a spreadsheet tells them nothing they can act
 * on. This only tidies the presentation — it never invents a reason, and a
 * message that does not match the pattern is passed through untouched.
 */
export function cleanDatasetError(raw: string): string {
  let text = raw.trim();
  const prefixed = /^[A-Z][A-Za-z0-9_]*(Error|Exception):\s*(.+)$/s.exec(text);
  if (prefixed?.[2]) text = prefixed[2].trim();
  if (text.startsWith('(') && text.endsWith(')')) {
    const inner = text.slice(1, -1);
    // Only unwrap when the parentheses really are a wrapper, not part of the
    // sentence ("(CSV, 5 columns) could not be read").
    if (!inner.includes('(') && !inner.includes(')')) text = inner.trim();
  }
  return text || raw;
}

/**
 * Live progress for one dataset that is still being indexed.
 *
 * Mounted only for non-terminal rows, so a table of finished datasets starts
 * no pollers at all.
 *
 * There is deliberately **no bar until the indexer has reported a fraction**.
 * A determinate bar pinned at 0% while the first poll is in flight is the
 * exact thing the honesty rules forbid: an indicator that implies measured
 * movement when nothing has been measured yet. Until then the row says
 * "Starting…", which is all that is actually known.
 */
function IndexingCell({ dataset }: { dataset: Dataset }) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const job = useDatasetJob(dataset.id);
  const announced = React.useRef(false);

  const status = job.data?.status ?? dataset.status;
  const done = isTerminalDatasetStatus(status);

  React.useEffect(() => {
    if (!done || announced.current) return;
    announced.current = true;
    // The row's chunk and redaction counts only exist on the dataset record,
    // so the list has to be re-read before they can be shown.
    void qc.invalidateQueries({ queryKey: datasetQk.all });
    const reason = job.data?.message ?? dataset.error ?? null;
    toast({
      title:
        status === 'ready'
          ? `${dataset.name} is ready`
          : `Could not index ${dataset.name}`,
      description:
        status === 'ready'
          ? 'Questions in Ask can now retrieve from this file.'
          : // "Indexing failed" under the heading "Indexing … failed" told the
            // reader nothing twice. A reason that only restates the title is
            // dropped in favour of saying where the real one is.
            describeIndexingFailure(reason),
      variant: status === 'ready' ? 'success' : 'destructive',
    });
  }, [done, status, dataset.name, dataset.error, job.data?.message, qc, toast]);

  if (done) return <DatasetStatusBadge status={status} />;

  const progress = job.data?.progress;
  const measured = typeof progress === 'number' && Number.isFinite(progress);

  return (
    <div className="min-w-0 space-y-1.5">
      <DatasetStatusBadge status={status} />
      {measured ? (
        <>
          <DatasetProgress
            value={progress}
            label={`Indexing ${dataset.name}`}
            className="max-w-[6.5rem]"
          />
          <p className="text-xs text-muted-foreground">
            <span className="tabular-nums">{formatProgressPercent(progress)}</span>
            {job.data?.message ? ` · ${job.data.message}` : null}
          </p>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {job.data?.message ?? 'Starting…'}
        </p>
      )}
    </div>
  );
}

/** Toast body for a failed index: never a restatement of the toast's title. */
function describeIndexingFailure(reason: string | null): string {
  const cleaned = reason ? cleanDatasetError(reason) : '';
  if (!cleaned || /^indexing failed\.?$/i.test(cleaned)) {
    return 'Nothing from this file was indexed. The reason is on its row under Indexed files.';
  }
  return cleaned;
}

function KindBadge({ kind }: { kind: Dataset['kind'] }) {
  return (
    <Badge variant="muted" className="gap-1.5 whitespace-nowrap">
      {kind === 'tabular' ? (
        <FileSpreadsheet className="size-4" aria-hidden />
      ) : (
        <FileText className="size-4" aria-hidden />
      )}
      {kind === 'tabular' ? 'Table' : 'Document'}
    </Badge>
  );
}

/**
 * Size and age, folded into the name cell.
 *
 * They were two columns of their own, which is two columns spent on the two
 * least-consulted facts about a file. As a subtitle under the filename they
 * cost nothing and read the way a file list reads everywhere else.
 */
function DatasetMeta({ dataset }: { dataset: Dataset }) {
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs font-normal text-muted-foreground">
      <span className="tabular-nums">{formatDatasetBytes(dataset.bytes)}</span>
      <span aria-hidden>·</span>
      <RelativeTime iso={dataset.created_at} />
    </span>
  );
}

function DatasetName({ dataset }: { dataset: Dataset }) {
  return (
    <>
      <span className="block truncate font-medium" title={dataset.name}>
        {dataset.name}
      </span>
      <DatasetMeta dataset={dataset} />
      {dataset.status === 'failed' && dataset.error ? (
        <span className="mt-1 block text-xs font-normal text-destructive">
          {cleanDatasetError(dataset.error)}
        </span>
      ) : null}
    </>
  );
}

/** How many passages this file put into the index, or why there is no number. */
function ChunkFigure({ dataset }: { dataset: Dataset }) {
  if (dataset.status !== 'ready') {
    // Not "0": nothing has been written yet, which is a different statement
    // from "this file produced nothing".
    return <span className="text-muted-foreground">—</span>;
  }
  return <>{formatNumber(dataset.chunks)}</>;
}

/**
 * The redaction count — the trust signal on this screen.
 *
 * It gets the one coloured chip in the row and spells out what it means on
 * hover. `whitespace-nowrap` matters: without it "2 removed" broke across two
 * lines inside the chip and the row grew a second line of height for it.
 */
function RedactionFigure({ dataset }: { dataset: Dataset }) {
  if (dataset.status !== 'ready') {
    return <span className="text-muted-foreground">—</span>;
  }
  if (dataset.redactions > 0) {
    return (
      <Badge
        variant="success"
        className="whitespace-nowrap tabular-nums"
        title={`${formatNumber(
          dataset.redactions,
        )} secret or personal value(s) were removed from this file before any of it was embedded.`}
      >
        <ShieldCheck className="size-3" aria-hidden />
        {formatNumber(dataset.redactions)} removed
      </Badge>
    );
  }
  return (
    <span
      className="whitespace-nowrap text-xs text-muted-foreground"
      title="Redaction ran and matched nothing in this file."
    >
      None found
    </span>
  );
}

/**
 * The confirmation body, shared by the table row and the card.
 *
 * The consequence is a sentence, not a word — asking "Are you sure?" in a 64px
 * cell is how people delete things they meant to keep — and the sentence is
 * rewritten for a dataset that never reached the index. Telling someone their
 * "indexed passages will be deleted" when indexing failed and nothing was ever
 * written is a false claim about what the button does.
 */
function DeleteConfirm({
  dataset,
  deleting,
  onConfirm,
  onCancel,
}: {
  dataset: Dataset;
  deleting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const indexed = dataset.status === 'ready' && dataset.chunks > 0;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
      <p className="max-w-[72ch] text-sm">
        Remove <span className="font-medium">{dataset.name}</span>?{' '}
        {indexed ? (
          <>
            Its {formatNumber(dataset.chunks)} indexed passages are deleted from
            the local vector index, and answers in Ask stop being able to
            retrieve from this file.
          </>
        ) : (
          <>
            Nothing from this file reached the index, so no answer changes. This
            removes the upload and its record.
          </>
        )}{' '}
        You cannot download the file back from here, and this cannot be undone.
      </p>
      <div className="flex shrink-0 gap-2">
        <Button
          variant="destructive"
          size="sm"
          loading={deleting}
          loadingLabel="Removing…"
          onClick={onConfirm}
        >
          Remove dataset
        </Button>
        <Button variant="outline" size="sm" onClick={onCancel}>
          Keep it
        </Button>
      </div>
    </div>
  );
}

function RemoveButton({
  dataset,
  expanded,
  deleting,
  onClick,
}: {
  dataset: Dataset;
  expanded: boolean;
  deleting: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={`Remove ${dataset.name}`}
      aria-expanded={expanded}
      loading={deleting}
      onClick={onClick}
    >
      <Trash2 className="size-4" aria-hidden />
    </Button>
  );
}

interface RowControl {
  confirmingId: string | null;
  setConfirmingId: (id: string | null) => void;
  deletingId: string | null;
  onDelete: (dataset: Dataset) => void;
  canDelete: boolean;
}

/** One dataset as a stacked card. The narrow-viewport form of a row. */
function DatasetCards({
  datasets,
  control,
}: {
  datasets: Dataset[];
  control: RowControl;
}) {
  return (
    <ul className="space-y-3">
      {datasets.map((dataset) => {
        const confirming = control.confirmingId === dataset.id;
        const deleting = control.deletingId === dataset.id;
        return (
          <li
            key={dataset.id}
            className="overflow-hidden rounded-lg border bg-card px-4 py-3.5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <DatasetName dataset={dataset} />
              </div>
              {control.canDelete ? (
                <RemoveButton
                  dataset={dataset}
                  expanded={confirming}
                  deleting={deleting}
                  onClick={() =>
                    control.setConfirmingId(confirming ? null : dataset.id)
                  }
                />
              ) : null}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <KindBadge kind={dataset.kind} />
              {isTerminalDatasetStatus(dataset.status) ? (
                <DatasetStatusBadge status={dataset.status} />
              ) : (
                <IndexingCell dataset={dataset} />
              )}
            </div>

            {/* A definition list, not a second table: two facts, each of which
                needs its label said out loud once the column header is gone. */}
            <dl className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm">
              <div className="flex items-center gap-1.5">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Passages
                </dt>
                <dd className="tabular-nums">
                  <ChunkFigure dataset={dataset} />
                </dd>
              </div>
              <div className="flex items-center gap-1.5">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Redacted
                </dt>
                <dd>
                  <RedactionFigure dataset={dataset} />
                </dd>
              </div>
            </dl>

            {confirming ? (
              <div className="mt-3 border-t pt-3">
                <DeleteConfirm
                  dataset={dataset}
                  deleting={deleting}
                  onConfirm={() => control.onDelete(dataset)}
                  onCancel={() => control.setConfirmingId(null)}
                />
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/** The columns, in order. Owning the list is what keeps `colSpan` honest. */
const COLUMNS = [
  { key: 'dataset', label: 'Dataset', className: 'min-w-[180px]' },
  { key: 'kind', label: 'Kind', className: 'w-[120px]' },
  // 130px is the "Indexing" chip's own min-content width plus the cell's
  // padding; anything larger pushed the six columns past a 1024px laptop's
  // 718px content area and put the remove button behind a scrollbar again.
  { key: 'status', label: 'Status', className: 'min-w-[130px]' },
  { key: 'chunks', label: 'Passages', className: 'w-[96px] text-right' },
  { key: 'redacted', label: 'Redacted', className: 'w-[128px] text-right' },
] as const;

function DatasetRows({
  datasets,
  control,
}: {
  datasets: Dataset[];
  control: RowControl;
}) {
  // Derived, never a hand-maintained constant: the previous `COLUMN_COUNT = 7`
  // was one short of the eight headers actually rendered, so the confirmation
  // row stopped a column early.
  const columnCount = COLUMNS.length + (control.canDelete ? 1 : 0);

  return (
    <div className="w-full max-w-full overflow-hidden rounded-lg border">
      <Table
        // Names the horizontal scroll region and makes it a tab stop, so the
        // columns past the fold can be reached without a mouse on the narrower
        // desktop widths where this table still overflows.
        ariaLabel="Uploaded datasets"
      >
        <caption className="sr-only">
          Uploaded datasets, with indexing status, how many passages were
          indexed and how many secrets were removed before indexing. File size
          and upload time are shown under each name.
        </caption>
        <TableHeader>
          <TableRow>
            {COLUMNS.map((column) => (
              <TableHead key={column.key} scope="col" className={column.className}>
                {column.label}
              </TableHead>
            ))}
            {control.canDelete ? (
              <TableHead scope="col" className="w-[56px] text-right">
                <span className="sr-only">Actions</span>
              </TableHead>
            ) : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {datasets.map((dataset) => {
            const confirming = control.confirmingId === dataset.id;
            const deleting = control.deletingId === dataset.id;
            return (
              <React.Fragment key={dataset.id}>
                <TableRow>
                  <TableCell className="min-w-0">
                    <DatasetName dataset={dataset} />
                  </TableCell>
                  <TableCell>
                    <KindBadge kind={dataset.kind} />
                  </TableCell>
                  <TableCell className="min-w-0">
                    {isTerminalDatasetStatus(dataset.status) ? (
                      <DatasetStatusBadge status={dataset.status} />
                    ) : (
                      <IndexingCell dataset={dataset} />
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    <ChunkFigure dataset={dataset} />
                  </TableCell>
                  <TableCell className="text-right">
                    <RedactionFigure dataset={dataset} />
                  </TableCell>
                  {control.canDelete ? (
                    <TableCell>
                      <div className="flex justify-end">
                        <RemoveButton
                          dataset={dataset}
                          expanded={confirming}
                          deleting={deleting}
                          onClick={() =>
                            control.setConfirmingId(confirming ? null : dataset.id)
                          }
                        />
                      </div>
                    </TableCell>
                  ) : null}
                </TableRow>

                {confirming ? (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell colSpan={columnCount}>
                      <DeleteConfirm
                        dataset={dataset}
                        deleting={deleting}
                        onConfirm={() => control.onDelete(dataset)}
                        onCancel={() => control.setConfirmingId(null)}
                      />
                    </TableCell>
                  </TableRow>
                ) : null}
              </React.Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

export function DatasetTable({
  datasets,
  canDelete,
  className,
}: {
  datasets: Dataset[];
  /** False for a viewer: the column is dropped rather than shown greyed out. */
  canDelete: boolean;
  className?: string;
}) {
  const { toast } = useToast();
  const remove = useDeleteDataset();
  const wide = useMediaQuery(TABLE_QUERY);
  const [confirmingId, setConfirmingId] = React.useState<string | null>(null);

  // A pending confirmation is abandoned if the row it belongs to is gone.
  React.useEffect(() => {
    if (confirmingId && !datasets.some((d) => d.id === confirmingId)) {
      setConfirmingId(null);
    }
  }, [confirmingId, datasets]);

  const onDelete = (dataset: Dataset) => {
    setConfirmingId(null);
    // The confirmation and the confirmation-of-the-confirmation have to agree:
    // a dataset whose indexing failed never had vectors, so "its vectors are
    // deleted" would describe an effect that did not happen.
    const hadVectors = dataset.status === 'ready' && dataset.chunks > 0;
    remove.mutate(dataset.id, {
      onSuccess: () =>
        toast({
          title: `Removed ${dataset.name}`,
          description: hadVectors
            ? 'Its passages are deleted. Ask can no longer retrieve from it.'
            : 'Nothing was indexed from it, so no answer changes.',
          variant: 'success',
        }),
      onError: (err) =>
        toast({
          title: `Could not remove ${dataset.name}`,
          description: describeError(err),
          variant: 'destructive',
        }),
    });
  };

  const control: RowControl = {
    confirmingId,
    setConfirmingId,
    deletingId: remove.isPending ? (remove.variables ?? null) : null,
    onDelete,
    canDelete,
  };

  return (
    <div className={cn('min-w-0', className)}>
      {wide ? (
        <DatasetRows datasets={datasets} control={control} />
      ) : (
        <DatasetCards datasets={datasets} control={control} />
      )}
    </div>
  );
}
