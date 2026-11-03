'use client';

import * as React from 'react';
import Link from 'next/link';
import { ArrowLeft, Download, FileDown, Loader2 } from 'lucide-react';
import { ReportPreview } from '@/components/report-preview';
import { EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useReport } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { ApiError, type ReportFormat } from '@/lib/types';

function BackLink() {
  return (
    <Button asChild variant="ghost" size="sm" className="-ml-2">
      <Link href="/reports">
        <ArrowLeft className="size-4" aria-hidden /> Back to reports
      </Link>
    </Button>
  );
}

export default function ReportDetailPage({ params }: { params: { id: string } }) {
  const { hasRole, loading } = useAuth();

  if (loading) {
    return (
      <div className="space-y-6 p-4 sm:p-6" aria-busy="true">
        <BackLink />
        <Skeleton className="mx-auto h-96 w-full max-w-3xl" />
        <span className="sr-only">Loading report…</span>
      </div>
    );
  }

  // `GET /reports/{id}` is analyst-gated, exactly like `GET /reports`. The role
  // is checked before the query is mounted so a viewer who follows a shared
  // link never fires a request that can only 403.
  if (!hasRole('analyst')) {
    return (
      <div className="space-y-6 p-4 sm:p-6">
        <BackLink />
        <EmptyState
          title="Reports need the analyst role"
          description="Reading a report is restricted to analysts and administrators. Ask an administrator to raise your role."
        />
      </div>
    );
  }

  return <ReportDetail id={params.id} />;
}

function ReportDetail({ id }: { id: string }) {
  const { toast } = useToast();
  const { data: report, isLoading, isError, error, refetch } = useReport(id);
  const [exporting, setExporting] = React.useState<ReportFormat | null>(null);

  /**
   * The export endpoint is bearer-authenticated, so it cannot be opened as a
   * plain link (a new tab carries no Authorization header). Fetch it, then hand
   * the browser a blob.
   *
   * The report title is passed only as a *fallback* slug: the real filename
   * comes from the response's `Content-Disposition`, because naming the file
   * after the opaque report id gave the reader a download they could not
   * identify later.
   */
  const onExport = async (format: ReportFormat) => {
    setExporting(format);
    try {
      await api.downloadReport(id, format, report?.title);
    } catch (err) {
      const isMissingRenderer =
        err instanceof ApiError && err.status === 400 && format === 'pdf';
      toast({
        title: isMissingRenderer ? 'PDF export unavailable' : 'Export failed',
        description: isMissingRenderer
          ? `${err.message} Try the Markdown export instead.`
          : err instanceof Error
            ? err.message
            : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setExporting(null);
    }
  };

  const status = error instanceof ApiError ? error.status : undefined;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <BackLink />
        {/* Export only exists for a report that has content to export: a
            generating or failed report has nothing behind the button. */}
        {report?.status === 'ready' ? (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={exporting !== null}
              onClick={() => void onExport('markdown')}
            >
              {exporting === 'markdown' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <FileDown className="size-4" aria-hidden />
              )}
              Markdown
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={exporting !== null}
              onClick={() => void onExport('pdf')}
            >
              {exporting === 'pdf' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Download className="size-4" aria-hidden />
              )}
              Export PDF
            </Button>
          </div>
        ) : null}
      </div>

      {isLoading ? (
        <div aria-busy="true">
          <Skeleton className="mx-auto h-96 w-full max-w-3xl" />
          <span className="sr-only">Loading report…</span>
        </div>
      ) : isError && status === 404 ? (
        <EmptyState
          title="Report not found"
          description="This report id does not exist. It may have been removed, or it may belong to another account."
          action={
            <Button asChild variant="outline" size="sm">
              <Link href="/reports">See your reports</Link>
            </Button>
          }
        />
      ) : isError || !report ? (
        // A 403 is a permanent answer for this role (reports are analyst-gated),
        // so a "Try again" button here can never succeed.
        <ErrorState
          error={error}
          onRetry={status === 403 ? undefined : () => void refetch()}
        />
      ) : report.status === 'generating' ? (
        <EmptyState
          title="Generating this report…"
          description="This page refreshes on its own as soon as the report is ready."
          icon={<Loader2 className="size-5 animate-spin" aria-hidden />}
        />
      ) : report.status === 'failed' ? (
        // The report exists but has no content: say so with the engine's own
        // reason rather than rendering an empty document that looks finished.
        <ErrorState
          error={{
            message:
              report.error ??
              'Report generation failed. Generate it again, or narrow the period.',
          }}
        />
      ) : (
        <ReportPreview report={report} />
      )}
    </div>
  );
}
