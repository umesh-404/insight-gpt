'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Plus, MessageSquare } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { RelativeTime } from '@/components/relative-time';
import { useConversations } from '@/lib/hooks';
import { cn } from '@/lib/utils';

export function ConversationList() {
  const pathname = usePathname();
  const { data, isLoading, isError } = useConversations();
  const items = data?.items ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <Button asChild variant="outline" className="w-full justify-start gap-2">
          <Link href="/ask">
            <Plus className="size-4" /> New conversation
          </Link>
        </Button>
      </div>
      <div className="scrollbar-thin flex-1 overflow-y-auto px-2 pb-3">
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
            {items.map((conversation) => {
              const active = pathname === `/ask/${conversation.id}`;
              return (
                <li key={conversation.id}>
                  <Link
                    href={`/ask/${conversation.id}`}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'flex flex-col gap-0.5 rounded-md px-2 py-2 transition-colors',
                      active
                        ? 'bg-primary/10'
                        : 'hover:bg-accent',
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
                        {conversation.title || 'Untitled conversation'}
                      </span>
                    </span>
                    <RelativeTime
                      iso={conversation.updated_at}
                      className="pl-5 text-xs text-muted-foreground"
                    />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
