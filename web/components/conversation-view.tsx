'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { CornerDownLeft, Square, Paperclip, FileSpreadsheet, FileText } from 'lucide-react';
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
  const [attachments, setAttachments] = React.useState<File[]>([]);
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
    async (question: string, files: File[] = []) => {
      const trimmed = question.trim();
      if ((!trimmed && files.length === 0) || busy) return;
      const effectiveQuestion =
        trimmed ||
        (files.length > 0
          ? `Analyze and summarize ${files.map((f) => f.name).join(', ')}`
          : '');
      setInput('');
      setAttachments([]);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;

      setTurns((prev) => [
        ...prev,
        {
          id: `pending-${Date.now()}`,
          question: effectiveQuestion,
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
        await streamAsk(effectiveQuestion, {
          conversationId,
          signal: controller.signal,
          files,
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
    void ask(input, attachments);
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        {turns.length === 0 ? (
          <div className="bg-grid flex h-full items-center justify-center rounded-xl">
            <PromptChips onSelect={(p) => void ask(p)} />
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-6 lg:max-w-4xl xl:max-w-5xl">
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
        <form onSubmit={onSubmit} className="mx-auto w-full max-w-3xl lg:max-w-4xl xl:max-w-5xl">
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
                  void ask(input, attachments);
                }
              }}
              rows={1}
              disabled={busy}
              placeholder="Ask about revenue, inventory, customers…"
              className="max-h-40 min-h-[36px] flex-1 resize-none bg-transparent px-2 py-1.5 text-base leading-relaxed outline-none placeholder:text-muted-foreground disabled:opacity-60"
            />
            <div className="flex items-center gap-2">
              <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border bg-background px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
                <Paperclip className="size-3.5" />
                <span>Attach</span>
                <input
                  type="file"
                  multiple
                  accept=".csv,.xlsx,.xls,.pdf,.txt,.md,.json,.docx,image/*"
                  className="hidden"
                  onChange={(e) => setAttachments(Array.from(e.target.files ?? []))}
                />
              </label>
              {attachments.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  {attachments.map((file) => {
                    const isSpreadsheet = file.name.match(/\.(xlsx|xls|csv)$/i);
                    const isPdf = file.name.match(/\.pdf$/i);
                    return (
                      <span
                        key={`${file.name}-${file.size}`}
                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${
                          isSpreadsheet
                            ? 'border-emerald-500/30 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300'
                            : isPdf
                              ? 'border-rose-500/30 bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300'
                              : 'border-blue-500/30 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300'
                        }`}
                      >
                        {isSpreadsheet ? (
                          <FileSpreadsheet className="size-3" />
                        ) : (
                          <FileText className="size-3" />
                        )}
                        <span className="max-w-[120px] truncate">{file.name}</span>
                      </span>
                    );
                  })}
                  <button
                    type="button"
                    className="text-xs text-destructive hover:underline"
                    onClick={() => setAttachments([])}
                  >
                    Clear
                  </button>
                </div>
              )}
            </div>
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
                disabled={!input.trim() && attachments.length === 0}
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
