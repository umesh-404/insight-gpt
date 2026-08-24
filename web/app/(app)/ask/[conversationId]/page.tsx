'use client';

import Link from 'next/link';
import { Loader2, MessageSquareOff } from 'lucide-react';
import { ConversationList } from '@/components/conversation-list';
import { ConversationView } from '@/components/conversation-view';
import { EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { useConversation } from '@/lib/hooks';
import { ApiError } from '@/lib/types';

export default function ConversationPage({
  params,
}: {
  params: { conversationId: string };
}) {
  const { data, isLoading, isError, error, refetch } = useConversation(
    params.conversationId,
  );

  // A conversation owned by another user comes back as 404 — that is "not
  // found", not a failure worth an error panel.
  const notFound = error instanceof ApiError && error.status === 404;

  return (
    <div className="grid h-[calc(100vh-4rem)] grid-cols-1 md:grid-cols-[280px_1fr]">
      <div className="hidden border-r bg-card md:block">
        <ConversationList />
      </div>
      {isLoading ? (
        <div className="flex items-center justify-center" aria-busy="true">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
          <span className="sr-only">Loading conversation…</span>
        </div>
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
    </div>
  );
}
