'use client';

import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import { api } from './api';
import { ApiError } from './types';
import type {
  Conversation,
  ConversationSummary,
  ForecastCapabilityReport,
  ForecastResult,
  Insight,
  InsightPage,
  MetricQuery,
  MetricResult,
  MetricsCatalog,
  Paginated,
  Pipeline,
  PipelineRun,
  Report,
  ReportSummary,
  Source,
  SourceConfig,
  SystemStatus,
} from './types';

/** Query keys, centralized so invalidation stays consistent. */
export const qk = {
  conversations: ['conversations'] as const,
  conversation: (id: string) => ['conversation', id] as const,
  metrics: ['metrics'] as const,
  metric: (q: MetricQuery) => ['metric', q] as const,
  pipelines: ['pipelines'] as const,
  runs: ['pipeline-runs'] as const,
  run: (id: string) => ['pipeline-run', id] as const,
  sources: ['sources'] as const,
  reports: ['reports'] as const,
  report: (id: string) => ['report', id] as const,
  insights: (limit: number, offset: number) => ['insights', limit, offset] as const,
  insight: (id: string) => ['insight', id] as const,
  forecastMetrics: (grain: string) => ['forecast-metrics', grain] as const,
  forecast: (metric: string, grain: string, horizon: number) =>
    ['forecast', metric, grain, horizon] as const,
  status: ['status'] as const,
};

/** Never retry a request the server has definitively refused. */
function retryUnlessClientError(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 1;
}

export function useConversations(): UseQueryResult<Paginated<ConversationSummary>> {
  return useQuery({
    queryKey: qk.conversations,
    queryFn: () => api.listConversations(),
    retry: retryUnlessClientError,
  });
}

export function useConversation(id: string): UseQueryResult<Conversation> {
  return useQuery({
    queryKey: qk.conversation(id),
    queryFn: () => api.getConversation(id),
    enabled: Boolean(id),
    retry: retryUnlessClientError,
  });
}

/* ----------------------------------------------------------------------------
 * Conversation mutations
 *
 * Both are optimistic: the sidebar is a navigation surface, so it must react on
 * the click, not a round trip later. `onMutate` snapshots the cache and
 * `onError` restores it, so a rejected rename or delete leaves the list exactly
 * as it was rather than in a half-applied state.
 * ------------------------------------------------------------------------- */

type ConversationsPage = Paginated<ConversationSummary>;

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.renameConversation(id, title),
    onMutate: async ({ id, title }) => {
      // Cancel in-flight refetches first, or one could land after the optimistic
      // write and revert the new title on screen.
      await qc.cancelQueries({ queryKey: qk.conversations });
      const previous = qc.getQueryData<ConversationsPage>(qk.conversations);
      const clean = title.replace(/\s+/g, ' ').trim();
      qc.setQueryData<ConversationsPage>(qk.conversations, (page) =>
        page
          ? {
              ...page,
              items: page.items.map((c) =>
                c.id === id ? { ...c, title: clean } : c,
              ),
            }
          : page,
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        qc.setQueryData(qk.conversations, context.previous);
      }
    },
    onSuccess: (summary, { id }) => {
      // Reconcile with the server's normalized title (it collapses whitespace).
      qc.setQueryData<ConversationsPage>(qk.conversations, (page) =>
        page
          ? { ...page, items: page.items.map((c) => (c.id === id ? summary : c)) }
          : page,
      );
      void qc.invalidateQueries({ queryKey: qk.conversation(id) });
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.conversations });
    },
  });
}

/**
 * Delete is deliberately *not* optimistic.
 *
 * Dropping the row in `onMutate` unmounts the component that owns the
 * mutation, and TanStack Query does not run per-call `mutate(…, {onSuccess})`
 * callbacks for an observer with no listeners — the "navigate away from the
 * conversation you just deleted" step would silently never fire. Removing on
 * success keeps the row mounted long enough to show its pending state, report
 * a failure in place, and redirect.
 */
export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: (_data, id) => {
      qc.setQueryData<ConversationsPage>(qk.conversations, (page) =>
        page
          ? {
              ...page,
              items: page.items.filter((c) => c.id !== id),
              total: Math.max(0, (page.total ?? page.items.length) - 1),
            }
          : page,
      );
      // The transcript is gone server-side; drop it so a stale back-navigation
      // cannot render a conversation that no longer exists.
      qc.removeQueries({ queryKey: qk.conversation(id) });
      void qc.invalidateQueries({ queryKey: qk.conversations });
    },
  });
}

export function useMetricsCatalog(): UseQueryResult<MetricsCatalog> {
  return useQuery({
    queryKey: qk.metrics,
    queryFn: () => api.listMetrics(),
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
  });
}

export function useMetricQuery(
  query: MetricQuery,
  enabled = true,
): UseQueryResult<MetricResult> {
  return useQuery({
    queryKey: qk.metric(query),
    queryFn: () => api.queryMetric(query),
    enabled,
    staleTime: 30_000,
    retry: retryUnlessClientError,
  });
}

/** Run several governed queries in parallel and keep them keyed by name. */
export function useMetricQueries(
  queries: Array<{ name: string; query: MetricQuery }>,
  enabled = true,
): {
  byName: Record<string, MetricResult | undefined>;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} {
  const results = useQueries({
    queries: queries.map(({ query }) => ({
      queryKey: qk.metric(query),
      queryFn: () => api.queryMetric(query),
      enabled,
      staleTime: 30_000,
      retry: retryUnlessClientError,
    })),
  });

  const byName: Record<string, MetricResult | undefined> = {};
  queries.forEach(({ name }, i) => {
    byName[name] = results[i]?.data;
  });

  return {
    byName,
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
    error: results.find((r) => r.error)?.error,
    refetch: () => {
      for (const result of results) void result.refetch();
    },
  };
}

export function usePipelines(): UseQueryResult<Pipeline[]> {
  return useQuery({
    queryKey: qk.pipelines,
    queryFn: () => api.listPipelines(),
    retry: retryUnlessClientError,
  });
}

export function useRuns(): UseQueryResult<PipelineRun[]> {
  return useQuery({
    queryKey: qk.runs,
    queryFn: () => api.listRuns(),
    retry: retryUnlessClientError,
    // Keep the history fresh while a run is in flight.
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === 'running' || r.status === 'queued')
        ? 5000
        : false,
  });
}

/** Polls while the run is non-terminal; stops once it reaches a terminal state. */
export function useRun(id: string): UseQueryResult<PipelineRun> {
  return useQuery({
    queryKey: qk.run(id),
    queryFn: () => api.getRun(id),
    enabled: Boolean(id),
    retry: retryUnlessClientError,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running' || status === 'queued' ? 4000 : false;
    },
  });
}

export function useSources(): UseQueryResult<Source[]> {
  return useQuery({
    queryKey: qk.sources,
    queryFn: () => api.listSources(),
    retry: retryUnlessClientError,
  });
}

export function useReports(): UseQueryResult<ReportSummary[]> {
  return useQuery({
    queryKey: qk.reports,
    queryFn: () => api.listReports(),
    retry: retryUnlessClientError,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === 'generating') ? 4000 : false,
  });
}

export function useReport(id: string): UseQueryResult<Report> {
  return useQuery({
    queryKey: qk.report(id),
    queryFn: () => api.getReport(id),
    enabled: Boolean(id),
    retry: retryUnlessClientError,
    refetchInterval: (query) =>
      query.state.data?.status === 'generating' ? 3000 : false,
  });
}

export function useInsights(
  limit = 20,
  offset = 0,
): UseQueryResult<InsightPage> {
  return useQuery({
    queryKey: qk.insights(limit, offset),
    queryFn: () => api.listInsights(limit, offset),
    staleTime: 60_000,
    retry: retryUnlessClientError,
  });
}

export function useInsight(id: string): UseQueryResult<Insight> {
  return useQuery({
    queryKey: qk.insight(id),
    queryFn: () => api.getInsight(id),
    enabled: Boolean(id),
    retry: retryUnlessClientError,
  });
}

/** Re-run detection now and refresh every cached insight list. */
export function useRefreshInsights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.refreshInsights(),
    onSuccess: (page) => {
      qc.setQueryData(qk.insights(20, 0), page);
      void qc.invalidateQueries({ queryKey: ['insights'] });
    },
  });
}

export function useStatus(): UseQueryResult<SystemStatus> {
  return useQuery({
    queryKey: qk.status,
    queryFn: () => api.status(),
    retry: retryUnlessClientError,
  });
}

export function useTriggerRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pipeline: string) => api.triggerRun(pipeline),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.runs });
      void qc.invalidateQueries({ queryKey: qk.pipelines });
    },
  });
}

export function useTestSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.testSource(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.sources });
    },
  });
}

export function useCreateSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: SourceConfig) => api.createSource(config),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.sources });
    },
  });
}

export function useDeleteSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteSource(id),
    onSuccess: (_data, id) => {
      // Drop the row immediately so the table never shows a source the server
      // has already removed, then reconcile with a refetch.
      qc.setQueryData<Source[]>(qk.sources, (prev) =>
        prev ? prev.filter((s) => s.id !== id) : prev,
      );
      void qc.invalidateQueries({ queryKey: qk.sources });
    },
  });
}


/* ----------------------------------------------------------------------------
 * Forecasting
 * ------------------------------------------------------------------------- */

/** Which governed metrics can be projected at this grain, and why not. */
export function useForecastMetrics(
  grain = 'quarter',
): UseQueryResult<ForecastCapabilityReport> {
  return useQuery({
    queryKey: qk.forecastMetrics(grain),
    queryFn: () => api.forecastMetrics(grain),
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
  });
}

export function useForecast(
  metric: string,
  grain = 'quarter',
  horizon = 4,
  enabled = true,
): UseQueryResult<ForecastResult> {
  return useQuery({
    queryKey: qk.forecast(metric, grain, horizon),
    queryFn: () => api.forecast({ metric, grain, horizon }),
    enabled: enabled && Boolean(metric),
    staleTime: 60_000,
    retry: retryUnlessClientError,
  });
}
