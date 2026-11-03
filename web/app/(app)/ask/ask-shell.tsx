'use client';

import * as React from 'react';
import { ChevronRight, History } from 'lucide-react';
import { ConversationList } from '@/components/conversation-list';

/**
 * Two-pane shell for the /ask surface: saved threads on the left, the live
 * thread on the right.
 *
 * The history pane is a fixed column from `md` up. Below that it becomes a
 * native `<details>` above the thread — a phone has no room for a permanent
 * column, but dropping the pane entirely (what this did before) left saved
 * conversations completely unreachable on a phone.
 */
export function AskShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col md:grid md:grid-cols-[17rem_1fr] lg:grid-cols-[19rem_1fr]">
      <details className="group shrink-0 border-b md:hidden">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-4 text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <History className="size-4 text-muted-foreground" aria-hidden />
          Conversation history
          <ChevronRight
            className="size-4 text-muted-foreground transition-transform duration-150 ease-out group-open:rotate-90"
            aria-hidden
          />
        </summary>
        <div className="h-72 border-t">
          <ConversationList />
        </div>
      </details>

      <div className="hidden border-r bg-card md:block">
        <ConversationList />
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}
