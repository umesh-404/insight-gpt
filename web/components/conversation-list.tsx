'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Plus, Pencil, Search, Trash2, Check, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { RelativeTime } from '@/components/relative-time';
import {
  useConversations,
  useDeleteConversation,
  useRenameConversation,
} from '@/lib/hooks';
import { ApiError, type ConversationSummary } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Matches the backend's `TITLE_INPUT_MAX_CHARS`; keeps the 422 unreachable. */
const TITLE_MAX_CHARS = 120;

/** Turn any failure into one short sentence the sidebar can show. */
function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return 'That conversation no longer exists.';
    return error.message || fallback;
  }
  return fallback;
}

/** A filter only earns its space once scanning the list stops being trivial. */
const FILTER_THRESHOLD = 8;

export function ConversationList() {
  const pathname = usePathname();
  const { data, isLoading, isError, refetch } = useConversations();
  const items = data?.items ?? [];
  const [filter, setFilter] = React.useState('');
  // Two instances of this list are mounted at once — the md+ column and the
  // small-screen disclosure — so every id has to be instance-scoped or the
  // labels bind to whichever copy the browser happened to see first.
  const filterId = `conversation-filter-${React.useId()}`;

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? items.filter((c) => (c.title || '').toLowerCase().includes(needle))
    : items;

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="space-y-2 border-b p-3">
        <Button asChild variant="outline" className="w-full justify-start gap-2">
          <Link href="/ask">
            <Plus className="size-4" aria-hidden /> New question
          </Link>
        </Button>
        {items.length >= FILTER_THRESHOLD ? (
          <div className="relative">
            <label htmlFor={filterId} className="sr-only">
              Filter conversations by title
            </label>
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              id={filterId}
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter"
              className="h-9 w-full rounded-md border border-input bg-background pl-8 pr-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>
        ) : null}
      </div>
      <div className="scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto pb-3">
        <h2 className="flex items-baseline gap-1.5 px-3 pb-1 pt-3 text-2xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
          History
          {!isLoading && !isError && items.length > 0 ? (
            <span className="tabular-nums normal-case tracking-normal">
              {needle ? `${shown.length} of ${items.length}` : items.length}
            </span>
          ) : null}
        </h2>
        {isLoading ? (
          <div className="space-y-1 px-3 pt-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-11 w-full" />
            ))}
          </div>
        ) : isError ? (
          // Loading history is a plain GET: a transient failure is genuinely
          // retryable, so offer the retry rather than a dead sentence.
          <div className="px-3 py-3">
            <p className="text-xs text-muted-foreground">
              History could not be loaded.
            </p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="mt-1.5 text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Try again
            </button>
          </div>
        ) : items.length === 0 ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            No questions yet. Ask one and it is saved here.
          </p>
        ) : shown.length === 0 ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            No conversation title matches “{filter.trim()}”.
          </p>
        ) : (
          <ul className="divide-y">
            {shown.map((conversation) => (
              <ConversationRow
                key={conversation.id}
                conversation={conversation}
                active={pathname === `/ask/${conversation.id}`}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* One row: navigate, rename inline, or delete behind a confirm step          */
/* -------------------------------------------------------------------------- */

type RowMode = 'idle' | 'renaming' | 'confirming-delete';

function ConversationRow({
  conversation,
  active,
}: {
  conversation: ConversationSummary;
  active: boolean;
}) {
  const router = useRouter();
  const { toast } = useToast();
  const rename = useRenameConversation();
  const remove = useDeleteConversation();

  const renameId = `rename-${React.useId()}`;
  const [mode, setMode] = React.useState<RowMode>('idle');
  const [draft, setDraft] = React.useState(conversation.title);
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const title = conversation.title || 'Untitled conversation';
  const busy = rename.isPending || remove.isPending;

  const startRename = React.useCallback(() => {
    setDraft(conversation.title);
    setError(null);
    setMode('renaming');
  }, [conversation.title]);

  React.useEffect(() => {
    if (mode !== 'renaming') return;
    // Select the whole title so typing replaces it, which is what a rename
    // usually is, while an arrow key still gets you to an edit.
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [mode]);

  const commitRename = React.useCallback(() => {
    const clean = draft.replace(/\s+/g, ' ').trim();
    if (!clean) {
      setError('Enter a title.');
      inputRef.current?.focus();
      return;
    }
    if (clean === conversation.title) {
      setMode('idle');
      setError(null);
      return;
    }
    setError(null);
    // Close on submit: the mutation is optimistic, so the new title is already
    // on screen and a rollback restores the old one if the server refuses.
    setMode('idle');
    rename.mutate(
      { id: conversation.id, title: clean },
      {
        onError: (err) => {
          const message = errorMessage(err, 'Could not rename this conversation.');
          setError(message);
          toast({ title: 'Rename failed', description: message, variant: 'destructive' });
          // Reopen the editor with what they typed so the work is not lost.
          setDraft(clean);
          setMode('renaming');
        },
      },
    );
  }, [conversation.id, conversation.title, draft, rename, toast]);

  const confirmDelete = React.useCallback(() => {
    const wasOpen = active;
    setError(null);
    remove.mutate(conversation.id, {
      onSuccess: () => {
        // Never leave the reader staring at a transcript that is gone.
        if (wasOpen) router.push('/ask');
      },
      onError: (err) => {
        const message = errorMessage(err, 'Could not delete this conversation.');
        setError(message);
        toast({ title: 'Delete failed', description: message, variant: 'destructive' });
        setMode('idle');
      },
    });
  }, [active, conversation.id, remove, router, toast]);

  if (mode === 'renaming') {
    return (
      <li>
        <form
          className="flex items-center gap-1 border-l-2 border-primary bg-accent/50 px-2 py-2"
          onSubmit={(event) => {
            event.preventDefault();
            commitRename();
          }}
        >
          <label className="sr-only" htmlFor={renameId}>
            Conversation title
          </label>
          <input
            id={renameId}
            ref={inputRef}
            value={draft}
            maxLength={TITLE_MAX_CHARS}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${renameId}-error` : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                setMode('idle');
                setError(null);
              }
            }}
            className="min-w-0 flex-1 rounded-sm border border-input bg-background px-2 py-1 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            aria-label="Save title"
            title="Save"
            className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Check className="size-3.5" aria-hidden />
          </button>
          <button
            type="button"
            aria-label="Cancel rename"
            title="Cancel"
            onClick={() => {
              setMode('idle');
              setError(null);
            }}
            className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-3.5" aria-hidden />
          </button>
        </form>
        {error ? (
          <p
            id={`${renameId}-error`}
            role="alert"
            className="px-2 pb-1 pt-0.5 text-xs text-destructive"
          >
            {error}
          </p>
        ) : null}
      </li>
    );
  }

  if (mode === 'confirming-delete') {
    return (
      <li>
        <div
          role="group"
          aria-label={`Delete ${title}?`}
          className="border-l-2 border-destructive bg-destructive/[0.06] px-3 py-2.5"
        >
          <p className="truncate text-sm text-foreground">Delete “{title}”?</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            The transcript and its answers go with it.
          </p>
          <div className="mt-2 flex gap-1.5">
            <button
              type="button"
              autoFocus
              disabled={remove.isPending}
              onClick={confirmDelete}
              className="min-h-8 rounded-md bg-destructive px-2.5 text-xs font-medium text-destructive-foreground transition-opacity duration-150 ease-out hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            >
              {remove.isPending ? 'Deleting…' : 'Delete'}
            </button>
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => setMode('idle')}
              className="min-h-8 rounded-md border border-input px-2.5 text-xs transition-colors duration-150 ease-out hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            >
              Cancel
            </button>
          </div>
        </div>
      </li>
    );
  }

  return (
    <li className="group relative">
      <Link
        href={`/ask/${conversation.id}`}
        aria-current={active ? 'page' : undefined}
        className={cn(
          // Right padding reserves space for the action buttons, which are
          // siblings rather than children: a button inside an anchor is invalid
          // markup and swallows the row's own click target.
          // A left rule marks the open thread instead of a filled pill: this is
          // a navigable index of saved work, not a chat roster.
          'flex flex-col gap-0.5 border-l-2 py-2.5 pl-3 pr-14 transition-colors duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
          active
            ? 'border-primary bg-primary/[0.06]'
            : 'border-transparent hover:bg-accent',
        )}
      >
        <span
          className={cn(
            'truncate text-sm font-medium',
            active ? 'text-primary' : 'text-foreground',
          )}
        >
          {title}
        </span>
        <RelativeTime
          iso={conversation.updated_at}
          className="text-xs tabular-nums text-muted-foreground"
        />
      </Link>
      {/* On a pointer device: hidden until hover or keyboard focus, but always
          in the tab order. On touch there is no hover, so they stay visible —
          otherwise rename and delete would simply be unreachable. */}
      <div className="absolute right-1.5 top-1.5 flex items-center gap-0.5 transition-opacity duration-150 ease-out focus-within:opacity-100 group-hover:opacity-100 md:opacity-0">
        <button
          type="button"
          disabled={busy}
          aria-label={`Rename ${title}`}
          title="Rename"
          onClick={startRename}
          className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <Pencil className="size-3.5" aria-hidden />
        </button>
        <button
          type="button"
          disabled={busy}
          aria-label={`Delete ${title}`}
          title="Delete"
          onClick={() => {
            setError(null);
            setMode('confirming-delete');
          }}
          className="rounded-md p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <Trash2 className="size-3.5" aria-hidden />
        </button>
      </div>
      {error ? (
        <p role="alert" className="px-2 pb-1 text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </li>
  );
}
