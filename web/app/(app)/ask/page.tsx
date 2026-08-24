import type { Metadata } from 'next';
import { ConversationList } from '@/components/conversation-list';
import { ConversationView } from '@/components/conversation-view';

export const metadata: Metadata = { title: 'Ask' };

export default function AskPage() {
  return (
    <div className="grid h-[calc(100vh-4rem)] grid-cols-1 md:grid-cols-[280px_1fr]">
      <div className="hidden border-r bg-card md:block">
        <ConversationList />
      </div>
      <ConversationView />
    </div>
  );
}
