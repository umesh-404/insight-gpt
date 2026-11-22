'use client';

import * as React from 'react';
import { Lock, ShieldCheck } from 'lucide-react';
import { Card, CardSection } from '@/components/ui/card';
import { LoadingRegion, SkeletonTable } from '@/components/ui/skeleton';
import { EmptyState, ErrorState } from '@/components/states';
import { DatasetPipelineExplainer } from '@/components/dataset-explainer';
import { DatasetTable } from '@/components/dataset-table';
import { DatasetUpload } from '@/components/dataset-upload';
import { useDatasets } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { formatNumber } from '@/lib/utils';
import { ApiError, isTerminalDatasetStatus } from '@/lib/types';

/**
 * Uploaded datasets: the upload affordance, the list, and the explanation of
 * what an upload actually does.
 *
 * Roles are decided here, before anything is fetched. `GET /datasets` needs
 * viewer and `POST`/`DELETE` need analyst, so a viewer gets the list and no
 * upload UI at all — not a greyed-out dropzone, which would be an invitation
 * this account cannot accept.
 */
export function DatasetPanel() {
  const { hasRole, loading } = useAuth();
  const canRead = hasRole('viewer');
  const canUpload = hasRole('analyst');

  // Gated at the fetch: mounting the query for an account that cannot read
  // datasets would fire a request guaranteed to 403.
  const datasets = useDatasets(!loading && canRead);

  if (loading) {
    return (
      <LoadingRegion label="Loading datasets…" className="space-y-3">
        <SkeletonTable rows={3} columns={5} numericFrom={3} />
      </LoadingRegion>
    );
  }

  if (!canRead) {
    return (
      <EmptyState
        title="Datasets need the viewer role"
        description="Reading the list of indexed files is restricted to signed-in users with at least the viewer role. Ask an administrator to raise your role."
        icon={<Lock className="size-5" aria-hidden />}
      />
    );
  }

  const list = datasets.data ?? [];
  const isEmpty = !datasets.isLoading && !datasets.isError && list.length === 0;
  const ready = list.filter((d) => d.status === 'ready');
  const totalChunks = ready.reduce((sum, d) => sum + d.chunks, 0);
  const totalRedactions = ready.reduce((sum, d) => sum + d.redactions, 0);
  const working = list.filter((d) => !isTerminalDatasetStatus(d.status)).length;

  return (
    <div className="min-w-0 max-w-full space-y-6">
      {/* The hero state is the demo's first impression, so on an empty index
          the drop target and the explanation are one block rather than a
          dropzone sitting above a separate dashed "nothing here" panel. */}
      {isEmpty ? (
        canUpload ? (
          <Card variant="flat">
            <CardSection
              title="No files indexed yet"
              description="Upload one and it becomes searchable in Ask. Here is exactly what happens to it."
            >
              <div className="mt-4 space-y-6">
                <DatasetUpload tone="hero" />
                <DatasetPipelineExplainer />
              </div>
            </CardSection>
          </Card>
        ) : (
          <Card variant="flat">
            <CardSection
              title="No files indexed yet"
              description="Uploading a file needs the analyst role, so nothing here is yours to add. When an analyst uploads one, this is what happens to it — and what you will be able to search."
            >
              <div className="mt-4">
                <DatasetPipelineExplainer />
              </div>
            </CardSection>
          </Card>
        )
      ) : (
        <>
          {canUpload ? <DatasetUpload tone="compact" /> : null}

          <section aria-labelledby="datasets-heading" className="min-w-0 space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h3
                id="datasets-heading"
                className="text-xs font-medium uppercase tracking-wider text-muted-foreground"
              >
                Indexed files
              </h3>
              {list.length ? (
                // The redaction total sits in the section header, not only in a
                // cell: "22 secrets removed before indexing" is the claim this
                // product most needs a reader to see without hunting for it.
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground tabular-nums">
                  <span>
                    {list.length} {list.length === 1 ? 'dataset' : 'datasets'}
                  </span>
                  <span aria-hidden>·</span>
                  <span>{formatNumber(totalChunks)} passages indexed</span>
                  <span aria-hidden>·</span>
                  <span className="inline-flex items-center gap-1 font-medium text-foreground">
                    <ShieldCheck className="size-3.5 text-success" aria-hidden />
                    {formatNumber(totalRedactions)} secrets removed before indexing
                  </span>
                  {working ? (
                    <>
                      <span aria-hidden>·</span>
                      <span>{working} still indexing</span>
                    </>
                  ) : null}
                </p>
              ) : null}
            </div>

            {datasets.isLoading ? (
              <LoadingRegion label="Loading datasets…">
                <SkeletonTable rows={3} columns={6} numericFrom={3} />
              </LoadingRegion>
            ) : datasets.isError ? (
              <ErrorState
                error={datasets.error}
                // A 403 is this account's permanent answer. Offering a retry
                // would be a button that can never succeed.
                onRetry={
                  datasets.error instanceof ApiError && datasets.error.status === 403
                    ? undefined
                    : () => void datasets.refetch()
                }
                retryLabel="Reload datasets"
              />
            ) : (
              <DatasetTable datasets={list} canDelete={canUpload} />
            )}
          </section>

          {/* Once the table carries the story, the explanation moves out of the
              way but stays on the page: it is the honest account of where an
              answer's evidence came from, not onboarding to be dismissed. */}
          <details className="group rounded-lg border bg-card">
            {/* `list-none` alone leaves Safari's own triangle in place. */}
            <summary className="cursor-pointer list-none px-5 py-3.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              What happens to an uploaded file
              <span className="ml-2 font-normal text-muted-foreground group-open:hidden">
                Show
              </span>
              <span className="ml-2 hidden font-normal text-muted-foreground group-open:inline">
                Hide
              </span>
            </summary>
            <div className="border-t px-5 py-5">
              <DatasetPipelineExplainer />
            </div>
          </details>
        </>
      )}
    </div>
  );
}
