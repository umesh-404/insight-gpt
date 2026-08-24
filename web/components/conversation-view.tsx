'use client';

import * as React from 'react';
import { CornerDownLeft, Loader2, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { InsightCard } from '@/components/insight-card';
import { PromptChips } from '@/components/prompt-chips';
import { streamAsk, api } from '@/lib/api';
import { useToast } from '@/components/ui/toast';
import type { AnswerEnvelope, AskStreamEvent, ConversationTurn } from '@/lib/types';

interface ThreadTurn {
  id: string;
  question: string;
  envelope: AnswerEnvelope;
  streaming: boolean;
  feedback: 'up' | 'down' | null;
}

function emptyEnvelope(): AnswerEnvelope {
  return {
    answer: '',
    sql: null,
    tables: [],
    citations: [],
    chart_spec: null,
    caveats: [],
  };
}

export function ConversationView({
  conversationId,
  initialTurns = [],
}: {
  conversationId?: string;
  initialTurns?: ConversationTurn[];
}) {
  const { toast } = useToast();
  const [turns, setTurns] = React.useState<ThreadTurn[]>(() =>
    initialTurns.map((t) => ({
      id: t.id,
      question: t.question,
      envelope: t.envelope,
      streaming: false,
      feedback: t.feedback ?? null,
    })),
  );
  const [input, setInput] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const abortRef = React.useRef<AbortController | null>(null);
  const bottomRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  const patchLast = React.useCallback(
    (updater: (turn: ThreadTurn) => ThreadTurn) => {
      setTurns((prev) => {
        if (!prev.length) return prev;
        const next = [...prev];
        next[next.length - 1] = updater(next[next.length - 1]!);
        return next;
      });
    },
    [],
  );

  const applyEvent = React.useCallback(
    (event: AskStreamEvent) => {
      switch (event.type) {
        case 'token':
          patchLast((t) => ({
            ...t,
            envelope: { ...t.envelope, answer: t.envelope.answer + event.data.text },
          }));
          break;
        case 'route':
          patchLast((t) => ({
            ...t,
            envelope: {
              ...t.envelope,
              route: event.data.route,
              confidence: event.data.confidence,
            },
          }));
          break;
        case 'sql':
          patchLast((t) => ({
            ...t,
            envelope: { ...t.envelope, sql: event.data.sql, dialect: event.data.dialect },
          }));
          break;
        case 'tables':
          patchLast((t) => ({
            ...t,
            envelope: { ...t.envelope, tables: [...t.envelope.tables, event.data] },
          }));
          break;
        case 'citations':
          patchLast((t) => ({
            ...t,
            envelope: { ...t.envelope, citations: event.data.items },
          }));
          break;
        case 'chart':
          patchLast((t) => ({
            ...t,
            envelope: { ...t.envelope, chart_spec: event.data.chart_spec },
          }));
          break;
        case 'caveats':
          patchLast((t) => ({
            ...t,
            envelope: { ...t.envelope, caveats: event.data.items },
          }));
          break;
        case 'meta':
          patchLast((t) => ({ ...t, id: event.data.message_id }));
          break;
        case 'done':
          patchLast((t) => ({ ...t, id: event.data.message_id, streaming: false }));
          break;
        case 'error':
          patchLast((t) => ({ ...t, streaming: false }));
          toast({
            title: 'Could not complete the answer',
            description: event.data.message,
            variant: 'destructive',
          });
          break;
        default:
          break;
      }
    },
    [patchLast, toast],
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
        },
      ]);

      try {
        await streamAsk(trimmed, {
          conversationId,
          signal: controller.signal,
          onEvent: applyEvent,
        });
      } catch (err) {
        patchLast((t) => ({ ...t, streaming: false }));
        toast({
          title: 'Request failed',
          description: err instanceof Error ? err.message : 'Unknown error',
          variant: 'destructive',
        });
      } finally {
        patchLast((t) => ({ ...t, streaming: false }));
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, conversationId, applyEvent, patchLast, toast],
  );

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
          <div className="flex h-full items-center justify-center">
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
                messageId={turn.id}
                feedback={turn.feedback}
                onFeedback={(rating) => onFeedback(turn.id, rating)}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="border-t bg-background/80 px-4 py-3 backdrop-blur sm:px-6">
        <form onSubmit={onSubmit} className="mx-auto max-w-3xl">
          <div className="flex items-end gap-2 rounded-lg border bg-card p-2 shadow-soft focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background">
            <label htmlFor="ask-input" className="sr-only">
              Ask a question
            </label>
            <textarea
              id="ask-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                }
              }}
              rows={1}
              placeholder="Ask about revenue, inventory, customers…"
              className="max-h-40 min-h-[36px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
            />
            {busy ? (
              <Button type="button" variant="secondary" size="icon" onClick={stop} aria-label="Stop">
                <Square className="size-4" />
              </Button>
            ) : (
              <Button type="submit" size="icon" disabled={!input.trim()} aria-label="Send">
                {busy ? <Loader2 className="size-4 animate-spin" /> : <CornerDownLeft className="size-4" />}
              </Button>
            )}
          </div>
          <p className="mt-1.5 px-1 text-xs text-muted-foreground">
            Press <kbd className="rounded border bg-muted px-1">Enter</kbd> to send ·{' '}
            <kbd className="rounded border bg-muted px-1">Shift</kbd>+
            <kbd className="rounded border bg-muted px-1">Enter</kbd> for a new line
          </p>
        </form>
      </div>
    </div>
  );
}
