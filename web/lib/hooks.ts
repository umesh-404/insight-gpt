'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import { api } from './api';
import type {
  Conversation,
  ConversationSummary,
  MetricDef,
  MetricQuery,
  MetricResult,
  Paginated,
  Pipeline,
  PipelineRun,
  Report,
  ReportSummary,
  Source,
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
  status: ['status'] as const,
};

export function useConversations(): UseQueryResult<
  Paginated<ConversationSummary>
> {
  return useQuery({ queryKey: qk.conversations, queryFn: api.listConversations });
}

export function useConversation(id: string): UseQueryResult<Conversation> {
  return useQuery({
    queryKey: qk.conversation(id),
    queryFn: () => api.getConversation(id),
    enabled: Boolean(id),
  });
}

export function useMetrics(): UseQueryResult<MetricDef[]> {
  return useQuery({ queryKey: qk.metrics, queryFn: api.listMetrics });
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
  });
}

export function usePipelines(): UseQueryResult<Pipeline[]> {
  return useQuery({ queryKey: qk.pipelines, queryFn: api.listPipelines });
}

export function useRuns(): UseQueryResult<PipelineRun[]> {
  return useQuery({ queryKey: qk.runs, queryFn: api.listRuns });
}

/** Polls while the run is non-terminal; backs off once it reaches a terminal state. */
export function useRun(id: string): UseQueryResult<PipelineRun> {
  return useQuery({
    queryKey: qk.run(id),
    queryFn: () => api.getRun(id),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running' || status === 'queued' ? 4000 : false;
    },
  });
}

export function useSources(): UseQueryResult<Source[]> {
  return useQuery({ queryKey: qk.sources, queryFn: api.listSources });
}

export function useReports(): UseQueryResult<ReportSummary[]> {
  return useQuery({ queryKey: qk.reports, queryFn: api.listReports });
}

export function useReport(id: string): UseQueryResult<Report> {
  return useQuery({
    queryKey: qk.report(id),
    queryFn: () => api.getReport(id),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      query.state.data?.status === 'generating' ? 3000 : false,
  });
}

export function useStatus(): UseQueryResult<SystemStatus> {
  return useQuery({ queryKey: qk.status, queryFn: api.status });
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
