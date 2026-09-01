'use client';

import * as React from 'react';
import {
  BarChart3,
  Code2,
  FileText,
  HelpCircle,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ChartRenderer } from '@/components/chart-renderer';
import { SqlViewer } from '@/components/sql-viewer';
import { CitationList } from '@/components/citation-list';
import { CaveatNote } from '@/components/caveat-note';
import { DataTable } from '@/components/data-table';
import {
  AbstainPanel,
  NoDataPanel,
  SelfCorrectedNote,
} from '@/components/answer-states';
import { cn } from '@/lib/utils';
import {
  isNoDataEnvelope,
  type AnswerEnvelope,
  type ApiErrorBody,
  type Confidence,
  type Route,
} from '@/lib/types';

/**
 * How the answer was produced. Phrased as provenance ("from SQL", "from
 * documents") rather than as internal route names, because the reader cares
 * where the numbers came from, not what the router called itself.
 */
const ROUTE_LABEL: Record<Route, string> = {
  structured: 'From SQL',
  unstructured: 'From documents',
  hybrid: 'SQL + documents',
  clarify: 'Needs clarification',
  abstain: 'Withheld',
};

const CONFIDENCE_CLASS: Record<Confidence, string> = {
  high: 'bg-success/10 text-success',
  medium: 'bg-muted text-muted-foreground',
  low: 'bg-warning/10 text-warning',
};

/* -------------------------------------------------------------------------- */
/* Answer body with inline citation chips                                     */
/* -------------------------------------------------------------------------- */

/**
 * Render `[1]`-style markers in the narrative as buttons that jump to the
 * matching source. Grounding is the product's core claim, so a citation has to
 * be *reachable* from the sentence that relies on it — not just listed below.
 *
 * Markers with no matching citation stay plain text: mid-stream, the narrative
 * arrives before the `citations` frame, and a dead chip would flicker in.
 */
function AnswerText({
  text,
  citationNumbers,
  onCite,
}: {
  text: string;
  citationNumbers: Set<number>;
  onCite?: (n: number) => void;
}) {
  const nodes = React.useMemo(() => {
    const out: React.ReactNode[] = [];
    const pattern = /\[(\d{1,3})\]/g;
    let last = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(text)) !== null) {
      const n = Number(match[1]);
      if (!citationNumbers.has(n)) continue;
      if (match.index > last) out.push(text.slice(last, match.index));
      out.push(
        <button
          key={`cite-${match.index}-${n}`}
          type="button"
          onClick={() => onCite?.(n)}
          aria-label={`Jump to source ${n}`}
          className="mx-px inline-flex min-w-[1.25rem] translate-y-[-1px] items-center justify-center rounded border border-primary/25 bg-primary/10 px-1 align-middle text-2xs font-semibold tabular-nums text-primary transition-colors hover:bg-primary hover:text-primary-foreground"
        >
          {n}
        </button>,
      );
      last = match.index + match[0].length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }, [text, citationNumbers, onCite]);

  return <>{nodes}</>;
}

/** Three rising dots shown before the first token lands. */
function ThinkingIndicator() {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 animate-thinking-dot rounded-full bg-primary"
            style={{ animationDelay: `${i * 140}ms` }}
          />
        ))}
      </span>
      Routing the question and compiling governed SQL…
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Card                                                                       */
/* -------------------------------------------------------------------------- */

export interface InsightCardProps {
  question: string;
  envelope: AnswerEnvelope;
  /** True while tokens are still streaming into `envelope.answer`. */
  streaming?: boolean;
  /** Mid-stream failure; rendered in place of the missing tail of the answer. */
  error?: ApiErrorBody | null;
  messageId?: string;
  feedback?: 'up' | 'down' | null;
  onFeedback?: (rating: 'up' | 'down') => void;
  /** Re-ask a suggested governed metric from the abstention panel. */
  onAsk?: (question: string) => void;
}

export function InsightCard({
  question,
  envelope,
  streaming,
  error,
  feedback,
  onFeedback,
  onAsk,
}: InsightCardProps) {
  const [tab, setTab] = React.useState('answer');

  const chart = envelope.chart_spec;
  const hasChart = Boolean(chart && (chart.data?.length ?? 0) > 0);
  const sqlStatements = envelope.sql ?? [];
  const hasSql = sqlStatements.length > 0;
  // Memoized because the `?? []` fallback would otherwise be a fresh array on
  // every render, invalidating the citation-number set below each time.
  const citations = React.useMemo(
    () => envelope.citations ?? [],
    [envelope.citations],
  );
  const tables = envelope.tables ?? [];
  const attempts = envelope.attempts ?? [];
  const hasCitations = citations.length > 0;
  const route = envelope.route;
  const abstained = envelope.abstained === true;
  const noData = !streaming && isNoDataEnvelope(envelope);

  // The abstention panel already states the reason; repeating the "no rows"
  // caveat under it would say the same thing twice.
  const caveats = noData
    ? (envelope.caveats ?? []).filter((c) => !/no rows matched/i.test(c))
    : (envelope.caveats ?? []);

  // Only tables with rows are worth rendering; a no-data envelope carries the
  // executed query's empty result, which the NoDataPanel explains better.
  const shownTables = tables.filter((t) => t.rows.length > 0);

  const citationNumbers = React.useMemo(
    () => new Set(citations.map((c) => c.n)),
    [citations],
  );

  const cardRef = React.useRef<HTMLDivElement | null>(null);

  const showSource = React.useCallback((n: number) => {
    setTab('sources');
    // Let the tab panel mount before scrolling the target into view.
    window.requestAnimationFrame(() => {
      // Scoped to this card: a thread renders many answers, so several cards
      // can each carry a `citation-1`. A document-wide lookup would jump to
      // the first one on the page — usually an earlier, unrelated answer.
      cardRef.current
        ?.querySelector<HTMLElement>(`[id="citation-${n}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  }, []);

  const awaitingFirstToken = Boolean(streaming) && !envelope.answer;

  return (
    <Card ref={cardRef} className="animate-fade-in overflow-hidden">
      {/* Streaming progress hairline — a live edge on the card, not a spinner
          that competes with the text arriving underneath it. */}
      {streaming ? (
        <div className="h-0.5 w-full overflow-hidden bg-primary/10" aria-hidden>
          <div className="h-full w-1/3 animate-sweep bg-primary/60" />
        </div>
      ) : null}

      <CardContent className="p-5">
        {/* Question echo */}
        {question ? (
          <div className="mb-4 flex items-start gap-3">
            <span className="mt-px flex size-7 shrink-0 items-center justify-center rounded-full bg-secondary text-2xs font-semibold uppercase tracking-wide text-secondary-foreground">
              You
            </span>
            <p className="pt-1 text-sm font-medium leading-snug text-foreground">
              {question}
            </p>
          </div>
        ) : null}

        <div className="flex items-start gap-3">
          <span
            className={cn(
              'mt-px flex size-7 shrink-0 items-center justify-center rounded-full',
              abstained
                ? 'bg-primary/10 text-primary'
                : 'bg-primary text-primary-foreground',
            )}
          >
            <Sparkles className="size-3.5" aria-hidden />
          </span>

          <div className="min-w-0 flex-1">
            {/* Provenance strip */}
            {route || envelope.confidence ? (
              <div className="mb-3 flex flex-wrap items-center gap-2">
                {route ? (
                  <Badge variant={abstained ? 'default' : 'secondary'}>
                    {ROUTE_LABEL[route] ?? route}
                  </Badge>
                ) : null}
                {envelope.confidence ? (
                  <span
                    className={cn(
                      'rounded-full px-2 py-0.5 text-2xs font-medium capitalize',
                      CONFIDENCE_CLASS[envelope.confidence],
                    )}
                    title="How much the engine trusts this answer, given its grounding"
                  >
                    {envelope.confidence} confidence
                  </span>
                ) : null}
              </div>
            ) : null}

            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                <TabsTrigger value="answer">
                  <Sparkles className="size-3.5" /> Answer
                </TabsTrigger>
                {hasChart ? (
                  <TabsTrigger value="chart">
                    <BarChart3 className="size-3.5" /> Chart
                  </TabsTrigger>
                ) : null}
                {hasSql ? (
                  <TabsTrigger value="sql">
                    <Code2 className="size-3.5" /> SQL
                    {sqlStatements.length > 1 ? (
                      <Badge variant="muted" className="ml-1 px-1.5 py-0">
                        {sqlStatements.length}
                      </Badge>
                    ) : null}
                  </TabsTrigger>
                ) : null}
                {hasCitations ? (
                  <TabsTrigger value="sources">
                    <FileText className="size-3.5" /> Sources
                    <Badge variant="muted" className="ml-1 px-1.5 py-0">
                      {citations.length}
                    </Badge>
                  </TabsTrigger>
                ) : null}
              </TabsList>

              <TabsContent value="answer">
                {/*
                 * One stable live region for the whole answer. It exists before
                 * the first token so assistive tech announces the narrative as
                 * it grows, and `aria-busy` marks it as still in flight.
                 */}
                <div
                  aria-live="polite"
                  aria-atomic="false"
                  aria-busy={streaming ? true : undefined}
                  className="text-base leading-relaxed text-foreground"
                >
                  {awaitingFirstToken ? (
                    <ThinkingIndicator />
                  ) : abstained && !streaming ? (
                    // The narrative and the abstention reason are the same
                    // sentence on the wire. Once the stream closes the panel
                    // below carries it, so printing it twice would be noise.
                    <p className="text-muted-foreground">
                      No answer was produced for this question.
                    </p>
                  ) : (
                    <p className="whitespace-pre-wrap">
                      <AnswerText
                        text={envelope.answer}
                        citationNumbers={citationNumbers}
                        onCite={showSource}
                      />
                      {streaming ? (
                        <span
                          className="ml-0.5 inline-block h-4 w-[2px] -translate-y-px animate-caret-blink bg-primary align-middle"
                          aria-hidden
                        />
                      ) : null}
                    </p>
                  )}
                </div>

                {/* Self-correction: quiet by default, expandable on demand. */}
                {!streaming ? <SelfCorrectedNote attempts={attempts} /> : null}

                {/* Honest refusal — a first-class outcome, not an error. */}
                {abstained && !streaming ? (
                  <AbstainPanel
                    reason={envelope.abstain_reason}
                    suggestions={envelope.suggestions}
                    onAsk={onAsk}
                  />
                ) : null}

                {/* Valid query, zero rows. */}
                {noData ? <NoDataPanel /> : null}

                {envelope.clarifying_question ? (
                  <div className="mt-4 flex gap-2.5 rounded-lg border border-primary/25 bg-primary/[0.04] p-3 text-sm">
                    <HelpCircle
                      className="mt-0.5 size-4 shrink-0 text-primary"
                      aria-hidden
                    />
                    <p className="text-foreground">
                      I can help with that — {envelope.clarifying_question.toLowerCase()}
                    </p>
                  </div>
                ) : null}

                {error ? (
                  <div
                    role="alert"
                    className="mt-4 flex gap-2.5 rounded-lg border border-destructive/30 bg-destructive/[0.06] p-3 text-sm"
                  >
                    <TriangleAlert
                      className="mt-0.5 size-4 shrink-0 text-destructive"
                      aria-hidden
                    />
                    <div className="min-w-0">
                      <p className="font-medium text-destructive">
                        {error.code === 'guardrail_rejected'
                          ? 'Blocked by a guardrail'
                          : 'This answer stopped early'}
                      </p>
                      <p className="mt-0.5 text-muted-foreground">{error.message}</p>
                    </div>
                  </div>
                ) : null}

                {shownTables.length ? (
                  <div className="mt-4 space-y-4">
                    {shownTables.map((table, i) => (
                      <DataTable key={`${table.name}-${i}`} block={table} />
                    ))}
                  </div>
                ) : null}

                {!streaming ? <CaveatNote caveats={caveats} /> : null}

                {/* Feedback */}
                {!streaming && onFeedback ? (
                  <div className="mt-5 flex items-center gap-2 border-t pt-3">
                    <span className="text-xs text-muted-foreground">
                      Was this helpful?
                    </span>
                    <button
                      type="button"
                      onClick={() => onFeedback('up')}
                      aria-label="Helpful"
                      aria-pressed={feedback === 'up'}
                      className={cn(
                        'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                        feedback === 'up' && 'bg-success/12 text-success',
                      )}
                    >
                      <ThumbsUp className="size-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onFeedback('down')}
                      aria-label="Not helpful"
                      aria-pressed={feedback === 'down'}
                      className={cn(
                        'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                        feedback === 'down' && 'bg-destructive/12 text-destructive',
                      )}
                    >
                      <ThumbsDown className="size-4" />
                    </button>
                  </div>
                ) : null}
              </TabsContent>

              {hasChart && chart ? (
                <TabsContent value="chart">
                  <ChartRenderer spec={chart} />
                </TabsContent>
              ) : null}

              {hasSql ? (
                <TabsContent value="sql">
                  <div className="space-y-3">
                    {sqlStatements.map((statement, i) => (
                      <SqlViewer
                        key={i}
                        sql={statement}
                        dialect={envelope.dialect ?? 'sql'}
                      />
                    ))}
                  </div>
                </TabsContent>
              ) : null}

              {hasCitations ? (
                <TabsContent value="sources">
                  <CitationList citations={citations} />
                </TabsContent>
              ) : null}
            </Tabs>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
