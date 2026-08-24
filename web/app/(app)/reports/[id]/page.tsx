'use client';

import * as React from 'react';
import Link from 'next/link';
import { ArrowLeft, Download, FileDown, Loader2 } from 'lucide-react';
import { ReportPreview } from '@/components/report-preview';
import { EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { useReport } from '@/lib/hooks';
import { api } from '@/lib/api';
import { ApiError, type ReportFormat } from '@/lib/types';

export default function ReportDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const { toast } = useToast();
  const { data: report, isLoading, isError, error, refetch } = useReport(params.id);
  const [exporting, setExporting] = React.useState<ReportFormat | null>(null);

  /**
   * The export endpoint is bearer-authenticated, so it cannot be opened as a
   * plain link (a new tab carries no Authorization header). Fetch it, then hand
   * the browser a blob.
   */
  const onExport = async (format: ReportFormat) => {
    setExporting(format);
    try {
      await api.downloadReport(params.id, format, report?.title);
    } catch (err) {
      const isMissingRenderer =
        err instanceof ApiError && err.status === 400 && format === 'pdf';
      toast({
        title: isMissingRenderer
          ? 'PDF export unavailable'
          : 'Export failed',
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

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link href="/reports">
            <ArrowLeft className="size-4" aria-hidden /> Back to reports
          </Link>
        </Button>
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
        <div className="flex h-[50vh] items-center justify-center" aria-busy="true">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
          <span className="sr-only">Loading report…</span>
        </div>
      ) : isError && error instanceof ApiError && error.status === 404 ? (
        <EmptyState
          title="Report not found"
          description="It may have been removed, or it belongs to another account."
          action={
            <Button asChild variant="outline" size="sm">
              <Link href="/reports">Back to reports</Link>
            </Button>
          }
        />
      ) : isError || !report ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : report.status === 'generating' ? (
        <EmptyState
          title="Generating your report…"
          description="This page refreshes automatically when the report is ready."
          icon={<Loader2 className="size-5 animate-spin" aria-hidden />}
        />
      ) : report.status === 'failed' ? (
        <ErrorState
          error={{
            message:
              report.error ?? 'Report generation failed. Try regenerating it.',
          }}
        />
      ) : (
        <ReportPreview report={report} />
      )}
    </div>
  );
}
