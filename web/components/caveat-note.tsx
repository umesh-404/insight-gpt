import { Info } from 'lucide-react';

/** Muted assumptions/limitations strip rendered under an answer (docs/07 §4.1). */
export function CaveatNote({ caveats }: { caveats: string[] }) {
  if (!caveats.length) return null;
  return (
    <div className="mt-4 flex gap-2.5 rounded-md border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
      <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div>
        <p className="mb-1 font-medium text-foreground">Caveats</p>
        <ul className="list-disc space-y-1 pl-4">
          {caveats.map((caveat, i) => (
            <li key={i}>{caveat}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
