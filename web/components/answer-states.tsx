'use client';

import * as React from 'react';
import {
  ChevronRight,
  CircleSlash,
  CornerDownRight,
  HelpCircle,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useMetricsCatalog } from '@/lib/hooks';
import { cn } from '@/lib/utils';
import { suggestionMetricKey, type CorrectionAttempt } from '@/lib/types';

/* -------------------------------------------------------------------------- */
/* Abstention                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * The engine understood the question and *refused to answer it*.
 *
 * This must not read as an error, and the difference is built in rather than
 * asserted. An error in this app carries `role="alert"`, the destructive hue, a
 * warning triangle, and a way to retry. An abstention carries none of those: no
 * alert role (nothing broke), the primary hue (information, not damage), a
 * shield (a guarantee was enforced), and *forward* actions instead of a retry —
 * retrying an ungroundable question would only fail again, so the panel offers
 * the governed metrics that would succeed.
 *
 * Visually it is a left rule rather than a tinted panel, so it sits inside the
 * answer as a qualification of it, not as a replacement banner.
 */
export function AbstainPanel({
  reason,
  suggestions,
  onAsk,
}: {
  reason?: string | null;
  suggestions?: string[];
  onAsk?: (question: string) => void;
}) {
  const items = suggestions ?? [];
  // A thread can contain several abstentions, so the heading id must be unique
  // per instance — a fixed id would break `aria-labelledby` on all but the first.
  const headingId = React.useId();
  // The suggestions name metrics by key (`avg_order_value`); the catalog holds
  // the human label ("Average order value"). Using the label matters twice
  // over: the chip reads as English, and the follow-up question is phrased the
  // way the router recognises the metric — a raw key would not resolve.
  const catalog = useMetricsCatalog();
  const labelFor = React.useCallback(
    (key: string): string =>
      catalog.data?.metrics.find((m) => m.key === key)?.label ??
      key.replace(/_/g, ' '),
    [catalog.data],
  );

  return (
    <section
      aria-labelledby={headingId}
      className="mt-4 border-l-2 border-primary pl-4"
    >
      <h4
        id={headingId}
        className="flex items-center gap-2 text-sm font-semibold text-foreground"
      >
        <ShieldCheck className="size-4 shrink-0 text-primary" aria-hidden />
        Withheld on purpose
      </h4>
      <p className="mt-1.5 max-w-[80ch] text-sm leading-relaxed text-foreground">
        {reason?.trim() ||
          'This question could not be grounded in a governed metric or a retrieved document.'}
      </p>
      <p className="mt-1.5 max-w-[80ch] text-xs leading-relaxed text-muted-foreground">
        Nothing failed. No figure was produced because none could be traced back
        to the warehouse — and an untraceable number is not reported here.
      </p>

      {items.length > 0 ? (
        <div className="mt-3">
          <p className="text-2xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Ask instead
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {items.map((suggestion, i) => {
              const key = suggestionMetricKey(suggestion);
              const label = key ? labelFor(key) : suggestion;
              // Without a quoted metric key the suggestion is already a whole
              // instruction — send it verbatim rather than inventing phrasing.
              const question = key
                ? `What was ${label.toLowerCase()} last quarter?`
                : suggestion;
              return (
                <li key={`${suggestion}-${i}`}>
                  <button
                    type="button"
                    onClick={() => onAsk?.(question)}
                    disabled={!onAsk}
                    title={suggestion}
                    className={cn(
                      'inline-flex min-h-8 items-center gap-1.5 rounded-full border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      onAsk
                        ? 'hover:bg-primary hover:text-primary-foreground active:bg-primary/90'
                        : 'cursor-default opacity-80',
                    )}
                  >
                    <CornerDownRight className="size-3.5" aria-hidden />
                    <span>{label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Clarification                                                              */
/* -------------------------------------------------------------------------- */

/**
 * The router needs one more thing before it can answer (docs/05 §9).
 *
 * Like an abstention this is a *deliberate* outcome — "a confident wrong answer
 * is worse than a question" — so it shares the abstention's shape: a left rule
 * in the primary hue, no alert role, and a forward action. The difference is
 * that the way forward is the engine's own question, echoed as a one-click
 * follow-up when the composer can take it.
 */
export function ClarifyPanel({
  question,
  onAsk,
}: {
  question: string;
  onAsk?: (question: string) => void;
}) {
  const headingId = React.useId();
  const text = question.trim();
  if (!text) return null;

  return (
    <section
      aria-labelledby={headingId}
      className="mt-4 border-l-2 border-primary pl-4"
    >
      <h4
        id={headingId}
        className="flex items-center gap-2 text-sm font-semibold text-foreground"
      >
        <HelpCircle className="size-4 shrink-0 text-primary" aria-hidden />
        One thing first
      </h4>
      <p className="mt-1.5 max-w-[80ch] text-sm leading-relaxed text-foreground">
        {text}
      </p>
      <p className="mt-1.5 text-xs text-muted-foreground">
        The engine asks rather than guessing a metric or a time range.
      </p>
      {onAsk ? (
        <button
          type="button"
          onClick={() => onAsk(text)}
          className="mt-3 inline-flex min-h-8 items-center gap-1.5 rounded-full border border-primary/40 px-3 py-1.5 text-xs font-medium text-primary transition-colors duration-150 ease-out hover:bg-primary hover:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <CornerDownRight className="size-3.5" aria-hidden />
          Answer this question
        </button>
      ) : null}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* No data                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * A valid, governed query that matched no rows.
 *
 * The narrative above already names the metric and the period, so this is a
 * compact classification strip rather than a second copy of the same sentence.
 * Its whole job is to say which of three very different things happened: not a
 * failure, not a measured zero, but an absence — and to point at the SQL tab,
 * where the reader can verify that absence for themselves.
 */
export function NoDataPanel({ message }: { message?: string }) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-dashed px-3 py-2.5 text-xs">
      <span className="inline-flex items-center gap-1.5 font-medium text-foreground">
        <CircleSlash className="size-3.5 text-muted-foreground" aria-hidden />
        No rows
      </span>
      <span className="text-muted-foreground">
        {message?.trim() ||
          'The query ran and matched nothing. That is an absence, not a failure — and not a measured zero.'}
      </span>
      <span className="ml-auto text-muted-foreground">
        The query that ran is on the SQL tab
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Self-correction                                                            */
/* -------------------------------------------------------------------------- */

/**
 * The engine retried a governed selection and recovered.
 *
 * This is a differentiator worth surfacing, but it is *metadata about how the
 * answer was produced* — it must never compete with the answer itself. So it
 * collapses to a single quiet chip, and only expands on request.
 *
 * Built on native `<details>` rather than a button plus state: the browser then
 * reveals the attempts for "find in page", which matters because the thing a
 * reviewer searches for ("gave up", a metric name) is often the text inside.
 */
export function SelfCorrectedNote({
  attempts,
}: {
  attempts: CorrectionAttempt[];
}) {
  if (!attempts.length) return null;

  const recovered = attempts.filter((a) => a.resolution === 'corrected').length;
  const label =
    recovered > 0
      ? `Self-corrected ${recovered === 1 ? 'once' : `${recovered}×`}`
      : 'Correction attempted';

  return (
    <details className="group mt-3">
      <summary
        className={cn(
          'inline-flex min-h-7 cursor-pointer list-none items-center gap-1.5 rounded-full border px-2.5 py-1 text-2xs font-medium transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden',
          recovered > 0
            ? 'border-success/30 text-success hover:bg-success/10'
            : 'border-warning/30 text-warning hover:bg-warning/10',
        )}
      >
        <Wrench className="size-3" aria-hidden />
        {label}
        <ChevronRight
          className="size-3 transition-transform duration-150 ease-out group-open:rotate-90"
          aria-hidden
        />
      </summary>

      <ol className="mt-2 space-y-2">
        {attempts.map((attempt, i) => (
          <li
            key={`${attempt.stage}-${attempt.attempt}-${i}`}
            className="rounded-md border bg-elevated p-3 text-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-2xs text-muted-foreground">
                attempt {attempt.attempt}
              </span>
              <Badge variant="muted" className="font-mono text-2xs">
                {attempt.stage}
              </Badge>
              <Badge
                variant={attempt.resolution === 'corrected' ? 'success' : 'warning'}
                className="ml-auto"
              >
                {attempt.resolution === 'corrected' ? 'recovered' : 'gave up'}
              </Badge>
            </div>
            <p className="mt-2 text-muted-foreground">{attempt.error}</p>
          </li>
        ))}
        <li className="text-2xs text-muted-foreground">
          Retries re-select from the governed semantic layer — never free-form
          SQL — so a correction can never widen the data boundary.
        </li>
      </ol>
    </details>
  );
}
