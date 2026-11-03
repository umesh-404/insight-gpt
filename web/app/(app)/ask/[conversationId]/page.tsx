'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { MessageSquareOff } from 'lucide-react';
import { ConversationView } from '@/components/conversation-view';
import { EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useConversation } from '@/lib/hooks';
import { ApiError } from '@/lib/types';
import { AskShell } from '../ask-shell';

/**
 * Skeleton shaped like the thread that is about to replace it — a question
 * heading, the evidence tab strip, and a few lines of narrative. A centred
 * spinner would tell the reader nothing about what is arriving, and the swap
 * would shift the whole page.
 */
function ThreadSkeleton() {
  return (
    <div className="px-4 py-6 sm:px-6" aria-busy="true">
      <span className="sr-only">Loading conversation…</span>
      <div className="mx-auto w-full max-w-3xl space-y-6 lg:max-w-4xl xl:max-w-5xl">
        {[0, 1].map((i) => (
          <div key={i} className="rounded-xl border bg-card p-5">
            <Skeleton className="h-6 w-2/3" />
            <div className="mt-5 flex gap-2">
              <Skeleton className="h-7 w-20" />
              <Skeleton className="h-7 w-16" />
              <Skeleton className="h-7 w-20" />
            </div>
            <div className="mt-4 space-y-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-11/12" />
              <Skeleton className="h-4 w-4/5" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ConversationPage({
  params,
}: {
  params: { conversationId: string };
}) {
  const router = useRouter();
  const { data, isLoading, isError, error, refetch } = useConversation(
    params.conversationId,
  );

  // A conversation owned by another user comes back as 404 — that is "not
  // found", not a failure worth an error panel.
  const notFound = error instanceof ApiError && error.status === 404;

  React.useEffect(() => {
    if (notFound) {
      router.replace('/ask');
    }
  }, [notFound, router]);

  return (
    <AskShell>
      {isLoading ? (
        <ThreadSkeleton />
      ) : notFound ? (
        <div className="p-6">
          <EmptyState
            title="Conversation not found"
            description="It may have been deleted, or it belongs to another account."
            icon={<MessageSquareOff className="size-5" aria-hidden />}
            action={
              <Button asChild variant="outline" size="sm">
                <Link href="/ask">Start a new conversation</Link>
              </Button>
            }
          />
        </div>
      ) : isError ? (
        <div className="p-6">
          <ErrorState error={error} onRetry={() => void refetch()} />
        </div>
      ) : (
        <ConversationView
          key={params.conversationId}
          conversationId={params.conversationId}
          initialTurns={data?.turns ?? []}
        />
      )}
    </AskShell>
  );
}
