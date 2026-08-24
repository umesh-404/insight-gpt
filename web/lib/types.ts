/**
 * Types mirroring the InsightGPT API contract (docs/06-api.md).
 * In production these are generated from the backend's OpenAPI schema so drift
 * is a compile error; they are hand-written here to keep the frontend package
 * self-contained and buildable on its own.
 */

/* ----------------------------------------------------------------------------
 * Auth & users
 * ------------------------------------------------------------------------- */

export type Role = 'admin' | 'analyst' | 'viewer';

/** Ordered so `roleAtLeast` can compare capability additively. */
export const ROLE_ORDER: Record<Role, number> = {
  viewer: 0,
  analyst: 1,
  admin: 2,
};

export function roleAtLeast(role: Role, minimum: Role): boolean {
  return ROLE_ORDER[role] >= ROLE_ORDER[minimum];
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

/* ----------------------------------------------------------------------------
 * Answer envelope (docs/06-api.md §7)
 * ------------------------------------------------------------------------- */

export interface Citation {
  id: string;
  title: string;
  source: string;
  snippet: string;
  score: number; // rerank score, 0..1
  uri?: string | null;
}

export type ColumnDtype = 'string' | 'number' | 'currency' | 'date' | 'ratio';

export interface ColumnSpec {
  name: string;
  dtype: ColumnDtype;
}

export interface TableBlock {
  name: string;
  columns: ColumnSpec[];
  rows: Array<Array<string | number | null>>;
}

export type ChartKind = 'line' | 'bar' | 'area' | 'pie' | 'scatter' | 'table';

export interface SeriesSpec {
  y: string; // column key for the series value
  label?: string;
  agg?: 'sum' | 'avg' | 'count' | 'ratio';
}

export interface ChartSpec {
  kind: ChartKind;
  title?: string | null;
  x?: string | null; // column key for the x-axis / category
  series: SeriesSpec[];
  stacked?: boolean;
  /** Free-form render options: axis format, units, sort, value key for pie. */
  options?: {
    unit?: ColumnDtype;
    yFormat?: ColumnDtype;
    valueKey?: string;
    nameKey?: string;
    sort?: 'asc' | 'desc';
    [key: string]: unknown;
  };
  /** Rows to plot. Present in stored/mock envelopes; keys match x/series. */
  data?: Array<Record<string, string | number | null>>;
}

export interface AnswerEnvelope {
  answer: string;
  sql?: string | null;
  dialect?: string;
  tables: TableBlock[];
  citations: Citation[];
  chart_spec?: ChartSpec | null;
  caveats: string[];
  /** Route taken by the insight engine — surfaced as a small badge. */
  route?: 'structured' | 'unstructured' | 'hybrid';
  confidence?: number; // 0..1
}

/* ----------------------------------------------------------------------------
 * SSE streaming (docs/06-api.md §6)
 * ------------------------------------------------------------------------- */

export interface SseMetaEvent {
  conversation_id: string;
  message_id: string;
}

export interface SseUsage {
  latency_ms: number;
  tokens: { in: number; out: number };
}

export type AskStreamEvent =
  | { type: 'meta'; data: SseMetaEvent }
  | { type: 'token'; data: { text: string } }
  | { type: 'sql'; data: { sql: string; dialect: string } }
  | { type: 'tables'; data: TableBlock }
  | { type: 'citations'; data: { items: Citation[] } }
  | { type: 'chart'; data: { chart_spec: ChartSpec } }
  | { type: 'caveats'; data: { items: string[] } }
  | { type: 'route'; data: { route: NonNullable<AnswerEnvelope['route']>; confidence?: number } }
  | { type: 'done'; data: { message_id: string; usage: SseUsage } }
  | { type: 'error'; data: ApiErrorBody };

/* ----------------------------------------------------------------------------
 * Conversations
 * ------------------------------------------------------------------------- */

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationTurn {
  id: string; // message_id
  question: string;
  envelope: AnswerEnvelope;
  created_at: string;
  feedback?: 'up' | 'down' | null;
}

export interface Conversation {
  id: string;
  title: string;
  turns: ConversationTurn[];
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/* ----------------------------------------------------------------------------
 * Dashboards & governed metrics (docs/06-api.md §3.2)
 * ------------------------------------------------------------------------- */

export type MetricUnit = 'currency' | 'count' | 'ratio' | 'duration';

export interface MetricDef {
  key: string;
  label: string;
  description: string;
  unit: MetricUnit;
  grain: string[];
  default_agg: 'sum' | 'avg' | 'count' | 'ratio';
}

export interface TimeRange {
  grain: 'day' | 'week' | 'month' | 'quarter';
  start: string;
  end: string;
}

export interface MetricQuery {
  metric: string;
  dimensions?: string[];
  filters?: Record<string, string | number | boolean>;
  time_range?: TimeRange | null;
  order_by?: string | null;
  limit?: number;
}

export interface MetricResult {
  columns: ColumnSpec[];
  rows: Array<Array<string | number | null>>;
  sql: string;
  row_count: number;
  truncated: boolean;
  /** Denormalized single value + delta for stat tiles (frontend convenience). */
  summary?: {
    value: number;
    unit: MetricUnit;
    delta_pct?: number; // period over period, e.g. -0.12
    previous?: number;
  };
}

/* ----------------------------------------------------------------------------
 * Pipelines (docs/06-api.md §3.4)
 * ------------------------------------------------------------------------- */

export type RunStatus = 'queued' | 'running' | 'success' | 'failed' | 'partial';
export type ServiceStatus = 'ok' | 'degraded' | 'error' | 'untested';

export interface StageRecord {
  name: string;
  rows_in: number;
  rows_out: number;
  ms: number;
  error?: string | null;
}

export interface PipelineRunSummary {
  id: string;
  status: RunStatus;
  started_at: string;
  finished_at?: string | null;
}

export interface PipelineRun {
  id: string;
  pipeline: string;
  status: RunStatus;
  trigger: 'manual' | 'scheduled';
  started_at: string;
  finished_at?: string | null;
  stages: StageRecord[];
  row_counts: Record<string, number>;
  error?: string | null;
}

export interface Pipeline {
  name: string;
  description: string;
  schedule?: string | null; // cron or null
  last_run?: PipelineRunSummary | null;
}

/* ----------------------------------------------------------------------------
 * Data sources (docs/06-api.md §3.3)
 * ------------------------------------------------------------------------- */

export type SourceKind =
  | 'postgres'
  | 'mysql'
  | 'csv'
  | 'excel'
  | 'documents';

export interface Source {
  id: string;
  name: string;
  kind: SourceKind;
  status: 'ok' | 'untested' | 'error';
  last_tested_at?: string | null;
}

/* ----------------------------------------------------------------------------
 * Reports (docs/06-api.md §3.5)
 * ------------------------------------------------------------------------- */

export type ReportSection = 'kpis' | 'sales' | 'inventory' | 'voice_of_customer';

export interface ReportRequest {
  title: string;
  period: TimeRange;
  sections: ReportSection[];
  format_hint?: 'executive' | 'detailed';
}

export interface ReportBlock {
  id: string;
  heading: string;
  prose: string;
  chart_spec?: ChartSpec | null;
  citations?: Citation[];
}

export interface Report {
  id: string;
  status: 'generating' | 'ready' | 'failed';
  title: string;
  period: TimeRange;
  blocks: ReportBlock[];
  created_at: string;
}

export interface ReportSummary {
  id: string;
  title: string;
  status: Report['status'];
  created_at: string;
}

/* ----------------------------------------------------------------------------
 * System status (docs/06-api.md §3.6)
 * ------------------------------------------------------------------------- */

export interface ServiceHealth {
  status: ServiceStatus;
  latency_ms?: number;
  detail?: string;
}

export interface SystemStatus {
  status: 'ok' | 'degraded';
  services: Record<string, ServiceHealth>;
  warehouse: { marts_rows: number; last_dbt_run?: string | null };
  index: { collection_size: number; last_index?: string | null };
  llm: { provider: string; model: string; reachable: boolean };
}

/* ----------------------------------------------------------------------------
 * Errors (docs/06-api.md §5)
 * ------------------------------------------------------------------------- */

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id?: string;
  details?: Record<string, unknown> | null;
}

export class ApiError extends Error {
  code: string;
  status: number;
  requestId?: string;
  details?: Record<string, unknown> | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id;
    this.details = body.details ?? null;
  }
}
