'use client';

import { Loader2 } from 'lucide-react';
import { ConversationList } from '@/components/conversation-list';
import { ConversationView } from '@/components/conversation-view';
import { ErrorState } from '@/components/states';
import { useConversation } from '@/lib/hooks';

export default function ConversationPage({
  params,
}: {
  params: { conversationId: string };
}) {
  const { data, isLoading, isError, error, refetch } = useConversation(
    params.conversationId,
  );

  return (
    <div className="grid h-[calc(100vh-4rem)] grid-cols-1 md:grid-cols-[280px_1fr]">
      <div className="hidden border-r bg-card md:block">
        <ConversationList />
      </div>
      {isLoading ? (
        <div className="flex items-center justify-center" aria-busy="true">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
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
