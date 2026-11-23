'use client';

import * as React from 'react';
import { ExternalLink, FileText, Paperclip } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn, formatDate } from '@/lib/utils';
import type { Citation } from '@/lib/types';

/**
 * Human labels for the wire's `source_type`.
 *
 * The enum went straight to the screen, so a passage from a file someone
 * uploaded was badged "dataset" — lower-case, singular, and a word for the
 * row in a registry rather than for the thing the reader recognises. Anything
 * unmapped still falls through verbatim: a label we have not written is
 * better than one we have guessed.
 */
const SOURCE_TYPE_LABEL: Record<string, string> = {
  attachment: 'Attachment',
  dataset: 'Uploaded file',
  document: 'Document',
  review: 'Review',
  ticket: 'Support ticket',
};

/** True for sources the *user* supplied, as opposed to the governed corpus. */
function isUserSupplied(sourceType: string): boolean {
  return sourceType === 'attachment' || sourceType === 'dataset';
}

/**
 * Tidy one retrieved passage for reading.
 *
 * Two things, and no more — this is evidence, so nothing is summarised,
 * reordered or paraphrased:
 *
 *  - The indexer prepends a `File: <name>` header to the first chunk of every
 *    file. The citation already shows that name as its title directly above,
 *    so the header is the same fact twice; it is dropped only when it really
 *    does repeat the title.
 *  - The rest is returned unchanged and rendered with its own line breaks
 *    intact, because a chunk of a CSV is a stack of table rows and collapsing
 *    the newlines turned it into one unreadable ribbon of pipes.
 */
function readableSnippet(snippet: string, title: string): string {
  const text = snippet.trim();
  const prefix = `File: ${title}`;
  if (!text.toLowerCase().startsWith(prefix.toLowerCase())) return text;
  // Keep anything the header itself carried that the title does not, such as
  // "(CSV, 60 data rows, 5 columns)".
  const rest = text.slice(prefix.length).replace(/^[\s:–—-]+/, '');
  return rest || text;
}

/**
 * The retrieved passage itself.
 *
 * Clamped to six lines, because one 40-line chunk of a spreadsheet pushed
 * every other source off the screen and made a list of five citations look
 * like one. The clamp is visual only: the whole passage is in the DOM, so
 * find-in-page and a screen reader still reach all of it, and the control
 * says how it is truncated rather than hiding that anything was cut.
 */
function Snippet({ text }: { text: string }) {
  const [expanded, setExpanded] = React.useState(false);
  const [clamped, setClamped] = React.useState(false);
  const ref = React.useRef<HTMLParagraphElement>(null);

  // Only offer the control when the text is actually longer than the clamp;
  // a "Show the full passage" button under three readable lines is a button
  // that does nothing visible.
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () =>
      setClamped(el.scrollHeight - el.clientHeight > 2 || expanded);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, expanded]);

  return (
    <div className="mt-1.5">
      <p
        ref={ref}
        className={cn(
          // `whitespace-pre-line` keeps the passage's own line breaks: a chunk
          // of a CSV is a stack of rows, and collapsing them ran the whole
          // table into a single ribbon of pipe characters.
          'max-w-[80ch] whitespace-pre-line break-words text-sm leading-relaxed text-muted-foreground',
          expanded ? null : 'line-clamp-6',
        )}
      >
        {text}
      </p>
      {clamped ? (
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className="mt-1 rounded text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {expanded ? 'Show less' : 'Show the whole passage'}
        </button>
      ) : null}
    </div>
  );
}

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
          const userSupplied = isUserSupplied(citation.source_type);
          const label =
            SOURCE_TYPE_LABEL[citation.source_type] ?? citation.source_type;
          const snippet = citation.snippet
            ? readableSnippet(citation.snippet, citation.title)
            : null;
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
                    {userSupplied ? (
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
                    {/* `basis-48` rather than `flex-1`: with a zero basis the
                        badge always sat beside the title, so a filename in a
                        375px column was broken mid-word ("quarterly_sale /
                        s.csv") to make room for it. Below 12rem the badge
                        wraps to its own line instead and the name stays
                        whole. */}
                    <p className="min-w-0 grow basis-48 break-words text-sm font-medium text-foreground">
                      {citation.title}
                    </p>
                    <Badge variant={userSupplied ? 'default' : 'muted'}>
                      {label}
                    </Badge>
                  </div>

                  {snippet ? <Snippet text={snippet} /> : null}

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
