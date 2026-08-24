'use client';

import * as React from 'react';
import {
  BarChart3,
  Code2,
  FileText,
  HelpCircle,
  MessageSquareText,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
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
import type { AnswerEnvelope, ApiErrorBody, Confidence, Route } from '@/lib/types';

const ROUTE_LABEL: Record<Route, string> = {
  structured: 'SQL',
  unstructured: 'Documents',
  hybrid: 'SQL + Documents',
  clarify: 'Needs clarification',
};

const CONFIDENCE_CLASS: Record<Confidence, string> = {
  high: 'text-success',
  medium: 'text-muted-foreground',
  low: 'text-warning',
};

export interface InsightCardProps {
  question: string;
  envelope: AnswerEnvelope;
  /** True while tokens are still streaming into `envelope.answer`. */
  streaming?: boolean;
  /** Mid-stream failure; rendered in place of the missing tail of the answer. */
  error?: ApiErrorBody | null;
  messageId?: string;
  feedback?: 'up' | 'down' | null;
  onFeedback?: (rating: 'up' | 'down') => void;
}

export function InsightCard({
  question,
  envelope,
  streaming,
  error,
  feedback,
  onFeedback,
}: InsightCardProps) {
  const chart = envelope.chart_spec;
  const hasChart = Boolean(chart && (chart.data?.length ?? 0) > 0);
  const sqlStatements = envelope.sql ?? [];
  const hasSql = sqlStatements.length > 0;
  const citations = envelope.citations ?? [];
  const tables = envelope.tables ?? [];
  const hasCitations = citations.length > 0;
  const hasTables = tables.length > 0;
  const route = envelope.route;

  return (
    <Card className="animate-fade-in">
      <CardContent className="p-5">
        {/* Question echo */}
        {question ? (
          <div className="mb-4 flex items-start gap-3">
            <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
              You
            </span>
            <p className="text-sm font-medium text-foreground">{question}</p>
          </div>
        ) : null}

        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <MessageSquareText className="size-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            {/* Route + confidence */}
            {route || envelope.confidence ? (
              <div className="mb-2 flex flex-wrap items-center gap-2">
                {route ? (
                  <Badge variant="secondary">{ROUTE_LABEL[route] ?? route}</Badge>
                ) : null}
                {envelope.confidence ? (
                  <span
                    className={cn('text-xs', CONFIDENCE_CLASS[envelope.confidence])}
                  >
                    {envelope.confidence} confidence
                  </span>
                ) : null}
              </div>
            ) : null}

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
                    {sqlStatements.length > 1 ? (
                      <Badge variant="muted" className="ml-1 px-1.5 py-0">
                        {sqlStatements.length}
                      </Badge>
                    ) : null}
                  </TabsTrigger>
                ) : null}
                {hasCitations ? (
                  <TabsTrigger value="sources">
                    <FileText className="size-3.5" /> Sources
                    <Badge variant="muted" className="ml-1 px-1.5 py-0">
                      {citations.length}
                    </Badge>
                  </TabsTrigger>
                ) : null}
              </TabsList>

              <TabsContent value="answer">
                <div aria-live="polite" className="text-sm leading-relaxed text-foreground">
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

                {envelope.clarifying_question ? (
                  <div className="mt-4 flex gap-2.5 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">
                    <HelpCircle
                      className="mt-0.5 size-4 shrink-0 text-primary"
                      aria-hidden
                    />
                    <p className="text-foreground">{envelope.clarifying_question}</p>
                  </div>
                ) : null}

                {error ? (
                  <div
                    role="alert"
                    className="mt-4 flex gap-2.5 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm"
                  >
                    <TriangleAlert
                      className="mt-0.5 size-4 shrink-0 text-destructive"
                      aria-hidden
                    />
                    <div className="min-w-0">
                      <p className="font-medium text-destructive">
                        {error.code === 'guardrail_rejected'
                          ? 'Blocked by a guardrail'
                          : 'This answer stopped early'}
                      </p>
                      <p className="mt-0.5 text-muted-foreground">{error.message}</p>
                    </div>
                  </div>
                ) : null}

                {hasTables ? (
                  <div className="mt-4 space-y-4">
                    {tables.map((table, i) => (
                      <DataTable key={`${table.name}-${i}`} block={table} />
                    ))}
                  </div>
                ) : null}

                {!streaming ? <CaveatNote caveats={envelope.caveats ?? []} /> : null}

                {/* Feedback */}
                {!streaming && onFeedback ? (
                  <div className="mt-4 flex items-center gap-2 border-t pt-3">
                    <span className="text-xs text-muted-foreground">Was this helpful?</span>
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

              {hasChart && chart ? (
                <TabsContent value="chart">
                  <ChartRenderer spec={chart} />
                </TabsContent>
              ) : null}

              {hasSql ? (
                <TabsContent value="sql">
                  <div className="space-y-3">
                    {sqlStatements.map((statement, i) => (
                      <SqlViewer
                        key={i}
                        sql={statement}
                        dialect={envelope.dialect ?? 'sql'}
                      />
                    ))}
                  </div>
                </TabsContent>
              ) : null}

              {hasCitations ? (
                <TabsContent value="sources">
                  <CitationList citations={citations} />
                </TabsContent>
              ) : null}
            </Tabs>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
