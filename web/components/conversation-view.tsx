'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { CornerDownLeft, Paperclip, Square, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { InsightCard } from '@/components/insight-card';
import { PromptChips } from '@/components/prompt-chips';
import { EnvelopeAccumulator, streamAsk, api } from '@/lib/api';
import { useToast } from '@/components/ui/toast';
import { qk } from '@/lib/hooks';
import { useQueryClient } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import {
  emptyEnvelope,
  type AnswerEnvelope,
  type ApiErrorBody,
  type AttachmentInput,
  type ConversationTurn,
} from '@/lib/types';

interface ThreadTurn {
  id: string;
  question: string;
  attachments?: { name: string; size?: number }[];
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
      attachments: t.attachments,
      envelope: t.envelope,
      streaming: false,
      feedback: t.feedback ?? null,
      error: null,
    })),
  );
  const [input, setInput] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [attachedFiles, setAttachedFiles] = React.useState<File[]>([]);
  const [isDragging, setIsDragging] = React.useState(false);

  const abortRef = React.useRef<AbortController | null>(null);
  const bottomRef = React.useRef<HTMLDivElement | null>(null);
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  // Grow the composer with its content up to the CSS max-height, then scroll.
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

  const readFilePayload = (file: File): Promise<AttachmentInput> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      const isText = /\.(csv|tsv|txt|md|markdown|json|log|sql|py|js|ts|yaml|yml|html|xml|env)$/i.test(
        file.name,
      );
      if (isText) {
        reader.onload = () =>
          resolve({
            name: file.name,
            content: typeof reader.result === 'string' ? reader.result : '',
            content_type: file.type || 'text/plain',
            size: file.size,
          });
        reader.onerror = () => reject(reader.error);
        reader.readAsText(file);
      } else {
        // Binary (PDF, DOCX, XLSX, etc.): encode as base64 DataURL
        reader.onload = () =>
          resolve({
            name: file.name,
            content: typeof reader.result === 'string' ? reader.result : '',
            content_type: file.type || 'application/octet-stream',
            size: file.size,
          });
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      }
    });
  };

  const ask = React.useCallback(
    async (question: string, filesToAttach: File[] = attachedFiles) => {
      const trimmed = question.trim();
      if ((!trimmed && filesToAttach.length === 0) || busy) return;
      const effectiveQuestion =
        trimmed || 'Analyze the attached file(s) and provide the key insights.';
      setInput('');
      setAttachedFiles([]);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const turnAttachments = filesToAttach.map((f) => ({ name: f.name, size: f.size }));

      setTurns((prev) => [
        ...prev,
        {
          id: `pending-${Date.now()}`,
          question: effectiveQuestion,
          attachments: turnAttachments,
          envelope: emptyEnvelope(),
          streaming: true,
          feedback: null,
          error: null,
        },
      ]);

      const acc = new EnvelopeAccumulator();

      try {
        const payloadAttachments: AttachmentInput[] = await Promise.all(
          filesToAttach.map(readFilePayload),
        );

        await streamAsk(effectiveQuestion, {
          conversationId,
          attachments: payloadAttachments,
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
        inputRef.current?.focus();
        void queryClient.invalidateQueries({ queryKey: qk.conversations });
        if (!conversationId && acc.conversationId && !controller.signal.aborted) {
          router.replace(`/ask/${acc.conversationId}`);
        }
      }
    },
    [attachedFiles, busy, conversationId, patchLast, queryClient, router, toast],
  );

  // Fire a seeded question exactly once per mount.
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
      void api.sendFeedback(turnId, rating).catch(() => undefined);
      toast({ title: 'Thanks for the feedback', variant: 'success' });
    },
    [toast],
  );

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void ask(input);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selected = Array.from(e.target.files);
      setAttachedFiles((prev) => [...prev, ...selected]);
    }
    e.target.value = '';
  };

  const removeAttachment = (index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
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
                attachments={turn.attachments}
                envelope={turn.envelope}
                streaming={turn.streaming}
                error={turn.error}
                messageId={turn.id}
                feedback={turn.feedback}
                onFeedback={(rating) => onFeedback(turn.id, rating)}
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
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            accept=".csv,.tsv,.txt,.md,.markdown,.json,.pdf,.docx,.doc,.log,.sql,.py,.yaml,.yml,.xlsx,.xls"
            onChange={handleFileChange}
          />
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragging(false);
              if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                setAttachedFiles((prev) => [...prev, ...Array.from(e.dataTransfer.files)]);
              }
            }}
            className={cn(
              'flex flex-col gap-2 rounded-xl border bg-card p-2 shadow-soft transition-all focus-within:border-primary/40 focus-within:shadow-raised focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background',
              isDragging && 'border-primary bg-primary/5 ring-2 ring-primary/30',
            )}
          >
            {/* Attached file chips */}
            {attachedFiles.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5 px-1 pt-1">
                {attachedFiles.map((file, idx) => (
                  <span
                    key={`${file.name}-${idx}`}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border/80 bg-muted/70 px-2 py-1 text-xs text-foreground transition-colors hover:bg-muted"
                  >
                    <Paperclip className="size-3 text-primary" />
                    <span className="max-w-[200px] truncate font-medium">{file.name}</span>
                    <span className="text-2xs text-muted-foreground">
                      ({file.size > 1024 * 1024
                        ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
                        : `${Math.round(file.size / 1024)} KB`})
                    </span>
                    <button
                      type="button"
                      onClick={() => removeAttachment(idx)}
                      disabled={busy}
                      aria-label={`Remove ${file.name}`}
                      className="ml-0.5 rounded p-0.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}

            <div className="flex items-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => fileInputRef.current?.click()}
                disabled={busy}
                title="Attach file (CSV, Excel, PDF, Word, JSON, TXT)"
                aria-label="Attach file"
                className="size-9 shrink-0 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Paperclip className="size-4" />
              </Button>

              <label htmlFor="ask-input" className="sr-only">
                Ask a question about your business data or attached files
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
                placeholder={
                  attachedFiles.length > 0
                    ? `Ask anything about ${attachedFiles.map((f) => f.name).join(', ')}…`
                    : 'Ask about revenue, inventory, customers, or attach a file…'
                }
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
                  disabled={!input.trim() && attachedFiles.length === 0}
                  aria-label="Send question"
                >
                  <CornerDownLeft className="size-4" />
                </Button>
              )}
            </div>
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
                a new line · <Paperclip className="inline size-3" /> Attach files or drag & drop (CSV, PDF, Excel, JSON, TXT)
              </>
            )}
          </p>
        </form>
      </div>
    </div>
  );
}
