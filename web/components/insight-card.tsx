'use client';

import * as React from 'react';
import {
  BarChart3,
  Code2,
  FileText,
  MessageSquareText,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ChartRenderer } from '@/components/chart-renderer';
import { SqlViewer } from '@/components/sql-viewer';
import { CitationList } from '@/components/citation-list';
import { CaveatNote } from '@/components/caveat-note';
import { DataTable } from '@/components/data-table';
import { cn } from '@/lib/utils';
import type { AnswerEnvelope } from '@/lib/types';

const ROUTE_LABEL: Record<NonNullable<AnswerEnvelope['route']>, string> = {
  structured: 'SQL',
  unstructured: 'Documents',
  hybrid: 'SQL + Documents',
};

export interface InsightCardProps {
  question: string;
  envelope: AnswerEnvelope;
  /** True while tokens are still streaming into `envelope.answer`. */
  streaming?: boolean;
  messageId?: string;
  feedback?: 'up' | 'down' | null;
  onFeedback?: (rating: 'up' | 'down') => void;
}

export function InsightCard({
  question,
  envelope,
  streaming,
  feedback,
  onFeedback,
}: InsightCardProps) {
  const hasChart = Boolean(envelope.chart_spec);
  const hasSql = Boolean(envelope.sql);
  const hasCitations = envelope.citations.length > 0;
  const hasTables = envelope.tables.length > 0;

  return (
    <Card className="animate-fade-in">
      <CardContent className="p-5">
        {/* Question echo */}
        <div className="mb-4 flex items-start gap-3">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
            You
          </span>
          <p className="text-sm font-medium text-foreground">{question}</p>
        </div>

        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <MessageSquareText className="size-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            {/* Route + confidence */}
            {(envelope.route || typeof envelope.confidence === 'number') && (
              <div className="mb-2 flex flex-wrap items-center gap-2">
                {envelope.route ? (
                  <Badge variant="secondary">{ROUTE_LABEL[envelope.route]}</Badge>
                ) : null}
                {typeof envelope.confidence === 'number' ? (
                  <span className="text-xs text-muted-foreground">
                    {Math.round(envelope.confidence * 100)}% confidence
                  </span>
                ) : null}
              </div>
            )}

            <Tabs defaultValue="answer">
              <TabsList>
                <TabsTrigger value="answer">
                  <MessageSquareText className="size-3.5" /> Answer
                </TabsTrigger>
                {hasChart ? (
                  <TabsTrigger value="chart">
                    <BarChart3 className="size-3.5" /> Chart
                  </TabsTrigger>
                ) : null}
                {hasSql ? (
                  <TabsTrigger value="sql">
                    <Code2 className="size-3.5" /> SQL
                  </TabsTrigger>
                ) : null}
                {hasCitations ? (
                  <TabsTrigger value="sources">
                    <FileText className="size-3.5" /> Sources
                    <Badge variant="muted" className="ml-1 px-1.5 py-0">
                      {envelope.citations.length}
                    </Badge>
                  </TabsTrigger>
                ) : null}
              </TabsList>

              <TabsContent value="answer">
                <div
                  aria-live="polite"
                  className="text-sm leading-relaxed text-foreground"
                >
                  <p className="whitespace-pre-wrap">
                    {envelope.answer}
                    {streaming ? (
                      <span
                        className="ml-0.5 inline-block h-4 w-[2px] -translate-y-[1px] animate-caret-blink bg-primary align-middle"
                        aria-hidden
                      />
                    ) : null}
                  </p>
                </div>

                {hasTables && !streaming ? (
                  <div className="mt-4 space-y-4">
                    {envelope.tables.map((table, i) => (
                      <DataTable key={i} block={table} />
                    ))}
                  </div>
                ) : null}

                {!streaming ? <CaveatNote caveats={envelope.caveats} /> : null}

                {/* Feedback */}
                {!streaming && onFeedback ? (
                  <div className="mt-4 flex items-center gap-2 border-t pt-3">
                    <span className="text-xs text-muted-foreground">
                      Was this helpful?
                    </span>
                    <button
                      type="button"
                      onClick={() => onFeedback('up')}
                      aria-label="Helpful"
                      aria-pressed={feedback === 'up'}
                      className={cn(
                        'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                        feedback === 'up' && 'bg-success/12 text-success',
                      )}
                    >
                      <ThumbsUp className="size-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onFeedback('down')}
                      aria-label="Not helpful"
                      aria-pressed={feedback === 'down'}
                      className={cn(
                        'rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                        feedback === 'down' && 'bg-destructive/12 text-destructive',
                      )}
                    >
                      <ThumbsDown className="size-4" />
                    </button>
                  </div>
                ) : null}
              </TabsContent>

              {hasChart ? (
                <TabsContent value="chart">
                  <ChartRenderer spec={envelope.chart_spec!} />
                </TabsContent>
              ) : null}

              {hasSql ? (
                <TabsContent value="sql">
                  <SqlViewer
                    sql={envelope.sql!}
                    dialect={envelope.dialect ?? 'postgres'}
                  />
                </TabsContent>
              ) : null}

              {hasCitations ? (
                <TabsContent value="sources">
                  <CitationList citations={envelope.citations} />
                </TabsContent>
              ) : null}
            </Tabs>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
