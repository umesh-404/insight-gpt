import type { Metadata } from 'next';
import { ConversationList } from '@/components/conversation-list';
import { ConversationView } from '@/components/conversation-view';

export const metadata: Metadata = {
  title: 'Ask',
  description:
    'Ask questions in plain English and get cited answers with the governed SQL and source documents attached.',
};

/**
 * `?q=` seeds the thread with a question — the command palette and any deep
 * link use it, so a shared URL reproduces the answer rather than an empty box.
 */
export default function AskPage({
  searchParams,
}: {
  searchParams?: { q?: string | string[] };
}) {
  const raw = searchParams?.q;
  const initialQuestion = (Array.isArray(raw) ? raw[0] : raw)?.slice(0, 2000);

  return (
    <div className="grid h-[calc(100vh-4rem)] grid-cols-1 md:grid-cols-[280px_1fr]">
      <div className="hidden border-r bg-card md:block">
        <ConversationList />
      </div>
      <ConversationView
        // Remount when the seeded question changes so it is asked exactly once.
        key={initialQuestion ?? 'blank'}
        initialQuestion={initialQuestion}
      />
    </div>
  );
}
