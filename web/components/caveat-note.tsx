import { Info } from 'lucide-react';

/**
 * The engine's own disclosures about an answer (docs/07 §4.1) — thin evidence,
 * an unclosed period, a degraded provider.
 *
 * Deliberately *not* behind a disclosure. An answer that hides its uncertainty
 * is a bug here, so the limits sit in the reading flow directly under the
 * narrative. Kept visually quiet — a hairline rule and muted type, no tinted
 * card — because a caveat qualifies the answer rather than competing with it,
 * and a coloured panel would read as a warning that something went wrong.
 */
export function CaveatNote({ caveats }: { caveats: string[] }) {
  if (!caveats.length) return null;
  return (
    <section className="mt-4 border-l-2 border-border pl-3.5">
      <h4 className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
        <Info className="size-3.5" aria-hidden />
        Assumptions and limits
      </h4>
      <ul className="mt-1.5 space-y-1 text-sm leading-relaxed text-muted-foreground">
        {caveats.map((caveat, i) => (
          <li key={i} className="max-w-[80ch]">
            {caveat}
          </li>
        ))}
      </ul>
    </section>
  );
}
