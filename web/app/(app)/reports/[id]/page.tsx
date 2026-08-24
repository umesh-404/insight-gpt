'use client';

import Link from 'next/link';
import { ArrowLeft, Download, Loader2 } from 'lucide-react';
import { ReportPreview } from '@/components/report-preview';
import { EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { useReport } from '@/lib/hooks';
import { api, USE_MOCK } from '@/lib/api';

export default function ReportDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const { toast } = useToast();
  const { data: report, isLoading, isError, error, refetch } = useReport(params.id);

  const onExport = () => {
    if (USE_MOCK) {
      // The real export streams a server-rendered PDF; there is no backend here.
      toast({
        title: 'PDF export',
        description:
          'In a live deployment this downloads the server-rendered PDF, byte-for-byte matching this preview.',
      });
      return;
    }
    window.open(api.reportExportUrl(params.id), '_blank', 'noopener');
  };

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <div className="flex items-center justify-between">
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link href="/reports">
            <ArrowLeft className="size-4" /> Back to reports
          </Link>
        </Button>
        {report?.status === 'ready' ? (
          <Button variant="outline" size="sm" onClick={onExport}>
            <Download className="size-4" /> Export PDF
          </Button>
        ) : null}
      </div>

      {isLoading ? (
        <div className="flex h-[50vh] items-center justify-center" aria-busy="true">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        </div>
      ) : isError || !report ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : report.status === 'generating' ? (
        <EmptyState
          title="Generating your report…"
          description="This page refreshes automatically when the report is ready."
          icon={<Loader2 className="size-5 animate-spin" />}
        />
      ) : report.status === 'failed' ? (
        <ErrorState error={{ message: 'Report generation failed. Try regenerating it.' }} />
      ) : (
        <ReportPreview report={report} />
      )}
    </div>
  );
}
