/**
 * Types mirroring the InsightGPT API contract.
 *
 * There are two layers here:
 *  - **Wire types** (lib/wire.ts) describe exactly what the FastAPI backend
 *    serializes. They are deliberately permissive where the backend is.
 *  - **UI types** (this file) are the normalized shapes every component renders.
 *
 * `lib/wire.ts` owns the translation between the two, so a backend field rename
 * is a one-file change instead of a UI-wide crash.
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

export function isRole(value: unknown): value is Role {
  return value === 'admin' || value === 'analyst' || value === 'viewer';
}

export function roleAtLeast(role: Role, minimum: Role): boolean {
  return ROLE_ORDER[role] >= ROLE_ORDER[minimum];
}

export interface User {
  id: string;
  email: string;
  /** The API always sends a name, but never assume it: see `displayName()`. */
  name?: string | null;
  role: Role;
}

/** Best-effort display name that can never throw on a partial user record. */
export function displayName(user: Pick<User, 'name' | 'email'> | null | undefined): string {
  if (!user) return 'Unknown user';
  const name = user.name?.trim();
  if (name) return name;
  const email = user.email?.trim();
  if (email) return email.split('@')[0] || email;
  return 'Unknown user';
}

/** `POST /auth/login` — access + refresh tokens (refresh also lands in a cookie). */
export interface TokenPair {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  expires_in: number;
}

/** `POST /auth/refresh` — access token only; the refresh cookie stays put. */
export interface AccessToken {
  access_token: string;
  token_type?: string;
  expires_in: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

/* ----------------------------------------------------------------------------
 * Answer envelope
 * ------------------------------------------------------------------------- */

export interface Citation {
  /** 1-based marker used inline in the answer text as `[n]`. */
  n: number;
  doc_id: string;
  source_type: string;
  title: string;
  date?: string | null;
  /** Rerank relevance, 0..1. Absent for some retrievers. */
  score?: number | null;
  snippet?: string | null;
  uri?: string | null;
}

export type ColumnDtype =
  | 'string'
  | 'number'
  | 'currency'
  | 'date'
  | 'ratio'
  | 'boolean';

export interface ColumnSpec {
  name: string;
  dtype: ColumnDtype;
}

export type Cell = string | number | boolean | null;

export interface TableBlock {
  name: string;
  columns: ColumnSpec[];
  rows: Cell[][];
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
  /**
   * Rows to plot; keys match `x` / `series[].y`. The backend sends `data_ref`
   * (e.g. `tables[0]`) instead — lib/wire.ts resolves it against the envelope's
   * tables so the renderer only ever deals with concrete rows.
   */
  data?: Array<Record<string, Cell>>;
}

export type Route =
  | 'structured'
  | 'unstructured'
  | 'hybrid'
  | 'clarify'
  /** The engine understood the question but refused to answer it (see below). */
  | 'abstain';

/** The backend grades confidence qualitatively, not as a probability. */
export type Confidence = 'low' | 'medium' | 'high';

/**
 * One iteration of the engine's bounded self-correction loop.
 *
 * The structured path retries a *governed selection* (never free SQL) when it
 * fails or comes back clearly wrong. Each attempt records what was tried, why
 * it was rejected, and whether the loop recovered or gave up.
 */
export interface CorrectionAttempt {
  attempt: number;
  /** Which selection failed, e.g. `"scalar:revenue"`, `"grouped:region"`. */
  stage: string;
  /** Compact view of the governed selection that was tried. */
  selection?: Record<string, unknown> | null;
  error: string;
  resolution: 'corrected' | 'gave_up';
}

export interface AnswerEnvelope {
  answer: string;
  /** Zero or more governed statements. The backend may run several per answer. */
  sql: string[];
  dialect?: string;
  tables: TableBlock[];
  citations: Citation[];
  chart_spec?: ChartSpec | null;
  caveats: string[];
  route?: Route;
  confidence?: Confidence;
  /** Set when the router needs more information before it can answer. */
  clarifying_question?: string | null;
  /** Bounded record of any governed-selection retries the structured path ran. */
  attempts?: CorrectionAttempt[];
  /**
   * True when the engine refused to answer rather than fabricate a number.
   * This is an *honest outcome*, not a failure: `route` is `"abstain"`, no
   * figure is emitted, and `suggestions` points at the closest governed metric.
   */
  abstained?: boolean;
  abstain_reason?: string | null;
  suggestions?: string[];
}

export function emptyEnvelope(): AnswerEnvelope {
  return {
    answer: '',
    sql: [],
    tables: [],
    citations: [],
    chart_spec: null,
    caveats: [],
    attempts: [],
    abstained: false,
    abstain_reason: null,
    suggestions: [],
  };
}

/**
 * True when a *well-formed* governed query executed and matched no rows.
 *
 * The backend does not flag this with a boolean — it returns an ordinary
 * envelope whose distinguishing signal is the caveat it always attaches
 * ("No rows matched a well-formed, governed query."). Detecting it here keeps
 * the UI from rendering an honest empty result as either a failure or a zero.
 */
export function isNoDataEnvelope(envelope: AnswerEnvelope): boolean {
  if (envelope.abstained) return false;
  const flagged = (envelope.caveats ?? []).some((c) =>
    /no rows matched/i.test(c),
  );
  if (!flagged) return false;
  // Corroborate with the tables: a no-data envelope carries the executed query
  // and its (empty) result, never populated rows.
  return (envelope.tables ?? []).every((t) => t.rows.length === 0);
}

/**
 * The metric key inside a governed suggestion, when the backend named one.
 *
 * Suggestions arrive as prose ("Try the governed metric 'return_rate'."), so
 * the quoted key is extracted for a clickable chip; a suggestion with no quoted
 * key still renders, using its own text as the follow-up question.
 */
export function suggestionMetricKey(suggestion: string): string | null {
  const match = /['"`]([a-z0-9_]+)['"`]/i.exec(suggestion);
  return match?.[1] ?? null;
}

/* ----------------------------------------------------------------------------
 * SSE streaming
 * ------------------------------------------------------------------------- */

export interface SseMetaEvent {
  conversation_id: string;
  message_id: string;
}

export interface SseUsage {
  latency_ms?: number;
  confidence?: Confidence;
  tokens?: { in: number; out: number };
}

export type AskStreamEvent =
  | { type: 'meta'; data: SseMetaEvent }
  | { type: 'token'; data: { text: string } }
  | { type: 'sql'; data: { sql: string; dialect?: string } }
  | { type: 'tables'; data: TableBlock }
  | { type: 'citations'; data: { items: Citation[] } }
  /** `raw` is the unnormalized payload, kept so `data_ref` can be resolved
   *  against tables that may still be arriving. */
  | { type: 'chart'; data: { chart_spec: ChartSpec | null; raw: unknown } }
  | { type: 'caveats'; data: { items: string[] } }
  | {
      type: 'route';
      data: { route: Route; confidence?: Confidence; abstained?: boolean };
    }
  | { type: 'clarify'; data: { question: string } }
  /** The engine declined to answer; carries the reason + governed suggestions. */
  | { type: 'abstain'; data: { reason: string; suggestions: string[] } }
  /** Bounded self-correction record, emitted only when a retry happened. */
  | { type: 'corrections'; data: { items: CorrectionAttempt[] } }
  | { type: 'done'; data: { message_id?: string; usage?: SseUsage } }
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

/** A question plus the answer it produced — the unit the UI renders. */
export interface ConversationTurn {
  id: string; // assistant message id
  question: string;
  envelope: AnswerEnvelope;
  created_at: string;
  feedback?: 'up' | 'down' | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at?: string;
  turns: ConversationTurn[];
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit?: number;
  offset?: number;
}

/* ----------------------------------------------------------------------------
 * Dashboards & governed metrics
 * ------------------------------------------------------------------------- */

export type MetricUnit = 'currency' | 'count' | 'ratio' | 'duration';

/** Server-declared render hint; takes precedence over `unit` when present. */
export type MetricFormat = 'currency' | 'percent' | 'integer' | 'decimal';

export interface MetricDef {
  key: string;
  label: string;
  description: string;
  unit: MetricUnit;
  format?: MetricFormat | null;
  grain: string[];
  default_agg: 'sum' | 'avg' | 'count' | 'ratio';
  /** False for ratios/averages, which must not be summed across rows. */
  additive?: boolean;
  aliases?: string[];
}

export interface DimensionDef {
  key: string;
  label?: string;
  grains: string[];
  default_grain?: TimeGrain | null;
  is_date: boolean;
}

export interface MetricsCatalog {
  metrics: MetricDef[];
  dimensions: DimensionDef[];
  time_grains?: TimeGrain[];
  limits?: Record<string, number>;
}

export type TimeGrain = 'day' | 'week' | 'month' | 'quarter' | 'year';

export interface TimeRange {
  grain?: TimeGrain | null;
  start: string;
  end: string;
}

export type FilterOp = 'eq' | 'in' | 'between' | 'gte' | 'lte' | 'ne';

/** Governed filter predicate (list form of the `/metrics/query` contract). */
export interface MetricFilter {
  dimension: string;
  op: FilterOp;
  values: Array<string | number | boolean>;
}

export interface MetricQuery {
  metric: string;
  dimensions?: string[];
  filters?: MetricFilter[];
  time_range?: TimeRange | null;
  time_grain?: TimeGrain | null;
  /** Sort by the metric column rather than the dimension. */
  order_by_metric?: 'asc' | 'desc' | null;
  limit?: number;
}

/** Echo of how the server resolved the query — the source of render hints. */
export interface MetricResultMeta {
  metric?: string;
  label?: string;
  unit?: MetricUnit;
  format?: MetricFormat | null;
  dimensions?: string[];
  grain?: TimeGrain | null;
  order?: string | null;
  limit?: number;
}

export interface MetricResult {
  columns: ColumnSpec[];
  rows: Cell[][];
  /** Row-major `rows` pre-zipped with `columns`; convenient for charts. */
  records?: Array<Record<string, Cell>>;
  meta?: MetricResultMeta;
  sql: string;
  row_count: number;
  truncated: boolean;
}

/** Derived client-side from a current + previous period query (see lib/metrics.ts). */
export interface MetricSummary {
  value: number;
  unit: MetricUnit;
  delta_pct?: number; // period over period, e.g. -0.12
  previous?: number;
}

/* ----------------------------------------------------------------------------
 * Pipelines
 * ------------------------------------------------------------------------- */

export type RunStatus = 'queued' | 'running' | 'success' | 'failed' | 'partial';
export type ServiceStatus = 'ok' | 'degraded' | 'error' | 'untested' | 'fixture';

export interface StageRecord {
  name: string;
  rows_in: number;
  rows_out: number;
  ms: number;
  error?: string | null;
}

export interface PipelineRunSummary {
  id: string;
  pipeline?: string;
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
 * Data sources
 * ------------------------------------------------------------------------- */

export const SOURCE_KINDS = [
  'postgres',
  'mysql',
  'csv',
  'excel',
  'documents',
] as const;

export type SourceKind = (typeof SOURCE_KINDS)[number];

export function isSourceKind(value: unknown): value is SourceKind {
  return (SOURCE_KINDS as readonly unknown[]).includes(value);
}

export interface Source {
  id: string;
  name: string;
  /** The API types this as a free string; unknown kinds must still render. */
  kind: string;
  status: 'ok' | 'untested' | 'error';
  last_tested_at?: string | null;
  active?: boolean;
  /**
   * Non-secret "where does this point": the configured path for file kinds,
   * `host:port` for connection kinds. The API never puts credentials here.
   */
  location?: string | null;
  /** Why the source is in its current status — the last probe message. */
  detail?: string | null;
}

export interface SourceConfig {
  name: string;
  kind: SourceKind;
  /** Write-only: accepted on create, never returned by any read. */
  dsn?: string | null;
  options?: Record<string, unknown>;
}

export interface SourceTestResult {
  ok: boolean;
  latency_ms: number;
  tables_seen: number;
  message: string;
  /** What the probe actually verified (`filesystem`, `connect`, `tcp`, …). */
  checked?: string;
  error_code?: string | null;
}

/** Kinds configured by a filesystem path vs. by a connection string. */
export const PATH_SOURCE_KINDS = ['csv', 'excel', 'documents'] as const;
export const DSN_SOURCE_KINDS = ['postgres', 'mysql'] as const;

export function sourceNeedsDsn(kind: SourceKind): boolean {
  return (DSN_SOURCE_KINDS as readonly SourceKind[]).includes(kind);
}

export function sourceNeedsPath(kind: SourceKind): boolean {
  return (PATH_SOURCE_KINDS as readonly SourceKind[]).includes(kind);
}

/* ----------------------------------------------------------------------------
 * Reports
 * ------------------------------------------------------------------------- */

export type ReportSection = 'kpis' | 'sales' | 'inventory' | 'voice_of_customer';
export type ReportFormat = 'markdown' | 'pdf';

export interface ReportRequest {
  title: string;
  period: TimeRange;
  sections: ReportSection[];
  format_hint?: 'executive' | 'detailed';
}

export interface ReportBlock {
  /** The API may omit ids; lib/wire.ts synthesizes stable ones. */
  id: string;
  heading: string;
  prose: string;
  chart_spec?: ChartSpec | null;
  /** Data tables backing the narrative. */
  tables?: TableBlock[];
  citations?: Citation[];
}

export interface Report {
  id: string;
  status: 'generating' | 'ready' | 'failed';
  title: string;
  period: TimeRange;
  blocks: ReportBlock[];
  created_at: string;
  /** Populated when `status === 'failed'`. */
  error?: string | null;
}

export interface ReportSummary {
  id: string;
  title: string;
  status: Report['status'];
  created_at: string;
}

/* ----------------------------------------------------------------------------
 * Proactive insight digest
 * ------------------------------------------------------------------------- */

export type InsightSeverity = 'high' | 'medium' | 'low';
export type InsightDirection = 'up' | 'down';
/** Display hint carried from the governed metric's `format`. */
export type InsightMetricFormat = 'currency' | 'percent' | 'integer' | 'number';

export interface InsightTrendPoint {
  period: string;
  value: number;
}

export interface InsightContribution {
  dimension: string;
  segment: string;
  current: number;
  prior: number;
  delta: number;
  /** Signed share of the total change, in percent. */
  contribution_pct: number;
}

export interface InsightRootCause {
  dimension: string;
  segment: string;
  current: number;
  prior: number;
  delta: number;
  contribution_pct: number;
}

export interface InsightEvidence {
  n: number;
  doc_id: string;
  source_type: string;
  title: string;
  date?: string | null;
  score?: number | null;
  snippet?: string | null;
}

/** One flagged anomaly with its deterministic root cause and evidence. */
export interface Insight {
  id: string;
  metric: string;
  metric_label: string;
  metric_format: InsightMetricFormat;
  grain: string;
  period: string;
  prior_period: string;
  current: number;
  prior: number;
  change_abs: number;
  /** Signed ratio, e.g. -0.114. */
  change_pct: number;
  direction: InsightDirection;
  severity: InsightSeverity;
  z_score?: number | null;
  method: string;
  headline: string;
  root_cause?: InsightRootCause | null;
  contributions: InsightContribution[];
  trend: InsightTrendPoint[];
  evidence: InsightEvidence[];
  created_at: string;
}

export interface InsightPage {
  items: Insight[];
  total: number;
  limit: number;
  offset: number;
  /** Which store answered: "postgres" | "file" | "memory (on-demand)". */
  backend: string;
}

/* ----------------------------------------------------------------------------
 * System status
 * ------------------------------------------------------------------------- */

export interface ServiceHealth {
  status: ServiceStatus;
  latency_ms?: number | null;
  detail?: string | null;
}

/** A labelled counter shown on the settings page; keys vary by deployment. */
export interface StatusFact {
  label: string;
  value: string;
}

export interface SystemStatus {
  status: 'ok' | 'degraded';
  version?: string;
  uptime_s?: number;
  services: Record<string, ServiceHealth>;
  /** Flattened `warehouse` + `index` payloads — the backend's keys differ per mode. */
  warehouse: StatusFact[];
  index: StatusFact[];
  llm: { provider: string; model?: string | null; reachable: boolean };
}

/* ----------------------------------------------------------------------------
 * Forecasting
 * ------------------------------------------------------------------------- */

/** One observed period. Forecast points additionally carry an interval. */
export interface ForecastPoint {
  period: string;
  value: number;
  lower?: number | null;
  upper?: number | null;
}

/** How much weight the answer deserves. `none` means it refused to project. */
export type ForecastConfidence = 'none' | 'low' | 'medium' | 'high';

export interface ForecastResult {
  metric: string;
  metric_label: string;
  format: MetricFormat;
  additive: boolean;
  grain: string;
  horizon: number;
  history: ForecastPoint[];
  /** Empty when the engine declined to forecast — see `caveats`. */
  forecast: ForecastPoint[];
  method: string;
  method_family: string;
  n_history: number;
  interval_level: number;
  confidence: ForecastConfidence;
  low_confidence: boolean;
  caveats: string[];
  headline: string;
}

/** Whether one metric can be forecast at a grain, and if not, why not. */
export interface ForecastCapability {
  metric: string;
  label: string;
  format: MetricFormat;
  additive: boolean;
  grain: string;
  n_history: number;
  forecastable: boolean;
  reason?: string | null;
}

export interface ForecastCapabilityReport {
  grain: string;
  min_history: number;
  method_family: string;
  method: string;
  metrics: ForecastCapability[];
}

/* ----------------------------------------------------------------------------
 * Errors
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
