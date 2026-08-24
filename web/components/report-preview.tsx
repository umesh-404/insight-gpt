import { CalendarRange } from 'lucide-react';
import { ChartRenderer } from '@/components/chart-renderer';
import { CitationList } from '@/components/citation-list';
import { DataTable } from '@/components/data-table';
import { Badge } from '@/components/ui/badge';
import { formatDateTime } from '@/lib/utils';
import type { Report } from '@/lib/types';

/** Paged, document-like report preview that mirrors the eventual PDF (docs/07 §4.4). */
export function ReportPreview({ report }: { report: Report }) {
  return (
    <article className="mx-auto max-w-3xl rounded-lg border bg-card p-8 shadow-soft sm:p-12">
      <header className="border-b pb-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <CalendarRange className="size-4" aria-hidden />
          <span>
            {report.period.start || '—'} — {report.period.end || '—'}
          </span>
          {report.period.grain ? (
            <>
              <span aria-hidden>·</span>
              <span className="capitalize">{report.period.grain}</span>
            </>
          ) : null}
        </div>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">
          {report.title}
        </h1>
        <p className="mt-2 text-xs text-muted-foreground">
          Generated {formatDateTime(report.created_at)}
        </p>
      </header>

      <div className="mt-8 space-y-10">
        {report.blocks.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            This report has no sections yet.
          </p>
        ) : null}
        {report.blocks.map((block) => (
          <section key={block.id} className="break-inside-avoid">
            <h2 className="text-xl font-semibold tracking-tight">
              {block.heading}
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-foreground">
              {block.prose}
            </p>
            {block.chart_spec && (block.chart_spec.data?.length ?? 0) > 0 ? (
              <div className="mt-5 rounded-lg border p-4">
                <ChartRenderer spec={block.chart_spec} height={240} />
              </div>
            ) : null}
            {block.tables?.length ? (
              <div className="mt-5 space-y-4">
                {block.tables.map((table, i) => (
                  <DataTable key={`${block.id}-t${i}`} block={table} />
                ))}
              </div>
            ) : null}
            {block.citations && block.citations.length ? (
              <div className="mt-5">
                <div className="mb-2 flex items-center gap-2">
                  <Badge variant="muted">Sources</Badge>
                </div>
                <CitationList citations={block.citations} />
              </div>
            ) : null}
          </section>
        ))}
      </div>
    </article>
  );
}
