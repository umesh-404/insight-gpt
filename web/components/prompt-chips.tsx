'use client';

import { Sparkles, TrendingDown, Boxes, MessagesSquare } from 'lucide-react';

export const EXAMPLE_PROMPTS = [
  {
    icon: TrendingDown,
    label: 'Why did sales decline last quarter?',
  },
  {
    icon: Boxes,
    label: 'Which products should we restock?',
  },
  {
    icon: MessagesSquare,
    label: 'Summarize customer complaints this month.',
  },
] as const;

/** Seed the flagship questions on an empty thread (docs/07 §4.1). */
export function PromptChips({
  onSelect,
}: {
  onSelect: (prompt: string) => void;
}) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-xl bg-primary/10">
        <Sparkles className="size-6 text-primary" aria-hidden />
      </div>
      <h2 className="text-xl font-semibold tracking-tight">
        Ask anything about your business data
      </h2>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Every answer shows its SQL, sources, and a chart — one click away.
      </p>
      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        {EXAMPLE_PROMPTS.map(({ icon: Icon, label }) => (
          <button
            key={label}
            type="button"
            onClick={() => onSelect(label)}
            className="group flex flex-col items-start gap-2 rounded-lg border bg-card p-4 text-left transition-all hover:border-primary/50 hover:shadow-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <span className="flex size-8 items-center justify-center rounded-md bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
              <Icon className="size-4" aria-hidden />
            </span>
            <span className="text-sm font-medium text-foreground">{label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
