'use client';

import * as React from 'react';
import { Loader2, Upload, X, TriangleAlert, FileUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { describeError } from '@/components/states';
import { useToast } from '@/components/ui/toast';
import { DatasetProgress, formatProgressPercent } from '@/components/dataset-status';
import { useUploadDataset } from '@/lib/hooks';
import { cn } from '@/lib/utils';
import {
  DATASET_ACCEPT_ATTR,
  DATASET_MAX_BATCH_BYTES,
  DATASET_MAX_FILE_BYTES,
  DATASET_MAX_FILES,
  datasetExtension,
  formatDatasetBytes,
  isAcceptedDatasetFile,
} from '@/lib/types';

/**
 * Human list of the accepted formats, in the order a user thinks about them
 * rather than the order the extensions happen to be declared in.
 */
const ACCEPTED_SUMMARY =
  'CSV, TSV, Excel, PDF, Word, Markdown, JSON and plain text';

/** The limits, as one sentence, shown before a file is ever chosen. */
export const UPLOAD_LIMITS_SENTENCE = `${ACCEPTED_SUMMARY}. Up to ${DATASET_MAX_FILES} files per upload, ${formatDatasetBytes(
  DATASET_MAX_FILE_BYTES,
)} per file and ${formatDatasetBytes(DATASET_MAX_BATCH_BYTES)} in total.`;

interface QueueItem {
  key: string;
  file: File;
  phase: 'queued' | 'uploading' | 'failed';
  /** 0..1, bytes sent. */
  progress: number;
  error?: string;
  controller?: AbortController;
}

interface Rejected {
  key: string;
  name: string;
  reason: string;
}

let keySeed = 0;
const nextKey = () => `u${(keySeed += 1)}`;

/**
 * Pre-flight the selection in the browser.
 *
 * Every rule here mirrors one the API enforces, so a file the server would
 * certainly refuse never costs the user an upload — and, more importantly, the
 * reason is specific to the file ("28.4 MB, over the 25 MB limit") rather than
 * a generic "upload failed" arriving minutes later.
 */
function triage(
  files: File[],
  alreadyQueued: QueueItem[],
): { accepted: File[]; rejected: Rejected[] } {
  const accepted: File[] = [];
  const rejected: Rejected[] = [];

  let count = alreadyQueued.length;
  let bytes = alreadyQueued.reduce((sum, item) => sum + item.file.size, 0);
  const seen = new Set(alreadyQueued.map((item) => `${item.file.name}:${item.file.size}`));

  for (const file of files) {
    const signature = `${file.name}:${file.size}`;
    if (seen.has(signature)) {
      rejected.push({
        key: nextKey(),
        name: file.name,
        reason: 'Already in this upload.',
      });
      continue;
    }
    if (!isAcceptedDatasetFile(file.name)) {
      const ext = datasetExtension(file.name);
      // The list of accepted formats is *not* repeated here. Three rejected
      // files produced three copies of the same 70-character sentence, which
      // buried the part that differs between them. The panel states it once,
      // underneath.
      rejected.push({
        key: nextKey(),
        name: file.name,
        reason: ext
          ? `${ext} is not one of the accepted formats.`
          : 'A file needs one of the accepted extensions.',
      });
      continue;
    }
    if (file.size === 0) {
      rejected.push({ key: nextKey(), name: file.name, reason: 'The file is empty.' });
      continue;
    }
    if (file.size > DATASET_MAX_FILE_BYTES) {
      rejected.push({
        key: nextKey(),
        name: file.name,
        reason: `${formatDatasetBytes(file.size)}, over the ${formatDatasetBytes(
          DATASET_MAX_FILE_BYTES,
        )} limit for a single file.`,
      });
      continue;
    }
    if (count >= DATASET_MAX_FILES) {
      rejected.push({
        key: nextKey(),
        name: file.name,
        reason: `Over the ${DATASET_MAX_FILES}-file limit for one upload. Send it in a second upload.`,
      });
      continue;
    }
    if (bytes + file.size > DATASET_MAX_BATCH_BYTES) {
      rejected.push({
        key: nextKey(),
        name: file.name,
        reason: `Would take this upload past ${formatDatasetBytes(
          DATASET_MAX_BATCH_BYTES,
        )}. Send it in a second upload.`,
      });
      continue;
    }
    accepted.push(file);
    seen.add(signature);
    count += 1;
    bytes += file.size;
  }

  return { accepted, rejected };
}

export function DatasetUpload({
  /**
   * `hero` — the empty-state primary action: a tall drop target.
   * `compact` — a quiet bar above a populated table.
   */
  tone = 'compact',
  className,
}: {
  tone?: 'hero' | 'compact';
  className?: string;
}) {
  const { toast } = useToast();
  const upload = useUploadDataset();
  // Generated, not a constant: two of these on one page (hero and compact)
  // would otherwise share an id, and every label would point at the first
  // input.
  const reactId = React.useId();
  const inputId = `dataset-file-input${reactId}`;
  const limitsId = `dataset-upload-limits${reactId}`;

  const [queue, setQueue] = React.useState<QueueItem[]>([]);
  const [rejected, setRejected] = React.useState<Rejected[]>([]);
  const [dragging, setDragging] = React.useState(false);
  const [announcement, setAnnouncement] = React.useState('');

  // A drag over a child element fires `dragleave` on the parent, so a boolean
  // set from the events alone flickers the whole time the cursor moves inside
  // the zone. Counting enter/leave pairs is what makes the state stable.
  const dragDepth = React.useRef(0);
  const running = React.useRef(false);

  /**
   * The queue is held in a ref *and* in state, and every change goes through
   * `mutate` so both move together.
   *
   * The drain loop reads the queue between awaits. Reading it from state there
   * would read a snapshot captured before the previous iteration's update had
   * flushed, and the loop would hand the same file to the uploader twice.
   */
  const queueRef = React.useRef<QueueItem[]>([]);
  const mutate = React.useCallback(
    (updater: (prev: QueueItem[]) => QueueItem[]) => {
      queueRef.current = updater(queueRef.current);
      setQueue(queueRef.current);
    },
    [],
  );

  const patch = React.useCallback(
    (key: string, next: Partial<QueueItem>) => {
      mutate((prev) =>
        prev.map((item) => (item.key === key ? { ...item, ...next } : item)),
      );
    },
    [mutate],
  );

  const drop = React.useCallback(
    (key: string) => mutate((prev) => prev.filter((item) => item.key !== key)),
    [mutate],
  );

  /**
   * Upload one file at a time.
   *
   * Sequential on purpose: embedding runs locally on the same laptop that is
   * serving this page, and three files racing each other only makes all three
   * slower while making the progress bars meaningless.
   */
  const drain = React.useCallback(async () => {
    if (running.current) return;
    running.current = true;
    try {
      for (;;) {
        const item = queueRef.current.find((q) => q.phase === 'queued');
        if (!item) break;
        const controller = new AbortController();
        patch(item.key, { phase: 'uploading', progress: 0, controller });
        setAnnouncement(`Uploading ${item.file.name}`);
        try {
          const job = await upload.mutateAsync({
            file: item.file,
            onUploadProgress: (fraction) => patch(item.key, { progress: fraction }),
            signal: controller.signal,
          });
          // Accepted: the dataset now exists server-side, so the table owns it
          // from here. Keeping a second progress bar in this tray would be two
          // indicators for one thing.
          drop(item.key);
          setAnnouncement(`${item.file.name} uploaded. Indexing started.`);
          toast({
            title: `${item.file.name} uploaded`,
            description:
              job.message ??
              'Indexing has started. The row below shows its progress.',
            variant: 'success',
          });
        } catch (err) {
          if (err instanceof DOMException && err.name === 'AbortError') {
            drop(item.key);
            setAnnouncement(`${item.file.name} cancelled.`);
            continue;
          }
          const message = describeError(err);
          patch(item.key, { phase: 'failed', error: message, controller: undefined });
          setAnnouncement(`${item.file.name} failed to upload.`);
          toast({
            title: `Could not upload ${item.file.name}`,
            description: message,
            variant: 'destructive',
          });
        }
      }
    } finally {
      running.current = false;
    }
  }, [drop, patch, toast, upload]);

  const add = React.useCallback(
    (files: File[]) => {
      if (!files.length) return;
      const { accepted, rejected: refused } = triage(files, queueRef.current);
      if (refused.length) setRejected((prev) => [...prev, ...refused]);
      if (accepted.length) {
        const items: QueueItem[] = accepted.map((file) => ({
          key: nextKey(),
          file,
          phase: 'queued',
          progress: 0,
        }));
        mutate((prev) => [...prev, ...items]);
        void drain();
      }
    },
    [drain, mutate],
  );

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    add(Array.from(event.dataTransfer.files ?? []));
  };

  const hero = tone === 'hero';

  return (
    <div className={cn('space-y-3', className)}>
      {/*
        The drop target is a plain region, not a button: dropping is an
        enhancement for people with a pointer, and the labelled file input
        below is the path that works with a keyboard, a screen reader and a
        browser with no drag support. Nothing is reachable only by dragging.
      */}
      <div
        onDragEnter={(event) => {
          event.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(event) => {
          // Without this the browser refuses the drop and opens the file.
          event.preventDefault();
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragging(false);
        }}
        onDrop={onDrop}
        className={cn(
          'rounded-lg border border-dashed transition-colors duration-150',
          hero ? 'px-6 py-10 text-center' : 'px-4 py-4',
          dragging ? 'border-primary bg-primary/5' : 'border-border',
        )}
      >
        <div
          className={cn(
            hero
              ? 'flex flex-col items-center'
              : 'flex flex-wrap items-center justify-between gap-x-6 gap-y-3',
          )}
        >
          <div className={cn('min-w-0', hero ? 'flex flex-col items-center' : '')}>
            {hero ? (
              <span
                className="mb-3 flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground"
                aria-hidden
              >
                <FileUp className="size-5" />
              </span>
            ) : null}
            <p className="text-sm font-medium">
              {hero ? 'Upload a file to index' : 'Add another file'}
            </p>
            {/* The limits are stated here, before anything is chosen, rather
                than arriving as a rejection afterwards. */}
            <p
              id={limitsId}
              className={cn(
                'mt-1 text-sm text-muted-foreground',
                hero ? 'max-w-[52ch]' : 'max-w-[72ch]',
              )}
            >
              {UPLOAD_LIMITS_SENTENCE}
            </p>
          </div>

          <div
            className={cn(
              'flex shrink-0 items-center gap-3',
              hero ? 'mt-5 flex-col' : '',
            )}
          >
            <input
              id={inputId}
              type="file"
              multiple
              accept={DATASET_ACCEPT_ATTR}
              aria-describedby={limitsId}
              // `peer` and not `hidden`: the input stays the real focusable
              // control (a <label> is not focusable), so the visible ring has
              // to be driven off the input's focus state. Without this, a
              // keyboard user tabs onto a control with no visible focus at all.
              className="peer sr-only"
              onChange={(event) => {
                add(Array.from(event.target.files ?? []));
                // Reset so choosing the same file twice in a row still fires.
                event.target.value = '';
              }}
            />
            <Button asChild>
              <label
                htmlFor={inputId}
                className="cursor-pointer peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background"
              >
                <Upload className="size-4" aria-hidden />
                Choose files
              </label>
            </Button>
            {/* Dropping is pointer-only, so the invitation is hidden where
                there is no pointer rather than describing a gesture that does
                not exist on the device. */}
            <p className="text-xs text-muted-foreground [@media(pointer:coarse)]:hidden">
              …or drop them anywhere in this box.
            </p>
          </div>
        </div>
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {queue.length ? (
        <ul className="divide-y overflow-hidden rounded-lg border bg-card">
          {queue.map((item) => (
            <li key={item.key} className="px-4 py-3">
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{item.file.name}</p>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    {formatDatasetBytes(item.file.size)}
                    {item.phase === 'uploading'
                      ? ` · sending ${formatProgressPercent(item.progress)}`
                      : item.phase === 'queued'
                        ? ' · waiting for the file before it'
                        : ''}
                  </p>
                </div>
                {item.phase === 'uploading' ? (
                  <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
                ) : null}
                {item.phase === 'failed' ? (
                  <>
                    {/* A failed *upload* can genuinely succeed on a second
                        attempt — the request never reached the indexer — so
                        unlike a 403 this retry is not a dead button. */}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        patch(item.key, {
                          phase: 'queued',
                          progress: 0,
                          error: undefined,
                        });
                        void drain();
                      }}
                    >
                      Retry
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Dismiss ${item.file.name}`}
                      onClick={() => drop(item.key)}
                    >
                      <X className="size-4" aria-hidden />
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Cancel ${item.file.name}`}
                    onClick={() => {
                      item.controller?.abort();
                      if (item.phase === 'queued') drop(item.key);
                    }}
                  >
                    <X className="size-4" aria-hidden />
                  </Button>
                )}
              </div>
              {item.phase === 'uploading' ? (
                <DatasetProgress
                  className="mt-2.5"
                  value={item.progress}
                  label={`Sending ${item.file.name}`}
                />
              ) : null}
              {item.phase === 'failed' && item.error ? (
                <p className="mt-1.5 text-xs text-destructive">{item.error}</p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {rejected.length ? (
        // Persistent, not a toast: the reader has to be able to read four
        // reasons, compare them against four filenames, and fix them.
        <div
          role="alert"
          className="rounded-lg border border-warning/30 bg-warning/5 px-4 py-3"
        >
          <div className="flex items-start justify-between gap-3">
            <p className="flex items-center gap-2 text-sm font-medium text-foreground">
              <TriangleAlert className="size-4 text-warning" aria-hidden />
              {rejected.length === 1
                ? 'One file was not uploaded'
                : `${rejected.length} files were not uploaded`}
            </p>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Dismiss the list of files that were not uploaded"
              onClick={() => setRejected([])}
            >
              <X className="size-4" aria-hidden />
            </Button>
          </div>
          <ul className="mt-2 space-y-1.5">
            {rejected.map((entry) => (
              <li key={entry.key} className="text-sm">
                <span className="break-words font-medium">{entry.name}</span>
                <span className="text-muted-foreground"> — {entry.reason}</span>
              </li>
            ))}
          </ul>
          {rejected.some((entry) => /accepted (formats|extensions)/.test(entry.reason)) ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Accepted: {ACCEPTED_SUMMARY}.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
