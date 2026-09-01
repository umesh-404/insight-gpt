'use client';

import {
  Boxes,
  Code2,
  FileText,
  MessagesSquare,
  Sparkles,
  TrendingDown,
  type LucideIcon,
} from 'lucide-react';

interface ExamplePrompt {
  icon: LucideIcon;
  label: string;
  /** What the answer will contain — sets expectations before the first ask. */
  hint: string;
}

/** The flagship questions from docs/07 §4.1, one per capability of the engine. */
export const EXAMPLE_PROMPTS: readonly ExamplePrompt[] = [
  {
    icon: TrendingDown,
    label: 'Why did performance change this month?',
    hint: 'Compare trends and spot root causes',
  },
  {
    icon: Boxes,
    label: 'What should we focus on next?',
    hint: 'Prioritize actions and opportunities',
  },
  {
    icon: MessagesSquare,
    label: 'Summarize customer feedback and key issues.',
    hint: 'Turn raw input into clear insights',
  },
] as const;

/** What every answer carries — the trust contract, stated up front. */
const GUARANTEES: ReadonlyArray<{ icon: LucideIcon; label: string }> = [
  { icon: Code2, label: 'The SQL that produced the numbers' },
  { icon: FileText, label: 'The documents behind each claim' },
  { icon: Sparkles, label: 'A refusal when it cannot be grounded' },
];

/**
 * Empty-thread hero.
 *
 * Two jobs: give the evaluator something to click immediately, and state the
 * product's promise before a single answer exists. The guarantee row is the
 * part that distinguishes this from a generic chat box.
 */
export function PromptChips({
  onSelect,
}: {
  onSelect: (prompt: string) => void;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-2 text-center lg:max-w-4xl xl:max-w-5xl">
      <div className="relative mx-auto mb-5 flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-raised">
        <Sparkles className="size-6" aria-hidden />
        <span
          className="absolute inset-0 -z-10 rounded-2xl bg-primary/25 blur-xl"
          aria-hidden
        />
      </div>

      <h2 className="text-2xl font-semibold text-foreground">
        Ask me anything
      </h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        I can help with analysis, trends, root causes, and follow-up questions —
        and I’ll ground the answer in the evidence.
      </p>

      <div className="stagger mt-8 grid gap-3 sm:grid-cols-3">
        {EXAMPLE_PROMPTS.map(({ icon: Icon, label, hint }) => (
          <button
            key={label}
            type="button"
            onClick={() => onSelect(label)}
            className="group flex h-full flex-col items-start gap-2.5 rounded-xl border bg-card p-4 text-left shadow-soft transition-[box-shadow,border-color,transform] duration-200 ease-out hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <span className="flex size-8 items-center justify-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
              <Icon className="size-4" aria-hidden />
            </span>
            <span className="text-sm font-medium leading-snug text-foreground">
              {label}
            </span>
            <span className="mt-auto text-2xs text-muted-foreground">{hint}</span>
          </button>
        ))}
      </div>

      <ul className="mt-8 flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
        {GUARANTEES.map(({ icon: Icon, label }) => (
          <li
            key={label}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <Icon className="size-3.5 text-primary/70" aria-hidden />
            {label}
          </li>
        ))}
      </ul>
    </div>
  );
}
