'use client';

import * as React from 'react';
import {
  ChevronDown,
  CircleSlash,
  CornerDownRight,
  DatabaseZap,
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
 * Design intent: this must not look like an error. An error says the product
 * broke; an abstention says the product is trustworthy enough to decline. So it
 * uses the primary (informational) hue rather than destructive red, leads with
 * a shield, and gives the reader somewhere to go — the governed metrics the
 * engine suggested, as one-click follow-up questions.
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
      className="mt-4 overflow-hidden rounded-lg border border-primary/25 bg-primary/[0.04]"
    >
      <div className="flex gap-3 p-4">
        <span
          className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
          aria-hidden
        >
          <ShieldCheck className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h4 id={headingId} className="text-sm font-semibold text-foreground">
            Answer withheld on purpose
          </h4>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            {reason?.trim() ||
              'This question could not be grounded in a governed metric or a retrieved document.'}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            No figure was produced. InsightGPT declines rather than estimate a
            number it cannot trace back to the warehouse.
          </p>
        </div>
      </div>

      {items.length > 0 ? (
        <div className="border-t border-primary/20 bg-primary/[0.03] px-4 py-3">
          <p className="text-xs font-medium text-foreground">
            Governed metrics you can ask about instead
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
                      'group inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-card px-3 py-1.5 text-xs font-medium text-primary transition-colors',
                      onAsk
                        ? 'hover:bg-primary hover:text-primary-foreground'
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
    <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-dashed bg-muted/40 px-3 py-2.5 text-xs">
      <span className="inline-flex items-center gap-1.5 font-medium text-foreground">
        <CircleSlash className="size-3.5 text-muted-foreground" aria-hidden />
        Empty result
      </span>
      <span className="text-muted-foreground">
        {message?.trim() ||
          'A genuine absence of data — not an error, and not a measured zero.'}
      </span>
      <span className="ml-auto inline-flex items-center gap-1.5 text-muted-foreground">
        <DatabaseZap className="size-3.5" aria-hidden />
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
 */
export function SelfCorrectedNote({
  attempts,
}: {
  attempts: CorrectionAttempt[];
}) {
  const [open, setOpen] = React.useState(false);
  const id = React.useId();
  if (!attempts.length) return null;

  const recovered = attempts.filter((a) => a.resolution === 'corrected').length;
  const label =
    recovered > 0
      ? `Self-corrected ${recovered === 1 ? 'once' : `${recovered}×`}`
      : 'Correction attempted';

  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={id}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-2xs font-medium transition-colors',
          recovered > 0
            ? 'border-success/30 bg-success/10 text-success hover:bg-success/15'
            : 'border-warning/30 bg-warning/10 text-warning hover:bg-warning/15',
        )}
      >
        <Wrench className="size-3" aria-hidden />
        {label}
        <ChevronDown
          className={cn('size-3 transition-transform', open && 'rotate-180')}
          aria-hidden
        />
      </button>

      {open ? (
        <ol id={id} className="mt-2 space-y-2">
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
      ) : null}
    </div>
  );
}
