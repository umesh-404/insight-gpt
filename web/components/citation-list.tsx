import { ExternalLink, FileText } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { formatDate } from '@/lib/utils';
import type { Citation } from '@/lib/types';

/**
 * Grounding documents behind an answer. `snippet`, `score`, `date`, and `uri`
 * are all optional on the wire, so each is rendered only when present.
 */
export function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations.length) {
    return (
      <p className="text-sm text-muted-foreground">
        This answer has no document grounding.
      </p>
    );
  }
  return (
    <ol className="space-y-3">
      {citations.map((citation, i) => (
        <li
          key={`${citation.doc_id}-${citation.n}-${i}`}
          className="rounded-lg border bg-card p-4 transition-colors hover:border-primary/40"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-2.5">
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
                {citation.n}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <FileText
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                  <p className="truncate text-sm font-medium text-foreground">
                    {citation.title}
                  </p>
                </div>
                {citation.snippet ? (
                  <p className="mt-1.5 text-sm text-muted-foreground">
                    {citation.snippet}
                  </p>
                ) : null}
                <p className="mt-1.5 font-mono text-xs text-muted-foreground">
                  {citation.doc_id}
                  {citation.date ? ` · ${formatDate(citation.date)}` : ''}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1.5">
              <Badge variant="muted">{citation.source_type}</Badge>
              {typeof citation.score === 'number' ? (
                <span
                  className="text-xs tabular-nums text-muted-foreground"
                  title="Rerank relevance score"
                >
                  {Math.round(citation.score * 100)}% match
                </span>
              ) : null}
              {citation.uri ? (
                <a
                  href={citation.uri}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  Open <ExternalLink className="size-3" aria-hidden />
                </a>
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
