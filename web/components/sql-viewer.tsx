'use client';

import * as React from 'react';
import { Check, Copy, ShieldCheck } from 'lucide-react';
import { cn } from '@/lib/utils';

const KEYWORDS =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|AND|OR|NOT|NULL|LIMIT|OFFSET|SUM|COUNT|AVG|MIN|MAX|DISTINCT|CASE|WHEN|THEN|ELSE|END|date_trunc|NULLIF|COALESCE|date|HAVING|UNION|WITH|OVER|PARTITION)\b/gi;

/**
 * Minimal, dependency-free SQL highlighter.
 *
 * Three tones only — accented keywords, one literal hue, muted comments. A
 * six-colour theme looks like a code editor but loses legibility the moment the
 * page is thrown at a projector in a lit room, which is where this component
 * actually gets read.
 */
function highlightLine(line: string): React.ReactNode {
  if (line.trimStart().startsWith('--')) {
    return <span className="italic text-muted-foreground">{line}</span>;
  }

  const nodes: React.ReactNode[] = [];
  let key = 0;

  // Strings first: a keyword inside a quoted literal is not a keyword.
  for (const part of line.split(/('[^']*')/g)) {
    if (!part) continue;
    if (part.startsWith("'") && part.endsWith("'") && part.length > 1) {
      nodes.push(
        <span key={key++} className="text-chart-2">
          {part}
        </span>,
      );
      continue;
    }
    for (const seg of part.split(KEYWORDS)) {
      if (!seg) continue;
      KEYWORDS.lastIndex = 0;
      if (KEYWORDS.test(seg)) {
        KEYWORDS.lastIndex = 0;
        nodes.push(
          <span key={key++} className="font-semibold text-primary">
            {seg}
          </span>,
        );
        continue;
      }
      for (const n of seg.split(/(\b\d+(?:\.\d+)?\b)/g)) {
        if (!n) continue;
        nodes.push(
          /^\d/.test(n) ? (
            <span key={key++} className="tabular-nums text-chart-2">
              {n}
            </span>
          ) : (
            <React.Fragment key={key++}>{n}</React.Fragment>
          ),
        );
      }
    }
  }
  return nodes;
}

/**
 * The governed statement that produced the numbers — the single strongest piece
 * of evidence this product has, so it is rendered to be *read*, not skimmed:
 * real monospace at body size, numbered lines so a reviewer can point at one,
 * and the guardrails it passed stated next to it.
 */
export function SqlViewer({
  sql,
  dialect = 'postgres',
  className,
}: {
  sql: string;
  dialect?: string;
  className?: string;
}) {
  const [copied, setCopied] = React.useState(false);
  const lines = React.useMemo(() => sql.replace(/\s+$/, '').split('\n'), [sql]);
  const gutterWidth = `${String(lines.length).length + 1}ch`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable (insecure origin, denied permission) */
    }
  };

  return (
    <div className={cn('overflow-hidden rounded-lg border bg-elevated', className)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-2">
        <span className="font-mono text-2xs uppercase tracking-wide text-muted-foreground">
          {dialect}
        </span>
        <span className="inline-flex items-center gap-1.5 text-xs text-foreground">
          <ShieldCheck className="size-3.5 text-primary" aria-hidden />
          Read-only · allow-listed tables · LIMIT enforced
        </span>
        <button
          type="button"
          onClick={copy}
          className="ml-auto inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {copied ? (
            <>
              <Check className="size-3.5 text-success" aria-hidden /> Copied
            </>
          ) : (
            <>
              <Copy className="size-3.5" aria-hidden /> Copy query
            </>
          )}
        </button>
      </div>

      <pre className="scrollbar-thin max-h-96 overflow-auto py-2.5 text-sm leading-relaxed">
        <code className="block font-mono text-foreground">
          {lines.map((line, i) => (
            <span key={i} className="flex">
              {/* Numbered so a reviewer on a projector can say "line 4".
                  `select-none` keeps the numbers out of a copied selection. */}
              <span
                aria-hidden
                className="shrink-0 select-none pr-3 pl-3 text-right text-muted-foreground/60"
                style={{ width: gutterWidth }}
              >
                {i + 1}
              </span>
              <span className="min-w-0 flex-1 whitespace-pre pr-3">
                {highlightLine(line) || ' '}
              </span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
