'use client';

import * as React from 'react';
import { Check, Copy } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const KEYWORDS =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|AND|OR|NOT|NULL|LIMIT|OFFSET|SUM|COUNT|AVG|MIN|MAX|DISTINCT|CASE|WHEN|THEN|ELSE|END|date_trunc|NULLIF|COALESCE|date|HAVING|UNION|WITH|OVER|PARTITION)\b/gi;

/** Minimal, dependency-free SQL highlighter — keywords, strings, comments, numbers. */
function highlight(sql: string): React.ReactNode[] {
  const lines = sql.split('\n');
  return lines.map((line, li) => {
    const nodes: React.ReactNode[] = [];
    // Full-line comment.
    if (line.trimStart().startsWith('--')) {
      nodes.push(
        <span key="c" className="text-muted-foreground italic">
          {line}
        </span>,
      );
      return (
        <span key={li} className="block">
          {nodes}
          {'\n'}
        </span>
      );
    }
    let remaining = line;
    let key = 0;
    // Tokenize by strings first, then keywords/numbers within non-string spans.
    const stringSplit = remaining.split(/('[^']*')/g);
    for (const part of stringSplit) {
      if (part.startsWith("'") && part.endsWith("'")) {
        nodes.push(
          <span key={key++} className="text-chart-2">
            {part}
          </span>,
        );
        continue;
      }
      const sub = part.split(KEYWORDS);
      for (const seg of sub) {
        if (!seg) continue;
        if (KEYWORDS.test(seg)) {
          KEYWORDS.lastIndex = 0;
          nodes.push(
            <span key={key++} className="font-medium text-primary">
              {seg}
            </span>,
          );
        } else {
          const numSplit = seg.split(/(\b\d+\b)/g);
          for (const n of numSplit) {
            if (!n) continue;
            if (/^\d+$/.test(n)) {
              nodes.push(
                <span key={key++} className="text-chart-3">
                  {n}
                </span>,
              );
            } else {
              nodes.push(<React.Fragment key={key++}>{n}</React.Fragment>);
            }
          }
        }
      }
    }
    return (
      <span key={li} className="block">
        {nodes}
        {'\n'}
      </span>
    );
  });
}

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

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div className={cn('overflow-hidden rounded-lg border bg-muted/30', className)}>
      <div className="flex items-center justify-between border-b bg-muted/50 px-3 py-1.5">
        <div className="flex items-center gap-2">
          <Badge variant="muted">{dialect}</Badge>
          <span className="text-xs text-muted-foreground">
            Governed, read-only SQL
          </span>
        </div>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label="Copy SQL"
        >
          {copied ? (
            <>
              <Check className="size-3.5 text-success" /> Copied
            </>
          ) : (
            <>
              <Copy className="size-3.5" /> Copy
            </>
          )}
        </button>
      </div>
      <pre className="scrollbar-thin max-h-80 overflow-auto p-4 text-xs leading-relaxed">
        <code className="font-mono text-foreground">{highlight(sql)}</code>
      </pre>
    </div>
  );
}
