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
import { datasetQk, useDatasetJob, useDeleteDataset } from '@/lib/hooks';
import { formatNumber } from '@/lib/utils';
import {
  formatDatasetBytes,
  isTerminalDatasetStatus,
  type Dataset,
} from '@/lib/types';

const COLUMN_COUNT = 7;

/**
 * Live progress for one dataset that is still being indexed.
 *
 * Mounted only for non-terminal rows, so a table of finished datasets starts
 * no pollers at all. The bar carries the indexer's own fraction and the stage
 * it reports — "Embedding chunks" at 62% is a status; a spinner is not.
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
    toast({
      title:
        status === 'ready'
          ? `${dataset.name} is ready`
          : `Indexing ${dataset.name} failed`,
      description:
        status === 'ready'
          ? 'Questions in Ask can now retrieve from this file.'
          : (job.data?.message ??
            dataset.error ??
            'The reason is shown on the row.'),
      variant: status === 'ready' ? 'success' : 'destructive',
    });
  }, [done, status, dataset.name, dataset.error, job.data?.message, qc, toast]);

  if (done) return <DatasetStatusBadge status={status} />;

  const progress = job.data?.progress ?? 0;
  return (
    <div className="min-w-0 space-y-1.5">
      <DatasetStatusBadge status={status} />
      <DatasetProgress
        value={progress}
        label={`Indexing ${dataset.name}`}
        className="max-w-[12rem]"
      />
      <p className="text-xs text-muted-foreground">
        <span className="tabular-nums">{formatProgressPercent(progress)}</span>
        {job.data?.message ? ` · ${job.data.message}` : null}
      </p>
    </div>
  );
}

function KindBadge({ kind }: { kind: Dataset['kind'] }) {
  return (
    <Badge variant="muted" className="gap-1.5">
      {kind === 'tabular' ? (
        <FileSpreadsheet className="size-4" aria-hidden />
      ) : (
        <FileText className="size-4" aria-hidden />
      )}
      {kind === 'tabular' ? 'Table' : 'Document'}
    </Badge>
  );
}

export function DatasetTable({
  datasets,
  canDelete,
}: {
  datasets: Dataset[];
  /** False for a viewer: the column is dropped rather than shown greyed out. */
  canDelete: boolean;
}) {
  const { toast } = useToast();
  const remove = useDeleteDataset();
  const [confirmingId, setConfirmingId] = React.useState<string | null>(null);

  // A pending confirmation is abandoned if the row it belongs to is gone.
  React.useEffect(() => {
    if (confirmingId && !datasets.some((d) => d.id === confirmingId)) {
      setConfirmingId(null);
    }
  }, [confirmingId, datasets]);

  const onDelete = (dataset: Dataset) => {
    setConfirmingId(null);
    remove.mutate(dataset.id, {
      onSuccess: () =>
        toast({
          title: `Removed ${dataset.name}`,
          description: 'Its vectors are deleted. Ask can no longer retrieve from it.',
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

  const columns = canDelete ? COLUMN_COUNT : COLUMN_COUNT - 1;

  return (
    <div className="w-full max-w-full overflow-hidden rounded-lg border">
      <Table>
        <caption className="sr-only">
          Uploaded datasets, with indexing status, how many passages were
          indexed, how many secrets were removed before indexing, file size and
          upload time.
        </caption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col" className="min-w-[200px]">
              Dataset
            </TableHead>
            <TableHead scope="col" className="w-[130px]">
              Kind
            </TableHead>
            <TableHead scope="col" className="min-w-[170px]">
              Status
            </TableHead>
            <TableHead scope="col" className="w-[110px] text-right">
              Chunks
            </TableHead>
            <TableHead scope="col" className="w-[150px] text-right">
              Redacted
            </TableHead>
            <TableHead scope="col" className="w-[100px] text-right">
              Size
            </TableHead>
            <TableHead scope="col" className="w-[120px]">
              Added
            </TableHead>
            {canDelete ? (
              <TableHead scope="col" className="w-[64px] text-right">
                <span className="sr-only">Actions</span>
              </TableHead>
            ) : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {datasets.map((dataset) => {
            const confirming = confirmingId === dataset.id;
            const deleting = remove.isPending && remove.variables === dataset.id;
            const indexed = dataset.status === 'ready';
            return (
              <React.Fragment key={dataset.id}>
                <TableRow>
                  <TableCell className="min-w-0 font-medium">
                    <span className="block truncate" title={dataset.name}>
                      {dataset.name}
                    </span>
                    {dataset.status === 'failed' && dataset.error ? (
                      <span
                        className="mt-0.5 block max-w-xs truncate text-xs font-normal text-destructive"
                        title={dataset.error}
                      >
                        {dataset.error}
                      </span>
                    ) : null}
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
                    {indexed ? (
                      formatNumber(dataset.chunks)
                    ) : (
                      // Not "0": nothing has been written yet, which is a
                      // different statement from "this file produced nothing".
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {indexed ? (
                      dataset.redactions > 0 ? (
                        // The count is the trust signal on this screen, so it
                        // gets the one coloured chip in the row and spells out
                        // what it means on hover.
                        <Badge
                          variant="success"
                          className="tabular-nums"
                          title={`${formatNumber(
                            dataset.redactions,
                          )} secret or personal value(s) were removed from this file before any of it was embedded.`}
                        >
                          <ShieldCheck className="size-3" aria-hidden />
                          {formatNumber(dataset.redactions)} removed
                        </Badge>
                      ) : (
                        <span
                          className="text-xs text-muted-foreground"
                          title="Redaction ran and matched nothing in this file."
                        >
                          None found
                        </span>
                      )
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatDatasetBytes(dataset.bytes)}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <RelativeTime iso={dataset.created_at} />
                  </TableCell>
                  {canDelete ? (
                    <TableCell>
                      <div className="flex justify-end">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Remove ${dataset.name}`}
                          aria-expanded={confirming}
                          loading={deleting}
                          onClick={() =>
                            setConfirmingId(confirming ? null : dataset.id)
                          }
                        >
                          <Trash2 className="size-4" aria-hidden />
                        </Button>
                      </div>
                    </TableCell>
                  ) : null}
                </TableRow>

                {confirming ? (
                  // The confirmation gets a full-width row because the
                  // consequence is a sentence, not a word. Asking "Are you
                  // sure?" in a 64px cell is how people delete things they
                  // meant to keep.
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell colSpan={columns}>
                      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
                        <p className="max-w-[72ch] text-sm">
                          Remove <span className="font-medium">{dataset.name}</span>?
                          Its {dataset.chunks > 0 ? `${formatNumber(dataset.chunks)} ` : ''}
                          indexed passages are deleted from the local vector index,
                          and answers in Ask stop being able to retrieve from this
                          file. The uploaded file itself is not recoverable from
                          here. This cannot be undone.
                        </p>
                        <div className="flex shrink-0 gap-2">
                          <Button
                            variant="destructive"
                            size="sm"
                            loading={deleting}
                            loadingLabel="Removing…"
                            onClick={() => onDelete(dataset)}
                          >
                            Remove dataset
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setConfirmingId(null)}
                          >
                            Keep it
                          </Button>
                        </div>
                      </div>
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
