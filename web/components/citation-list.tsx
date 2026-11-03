import { ExternalLink, FileText, Paperclip } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { formatDate } from '@/lib/utils';
import type { Citation } from '@/lib/types';

/**
 * Grounding documents behind an answer. `snippet`, `score`, `date`, and `uri`
 * are all optional on the wire, so each is rendered only when present.
 *
 * Two honesty rules shape this list, and both are visible in the UI:
 *
 *  - **The score is a retrieval score.** It says how closely the document
 *    matched the *question*; it is not a confidence in the answer, and it is
 *    labelled so nobody can read it as one. The envelope's `confidence` field
 *    is the only thing that grades the answer.
 *  - **Dates are shown, never tidied away.** Retrieval treats the requested
 *    time range as a soft preference, so a 2024 document can legitimately rank
 *    into a 2026 report. Hiding the date would make that look like a bug
 *    instead of a disclosed limitation, so the date stays and the footnote
 *    explains it.
 */
export function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations.length) {
    return (
      <p className="text-sm text-muted-foreground">
        This answer has no document grounding.
      </p>
    );
  }

  const hasScores = citations.some((c) => typeof c.score === 'number');
  const hasDates = citations.some((c) => Boolean(c.date));

  return (
    <div>
      <p className="pb-2 text-xs text-muted-foreground">
        {citations.length === 1 ? '1 document' : `${citations.length} documents`}{' '}
        grounded this answer
        {hasScores
          ? '. Match ranks a document against the question — it is not a confidence in the answer.'
          : '.'}
      </p>

      {/* Hairline rows, not nested cards: this list already sits inside a card. */}
      <ol className="divide-y border-y">
        {citations.map((citation, i) => {
          const isAttachment = citation.source_type === 'attachment';
          const score =
            typeof citation.score === 'number'
              ? Math.round(citation.score * 100)
              : null;
          return (
            <li
              key={`${citation.doc_id}-${citation.n}-${i}`}
              // Targeted by the inline `[n]` chips in an answer, which scroll
              // the matching source into view and focus it.
              id={`citation-${citation.n}`}
              tabIndex={-1}
              className="scroll-mt-6 px-0.5 py-3 transition-colors duration-150 ease-out target:bg-primary/[0.06] focus:bg-primary/[0.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <div className="flex items-start gap-3">
                <span
                  className="mt-px flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-2xs font-semibold tabular-nums text-primary"
                  aria-hidden
                >
                  {citation.n}
                </span>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    {isAttachment ? (
                      <Paperclip
                        className="size-3.5 shrink-0 translate-y-0.5 text-primary"
                        aria-hidden
                      />
                    ) : (
                      <FileText
                        className="size-3.5 shrink-0 translate-y-0.5 text-muted-foreground"
                        aria-hidden
                      />
                    )}
                    <p className="min-w-0 flex-1 text-sm font-medium text-foreground">
                      {citation.title}
                    </p>
                    <Badge variant={isAttachment ? 'default' : 'muted'}>
                      {isAttachment ? 'Attachment' : citation.source_type}
                    </Badge>
                  </div>

                  {citation.snippet ? (
                    <p className="mt-1.5 max-w-[80ch] text-sm leading-relaxed text-muted-foreground">
                      {citation.snippet}
                    </p>
                  ) : null}

                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span className="font-mono">{citation.doc_id}</span>
                    {citation.date ? (
                      <span className="tabular-nums">
                        {formatDate(citation.date)}
                      </span>
                    ) : null}
                    {score !== null ? (
                      <span
                        className="inline-flex items-center gap-1.5"
                        title="Retrieval match: how closely this document matched the question. Not a confidence in the answer."
                      >
                        <span
                          className="h-1 w-10 overflow-hidden rounded-full bg-border"
                          aria-hidden
                        >
                          <span
                            className="block h-full rounded-full bg-primary/60"
                            style={{ width: `${Math.max(score, 2)}%` }}
                          />
                        </span>
                        <span className="tabular-nums">{score}% match</span>
                      </span>
                    ) : null}
                    {citation.uri ? (
                      <a
                        href={citation.uri}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        Open <ExternalLink className="size-3" aria-hidden />
                      </a>
                    ) : null}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {hasDates ? (
        <p className="pt-2 text-xs text-muted-foreground">
          A requested date range is a ranking preference, not a hard filter, so a
          document from outside the period can still rank highly. Check the dates
          before quoting a source.
        </p>
      ) : null}
    </div>
  );
}
