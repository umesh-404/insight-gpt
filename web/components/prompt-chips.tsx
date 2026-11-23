'use client';

import {
  Boxes,
  Code2,
  FileText,
  LineChart,
  MessagesSquare,
  ShieldCheck,
  TrendingDown,
  type LucideIcon,
} from 'lucide-react';

interface ExamplePrompt {
  icon: LucideIcon;
  label: string;
  /** What the answer will contain — sets expectations before the first ask. */
  hint: string;
}

/**
 * The four questions the engine genuinely answers well (docs/07 §4.1), one per
 * path through the router: hybrid, structured-scalar, structured-ranked, and
 * pure retrieval. Nothing here promises a capability the engine does not have —
 * an empty state that oversells is a demo, not a tool.
 *
 * Also consumed by the command palette, so the shape is stable.
 */
export const EXAMPLE_PROMPTS: readonly ExamplePrompt[] = [
  {
    icon: TrendingDown,
    label: 'Why did sales decline last quarter?',
    hint: 'Governed SQL, then support documents for the cause',
  },
  {
    icon: LineChart,
    label: 'What was revenue last quarter?',
    hint: 'One governed metric, with the query that produced it',
  },
  {
    icon: Boxes,
    label: 'Which products should we restock?',
    hint: 'Inventory metrics, ranked by cover',
  },
  {
    icon: MessagesSquare,
    label: 'Summarize customer complaints this month.',
    hint: 'Retrieval over support tickets and reviews',
  },
] as const;

/** The trust contract, stated before a single answer exists. */
const GUARANTEES: ReadonlyArray<{ icon: LucideIcon; label: string }> = [
  { icon: Code2, label: 'The read-only SQL that produced every number' },
  { icon: FileText, label: 'A dated source document behind every claim' },
  { icon: ShieldCheck, label: 'A refusal when the question cannot be grounded' },
];

/**
 * Empty-thread hero.
 *
 * Two jobs, in this order: state what the product guarantees, and give the
 * reader four questions that prove it in one click. The guarantee row is the
 * part that distinguishes this from a generic chat box, so it is typographic
 * and plainly worded rather than a row of decorated tiles.
 */
export function PromptChips({
  onSelect,
}: {
  onSelect: (prompt: string) => void;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-1 lg:max-w-4xl xl:max-w-5xl">
      <p className="text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Conversational analytics
      </p>
      <h2 className="mt-2 max-w-[28ch] text-balance text-3xl font-semibold tracking-tight text-foreground">
        Ask your warehouse a question.
      </h2>
      <p className="mt-2.5 max-w-[56ch] text-base leading-relaxed text-muted-foreground">
        Plain English becomes governed SQL and cited retrieval. Every answer
        arrives with its evidence attached — or with the reason it was withheld.
      </p>

      <h3 className="mt-9 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Start with one of these
      </h3>
      <div className="mt-3 grid gap-px overflow-hidden rounded-xl border bg-border sm:grid-cols-2">
        {EXAMPLE_PROMPTS.map(({ icon: Icon, label, hint }) => (
          <button
            key={label}
            type="button"
            onClick={() => onSelect(label)}
            // One hairline grid, not four floating cards: the 1px gaps come
            // from the parent's background showing through `gap-px`, so there
            // is no card-in-card nesting and no shadow stack.
            className="group flex min-h-[4.5rem] flex-col justify-center gap-1 bg-card px-4 py-3.5 text-left transition-colors duration-150 ease-out hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring active:bg-accent"
          >
            <span className="flex items-start gap-2.5">
              <Icon
                className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-primary"
                aria-hidden
              />
              <span className="text-base font-medium leading-snug text-foreground">
                {label}
              </span>
            </span>
            <span className="pl-[1.625rem] text-xs text-muted-foreground">
              {hint}
            </span>
          </button>
        ))}
      </div>

      <h3 className="mt-9 text-2xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        What every answer carries
      </h3>
      <ul className="mt-3 divide-y border-y">
        {GUARANTEES.map(({ icon: Icon, label }) => (
          <li
            key={label}
            className="flex items-center gap-2.5 py-2.5 text-sm text-foreground"
          >
            <Icon className="size-4 shrink-0 text-primary" aria-hidden />
            {label}
          </li>
        ))}
      </ul>
    </div>
  );
}
