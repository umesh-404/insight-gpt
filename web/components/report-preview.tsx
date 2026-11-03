import { ChartRenderer } from '@/components/chart-renderer';
import { CitationList } from '@/components/citation-list';
import { DataTable } from '@/components/data-table';
import { formatDateTime } from '@/lib/utils';
import type { Report } from '@/lib/types';

/**
 * Paged, document-like report preview that mirrors the eventual PDF
 * (docs/07 §4.4).
 *
 * Deliberately *not* styled like the rest of the app: this is the one surface
 * an executive is handed as a finished artefact, so it uses document furniture
 * (masthead, contents, numbered sections, a source list per section) rather
 * than the instrument-panel chrome of the dashboards. Everything on it is a
 * fact the API returned — there is no sample content anywhere.
 */
export function ReportPreview({ report }: { report: Report }) {
  const period = `${report.period.start || '—'} — ${report.period.end || '—'}`;

  return (
    <article className="mx-auto max-w-3xl rounded-lg border bg-card shadow-soft">
      <header className="border-b px-6 py-8 sm:px-12 sm:py-10">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
          Executive report
        </p>
        {/* The title is the single dominant element; the metadata that used to
            sit above it now sits below, where it reads as a caption rather
            than competing with the heading. */}
        <h1 className="mt-3 text-balance text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
          {report.title}
        </h1>
        <dl className="mt-6 grid grid-cols-1 gap-x-8 gap-y-3 border-t pt-5 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted-foreground">Period</dt>
            <dd className="mt-0.5 font-medium tabular-nums">{period}</dd>
          </div>
          {report.period.grain ? (
            <div>
              <dt className="text-xs text-muted-foreground">Grain</dt>
              <dd className="mt-0.5 font-medium capitalize">{report.period.grain}</dd>
            </div>
          ) : null}
          <div>
            <dt className="text-xs text-muted-foreground">Generated</dt>
            <dd className="mt-0.5 font-medium">{formatDateTime(report.created_at)}</dd>
          </div>
        </dl>

        {/* A contents list only earns its place once there is more than one
            section to navigate between. */}
        {report.blocks.length > 1 ? (
          <nav aria-label="Report contents" className="mt-6 border-t pt-5">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Contents
            </p>
            <ol className="mt-2 space-y-1 text-sm">
              {report.blocks.map((block, i) => (
                <li key={block.id} className="flex gap-3">
                  <span className="w-5 shrink-0 tabular-nums text-muted-foreground">
                    {i + 1}.
                  </span>
                  <a
                    href={`#${block.id}`}
                    className="rounded text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {block.heading}
                  </a>
                </li>
              ))}
            </ol>
          </nav>
        ) : null}
      </header>

      <div className="divide-y px-6 sm:px-12">
        {report.blocks.length === 0 ? (
          <div className="py-10">
            <p className="text-sm font-medium">This report has no sections</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Generate it again with at least one section selected.
            </p>
          </div>
        ) : null}

        {report.blocks.map((block, i) => (
          <section
            key={block.id}
            id={block.id}
            // `break-inside-avoid` keeps a section whole when the browser
            // prints this preview; `scroll-mt` keeps the contents links from
            // parking a heading under the sticky app header.
            className="scroll-mt-20 break-inside-avoid py-8 first:pt-10 last:pb-10"
          >
            <h2 className="flex gap-3 text-xl font-semibold tracking-tight">
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {i + 1}.
              </span>
              <span className="min-w-0">{block.heading}</span>
            </h2>

            {/* Capped at a readable measure instead of the full card width:
                report prose is read, not scanned. */}
            {block.prose ? (
              <p className="mt-3 max-w-[66ch] text-[15px] leading-7 text-foreground">
                {block.prose}
              </p>
            ) : null}

            {block.chart_spec && (block.chart_spec.data?.length ?? 0) > 0 ? (
              <figure className="mt-6">
                <div className="rounded-lg border p-4">
                  <ChartRenderer spec={block.chart_spec} height={240} />
                </div>
              </figure>
            ) : null}

            {block.tables?.length ? (
              <div className="mt-6 space-y-4">
                {block.tables.map((table, t) => (
                  <DataTable key={`${block.id}-t${t}`} block={table} />
                ))}
              </div>
            ) : null}

            {block.citations && block.citations.length ? (
              <div className="mt-6 border-t pt-4">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Sources for this section
                </p>
                <div className="mt-2">
                  <CitationList citations={block.citations} />
                </div>
              </div>
            ) : null}
          </section>
        ))}
      </div>
    </article>
  );
}
