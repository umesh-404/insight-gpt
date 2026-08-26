'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { CornerDownLeft, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { InsightCard } from '@/components/insight-card';
import { PromptChips } from '@/components/prompt-chips';
import { EnvelopeAccumulator, streamAsk, api } from '@/lib/api';
import { useToast } from '@/components/ui/toast';
import { qk } from '@/lib/hooks';
import { useQueryClient } from '@tanstack/react-query';
import {
  emptyEnvelope,
  type AnswerEnvelope,
  type ApiErrorBody,
  type ConversationTurn,
} from '@/lib/types';

interface ThreadTurn {
  id: string;
  question: string;
  envelope: AnswerEnvelope;
  streaming: boolean;
  feedback: 'up' | 'down' | null;
  /** Set when the stream failed part-way; rendered inside the card. */
  error?: ApiErrorBody | null;
}

export function ConversationView({
  conversationId,
  initialTurns = [],
  initialQuestion,
}: {
  conversationId?: string;
  initialTurns?: ConversationTurn[];
  /** Seeded from `/ask?q=…`; asked once as soon as the view mounts. */
  initialQuestion?: string;
}) {
  const { toast } = useToast();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [turns, setTurns] = React.useState<ThreadTurn[]>(() =>
    initialTurns.map((t) => ({
      id: t.id,
      question: t.question,
      envelope: t.envelope,
      streaming: false,
      feedback: t.feedback ?? null,
      error: null,
    })),
  );
  const [input, setInput] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);
  const bottomRef = React.useRef<HTMLDivElement | null>(null);
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  // Grow the composer with its content up to the CSS max-height, then scroll.
  // Driven off `input` so a programmatic set (a suggestion chip) resizes too.
  React.useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  // Abort any in-flight stream when the view unmounts (route change, sign-out).
  React.useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const patchLast = React.useCallback(
    (updater: (turn: ThreadTurn) => ThreadTurn) => {
      setTurns((prev) => {
        if (!prev.length) return prev;
        const next = [...prev];
        const last = next[next.length - 1];
        if (!last) return prev;
        next[next.length - 1] = updater(last);
        return next;
      });
    },
    [],
  );

  const ask = React.useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || busy) return;
      setInput('');
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;

      setTurns((prev) => [
        ...prev,
        {
          id: `pending-${Date.now()}`,
          question: trimmed,
          envelope: emptyEnvelope(),
          streaming: true,
          feedback: null,
          error: null,
        },
      ]);

      // The accumulator owns envelope reconstruction: it appends every table,
      // replaces the SQL block, and re-resolves the chart's `data_ref` as more
      // tables arrive, so the streamed result matches the non-stream envelope.
      const acc = new EnvelopeAccumulator();

      try {
        await streamAsk(trimmed, {
          conversationId,
          signal: controller.signal,
          onEvent: (event) => {
            const envelope = acc.apply(event);
            patchLast((t) => ({
              ...t,
              envelope,
              id: acc.messageId ?? t.id,
              // An `error` frame ends the turn — never leave the caret spinning.
              streaming: !acc.done,
              error: acc.error,
            }));
            if (event.type === 'error') {
              toast({
                title: 'Could not complete the answer',
                description: event.data.message,
                variant: 'destructive',
              });
            }
          },
        });
      } catch (err) {
        if (!controller.signal.aborted) {
          const message = err instanceof Error ? err.message : 'Unknown error';
          patchLast((t) => ({
            ...t,
            streaming: false,
            error: { code: 'request_failed', message },
          }));
          toast({
            title: 'Request failed',
            description: message,
            variant: 'destructive',
          });
        }
      } finally {
        patchLast((t) => ({ ...t, streaming: false }));
        setBusy(false);
        abortRef.current = null;
        // Return the caret to the composer so a follow-up needs no mouse.
        inputRef.current?.focus();
        // History gained a message (and possibly a brand-new conversation).
        void queryClient.invalidateQueries({ queryKey: qk.conversations });
        if (!conversationId && acc.conversationId && !controller.signal.aborted) {
          router.replace(`/ask/${acc.conversationId}`);
        }
      }
    },
    [busy, conversationId, patchLast, queryClient, router, toast],
  );

  // Fire a seeded question exactly once per mount. The ref guard matters
  // because `ask` is recreated whenever `busy` flips, which would otherwise
  // re-trigger this effect mid-stream and ask the same question twice.
  const seededRef = React.useRef(false);
  React.useEffect(() => {
    if (seededRef.current || !initialQuestion) return;
    seededRef.current = true;
    void ask(initialQuestion);
  }, [initialQuestion, ask]);

  const stop = React.useCallback(() => {
    abortRef.current?.abort();
    patchLast((t) => ({ ...t, streaming: false }));
    setBusy(false);
  }, [patchLast]);

  const onFeedback = React.useCallback(
    (turnId: string, rating: 'up' | 'down') => {
      setTurns((prev) =>
        prev.map((t) => (t.id === turnId ? { ...t, feedback: rating } : t)),
      );
      // Feedback is best-effort; a missing endpoint must not surface an error.
      void api.sendFeedback(turnId, rating).catch(() => undefined);
      toast({ title: 'Thanks for the feedback', variant: 'success' });
    },
    [toast],
  );

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void ask(input);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        {turns.length === 0 ? (
          <div className="bg-grid flex h-full items-center justify-center rounded-xl">
            <PromptChips onSelect={(p) => void ask(p)} />
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-6">
            {turns.map((turn) => (
              <InsightCard
                key={turn.id}
                question={turn.question}
                envelope={turn.envelope}
                streaming={turn.streaming}
                error={turn.error}
                messageId={turn.id}
                feedback={turn.feedback}
                onFeedback={(rating) => onFeedback(turn.id, rating)}
                // Suggested governed metrics on an abstention re-ask in place.
                onAsk={(q) => void ask(q)}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="border-t bg-background/85 px-4 py-3 backdrop-blur-md sm:px-6">
        <form onSubmit={onSubmit} className="mx-auto max-w-3xl">
          <div className="flex items-end gap-2 rounded-xl border bg-card p-2 shadow-soft transition-shadow focus-within:border-primary/40 focus-within:shadow-raised focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background">
            <label htmlFor="ask-input" className="sr-only">
              Ask a question about your business data
            </label>
            <textarea
              id="ask-input"
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                }
              }}
              rows={1}
              disabled={busy}
              placeholder="Ask about revenue, inventory, customers…"
              className="max-h-40 min-h-[36px] flex-1 resize-none bg-transparent px-2 py-1.5 text-base leading-relaxed outline-none placeholder:text-muted-foreground disabled:opacity-60"
            />
            {busy ? (
              <Button
                type="button"
                variant="secondary"
                size="icon"
                onClick={stop}
                aria-label="Stop generating"
                title="Stop generating"
              >
                <Square className="size-4" />
              </Button>
            ) : (
              <Button
                type="submit"
                size="icon"
                disabled={!input.trim()}
                aria-label="Send question"
              >
                <CornerDownLeft className="size-4" />
              </Button>
            )}
          </div>
          <p className="mt-1.5 px-1 text-xs text-muted-foreground">
            {busy ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="size-1.5 animate-pulse rounded-full bg-primary" />
                Answering — press Stop to cancel
              </span>
            ) : (
              <>
                <kbd className="rounded border bg-muted px-1 font-sans">Enter</kbd> to
                send · <kbd className="rounded border bg-muted px-1 font-sans">Shift</kbd>
                +<kbd className="rounded border bg-muted px-1 font-sans">Enter</kbd> for
                a new line
              </>
            )}
          </p>
        </form>
      </div>
    </div>
  );
}
