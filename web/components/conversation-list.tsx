'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Plus, MessageSquare, Pencil, Trash2, Check, X } from 'lucide-react';
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

export function ConversationList() {
  const pathname = usePathname();
  const { data, isLoading, isError } = useConversations();
  const items = data?.items ?? [];

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="p-3">
        <Button asChild variant="outline" className="w-full justify-start gap-2">
          <Link href="/ask">
            <Plus className="size-4" /> New conversation
          </Link>
        </Button>
      </div>
      <div className="scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto px-2 pb-3">
        <p className="px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Recent
        </p>
        {isLoading ? (
          <div className="space-y-1 px-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : isError ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            History is unavailable right now.
          </p>
        ) : items.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            No conversations yet. Ask a question to start one.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {items.map((conversation) => (
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
          className="flex items-center gap-1 rounded-md bg-accent/60 px-1.5 py-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            commitRename();
          }}
        >
          <label className="sr-only" htmlFor={`rename-${conversation.id}`}>
            Conversation title
          </label>
          <input
            id={`rename-${conversation.id}`}
            ref={inputRef}
            value={draft}
            maxLength={TITLE_MAX_CHARS}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `rename-error-${conversation.id}` : undefined}
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
            className="rounded-sm p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            className="rounded-sm p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-3.5" aria-hidden />
          </button>
        </form>
        {error ? (
          <p
            id={`rename-error-${conversation.id}`}
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
          className="rounded-md bg-destructive/10 px-2 py-2"
        >
          <p className="truncate text-xs text-foreground">Delete “{title}”?</p>
          <div className="mt-1.5 flex gap-1.5">
            <button
              type="button"
              autoFocus
              disabled={remove.isPending}
              onClick={confirmDelete}
              className="rounded-sm bg-destructive px-2 py-1 text-xs font-medium text-destructive-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            >
              {remove.isPending ? 'Deleting…' : 'Delete'}
            </button>
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => setMode('idle')}
              className="rounded-sm border border-input px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
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
          'flex flex-col gap-0.5 rounded-md py-2 pl-2 pr-14 transition-colors',
          active ? 'bg-primary/10' : 'hover:bg-accent',
        )}
      >
        <span className="flex items-center gap-2">
          <MessageSquare
            className={cn(
              'size-3.5 shrink-0',
              active ? 'text-primary' : 'text-muted-foreground',
            )}
            aria-hidden
          />
          <span
            className={cn(
              'truncate text-sm font-medium',
              active ? 'text-primary' : 'text-foreground',
            )}
          >
            {title}
          </span>
        </span>
        <RelativeTime
          iso={conversation.updated_at}
          className="pl-5 text-xs text-muted-foreground"
        />
      </Link>
      {/* Hidden until hover or keyboard focus, but always in the tab order. */}
      <div className="absolute right-1 top-1.5 flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <button
          type="button"
          disabled={busy}
          aria-label={`Rename ${title}`}
          title="Rename"
          onClick={startRename}
          className="rounded-sm p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
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
          className="rounded-sm p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
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
