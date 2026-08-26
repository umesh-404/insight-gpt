/**
 * Wire ↔ UI translation.
 *
 * The FastAPI backend and the UI disagree on several field names and shapes:
 *
 * | concept    | wire                                          | UI                            |
 * | ---------- | --------------------------------------------- | ----------------------------- |
 * | answer sql | `sql: string[]` (batch) / `{sql, dialect}` (SSE) | `sql: string[]`             |
 * | table      | `{title|name, columns: string[], rows}`       | `{name, columns: ColumnSpec[]}`|
 * | chart      | `{type, x, series:[{name,y}], data_ref}`      | `{kind, x, series:[{y,label}], data}` |
 * | citation   | `{n, doc_id, source_type, title, date, score}`| same, with optional snippet   |
 * | confidence | `"low" \| "medium" \| "high"`                  | same                          |
 *
 * Every `from*` function is total: it accepts `unknown` and always returns a
 * renderable value, so a backend change degrades a field instead of white-
 * screening a page.
 */
import {
  emptyEnvelope,
  isRole,
  type AnswerEnvelope,
  type Cell,
  type ChartKind,
  type ChartSpec,
  type Citation,
  type ColumnDtype,
  type ColumnSpec,
  type Confidence,
  type Conversation,
  type CorrectionAttempt,
  type ConversationTurn,
  type MetricFormat,
  type MetricResult,
  type MetricUnit,
  type MetricsCatalog,
  type Report,
  type ReportBlock,
  type Role,
  type Route,
  type ServiceHealth,
  type ServiceStatus,
  type StatusFact,
  type SystemStatus,
  type TableBlock,
  type TimeGrain,
  type User,
} from './types';

/* -------------------------------------------------------------------------- */
/* Tiny structural helpers                                                    */
/* -------------------------------------------------------------------------- */

type Json = Record<string, unknown>;

function obj(value: unknown): Json {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Json)
    : {};
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return fallback;
}

function num(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function cell(value: unknown): Cell {
  if (value == null) return null;
  if (typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  return String(value);
}

/**
 * Accepts a bare string, an already-shaped `{sql}` object, or arrays of either.
 * A single string may hold several statements joined by `";\n\n"` (the SSE
 * form), so split those back apart.
 */
function sqlList(value: unknown): string[] {
  if (typeof value === 'string') {
    return value
      .split(/;\s*\n\s*\n/)
      .map((statement) => statement.trim().replace(/;$/, ''))
      .filter(Boolean);
  }
  if (Array.isArray(value)) return value.flatMap(sqlList);
  const inner = obj(value).sql;
  return inner === undefined ? [] : sqlList(inner);
}

/* -------------------------------------------------------------------------- */
/* Scalars                                                                    */
/* -------------------------------------------------------------------------- */

const DTYPE_ALIASES: Record<string, ColumnDtype> = {
  string: 'string',
  str: 'string',
  text: 'string',
  varchar: 'string',
  number: 'number',
  int: 'number',
  integer: 'number',
  bigint: 'number',
  float: 'number',
  double: 'number',
  decimal: 'number',
  numeric: 'number',
  currency: 'currency',
  money: 'currency',
  ratio: 'ratio',
  percent: 'ratio',
  percentage: 'ratio',
  date: 'date',
  datetime: 'date',
  timestamp: 'date',
  bool: 'boolean',
  boolean: 'boolean',
};

export function toDtype(value: unknown): ColumnDtype {
  return DTYPE_ALIASES[str(value).toLowerCase()] ?? 'string';
}

const ROUTES: Route[] = [
  'structured',
  'unstructured',
  'hybrid',
  'clarify',
  'abstain',
];

export function toRoute(value: unknown): Route | undefined {
  const raw = str(value).toLowerCase();
  return ROUTES.find((r) => r === raw);
}

/**
 * Confidence is qualitative on the wire but was numeric in an older contract;
 * accept both so a rollback does not blank the badge.
 */
export function toConfidence(value: unknown): Confidence | undefined {
  const raw = str(value).toLowerCase();
  if (raw === 'low' || raw === 'medium' || raw === 'high') return raw;
  const n = num(value);
  if (n == null) return undefined;
  if (n >= 0.75) return 'high';
  if (n >= 0.4) return 'medium';
  return 'low';
}

const CHART_KINDS: ChartKind[] = ['line', 'bar', 'area', 'pie', 'scatter', 'table'];

function toChartKind(value: unknown): ChartKind {
  const raw = str(value).toLowerCase();
  return CHART_KINDS.find((k) => k === raw) ?? 'bar';
}

/* -------------------------------------------------------------------------- */
/* Auth                                                                       */
/* -------------------------------------------------------------------------- */

export function fromUser(raw: unknown): User {
  const o = obj(raw);
  const role: Role = isRole(o.role) ? o.role : 'viewer';
  return {
    id: str(o.id, 'unknown'),
    email: str(o.email),
    name: typeof o.name === 'string' ? o.name : null,
    role,
  };
}

/* -------------------------------------------------------------------------- */
/* Tables                                                                     */
/* -------------------------------------------------------------------------- */

/** Guess a column dtype from its values when the backend sends bare names. */
function inferDtype(rows: Cell[][], index: number): ColumnDtype {
  let sawNumber = false;
  for (const row of rows) {
    const value = row[index];
    if (value == null) continue;
    if (typeof value === 'number') {
      sawNumber = true;
      continue;
    }
    if (typeof value === 'boolean') return 'boolean';
    return 'string';
  }
  return sawNumber ? 'number' : 'string';
}

export function fromTable(raw: unknown): TableBlock {
  const o = obj(raw);
  const rows: Cell[][] = arr(o.rows).map((row) => arr(row).map(cell));
  const columns: ColumnSpec[] = arr(o.columns).map((col, i) => {
    if (typeof col === 'string') {
      return { name: col, dtype: inferDtype(rows, i) };
    }
    const c = obj(col);
    return {
      name: str(c.name, `col_${i + 1}`),
      dtype: c.dtype === undefined ? inferDtype(rows, i) : toDtype(c.dtype),
    };
  });
  return {
    name: str(o.name) || str(o.title),
    columns,
    rows,
  };
}

/** Row-major table → keyed records, the form Recharts consumes. */
export function tableToRecords(table: TableBlock): Array<Record<string, Cell>> {
  return table.rows.map((row) => {
    const record: Record<string, Cell> = {};
    table.columns.forEach((col, i) => {
      record[col.name] = row[i] ?? null;
    });
    return record;
  });
}

/* -------------------------------------------------------------------------- */
/* Charts                                                                     */
/* -------------------------------------------------------------------------- */

/** `"tables[2]"` → 2. Anything else → 0, matching the backend default. */
function parseDataRef(ref: string): number {
  const match = /\[(\d+)\]/.exec(ref);
  const index = match ? Number(match[1]) : 0;
  return Number.isFinite(index) && index >= 0 ? index : 0;
}

/**
 * Normalize a wire chart. The backend points at a table via `data_ref` rather
 * than inlining rows, so the envelope's tables must be resolved here — without
 * this the renderer always shows "No data to visualize".
 */
export function fromChart(raw: unknown, tables: TableBlock[] = []): ChartSpec | null {
  if (raw == null) return null;
  const o = obj(raw);
  // Some payloads nest the spec one level deeper as { chart_spec: {...} }.
  if (o.chart_spec !== undefined && o.type === undefined && o.kind === undefined) {
    return fromChart(o.chart_spec, tables);
  }

  const series = arr(o.series)
    .map((s) => {
      const item = obj(s);
      const y = str(item.y) || str(item.name);
      if (!y) return null;
      return { y, label: str(item.label) || str(item.name) || y };
    })
    .filter((s): s is { y: string; label: string } => s !== null);

  const kind = toChartKind(o.type ?? o.kind);
  const inlineData = arr(o.data);
  const source = tables[parseDataRef(str(o.data_ref, 'tables[0]'))];
  const data: Array<Record<string, Cell>> = inlineData.length
    ? inlineData.map((row) => {
        const record: Record<string, Cell> = {};
        for (const [k, v] of Object.entries(obj(row))) record[k] = cell(v);
        return record;
      })
    : source
      ? tableToRecords(source)
      : [];

  // With no series the renderer has nothing to plot; fall back to the first
  // numeric column of the resolved table rather than rendering an empty axis.
  const resolvedSeries =
    series.length > 0
      ? series
      : source
        ? source.columns
            .filter((c) => c.dtype === 'number' || c.dtype === 'currency' || c.dtype === 'ratio')
            .map((c) => ({ y: c.name, label: c.name }))
        : [];

  if (!resolvedSeries.length) return null;

  const x = str(o.x) || source?.columns.find((c) => c.dtype === 'string')?.name || null;
  const unitColumn = source?.columns.find((c) => c.name === resolvedSeries[0]?.y);

  return {
    kind,
    title: typeof o.title === 'string' ? o.title : null,
    x,
    series: resolvedSeries,
    options: {
      ...obj(o.options),
      ...(unitColumn && unitColumn.dtype !== 'number'
        ? { yFormat: unitColumn.dtype }
        : {}),
      ...(kind === 'pie' && x ? { nameKey: x, valueKey: resolvedSeries[0]?.y } : {}),
    },
    data,
  };
}

/* -------------------------------------------------------------------------- */
/* Citations                                                                  */
/* -------------------------------------------------------------------------- */

export function fromCitation(raw: unknown, index: number): Citation {
  const o = obj(raw);
  return {
    n: num(o.n) ?? index + 1,
    doc_id: str(o.doc_id) || str(o.id) || `doc_${index + 1}`,
    source_type: str(o.source_type) || str(o.source) || 'document',
    title: str(o.title) || str(o.doc_id) || 'Untitled document',
    date: typeof o.date === 'string' ? o.date : null,
    score: num(o.score),
    snippet: typeof o.snippet === 'string' ? o.snippet : null,
    uri: typeof o.uri === 'string' ? o.uri : null,
  };
}

export function fromCitations(raw: unknown): Citation[] {
  return arr(raw).map(fromCitation);
}

/* -------------------------------------------------------------------------- */
/* Answer envelope                                                            */
/* -------------------------------------------------------------------------- */

/**
 * Self-correction record. `resolution` is the load-bearing field — it decides
 * whether the UI says "recovered" or "gave up" — so anything unrecognised is
 * treated as `gave_up` rather than silently claiming a successful correction.
 */
export function fromCorrectionAttempt(
  raw: unknown,
  index: number,
): CorrectionAttempt {
  const o = obj(raw);
  return {
    attempt: num(o.attempt) ?? index + 1,
    stage: str(o.stage) || 'selection',
    selection:
      o.selection && typeof o.selection === 'object' && !Array.isArray(o.selection)
        ? (o.selection as Record<string, unknown>)
        : null,
    error: str(o.error) || 'The governed selection was rejected.',
    resolution: str(o.resolution) === 'corrected' ? 'corrected' : 'gave_up',
  };
}

export function fromCorrectionAttempts(raw: unknown): CorrectionAttempt[] {
  return arr(raw).map(fromCorrectionAttempt);
}

export function fromEnvelope(raw: unknown): AnswerEnvelope {
  const o = obj(raw);
  const tables = arr(o.tables).map(fromTable);
  const route = toRoute(o.route);
  return {
    answer: str(o.answer),
    sql: sqlList(o.sql),
    dialect: typeof o.dialect === 'string' ? o.dialect : undefined,
    tables,
    citations: fromCitations(o.citations),
    chart_spec: fromChart(o.chart ?? o.chart_spec, tables),
    caveats: arr(o.caveats).map((c) => str(c)).filter(Boolean),
    route,
    confidence: toConfidence(o.confidence),
    clarifying_question:
      typeof o.clarifying_question === 'string' ? o.clarifying_question : null,
    attempts: fromCorrectionAttempts(o.attempts),
    // Trust the explicit flag, but a rollback that only sets `route` must still
    // render as an abstention rather than as a normal (empty) answer.
    abstained: o.abstained === true || route === 'abstain',
    abstain_reason:
      typeof o.abstain_reason === 'string' ? o.abstain_reason : null,
    suggestions: arr(o.suggestions).map((s) => str(s)).filter(Boolean),
  };
}

/* -------------------------------------------------------------------------- */
/* Conversations                                                              */
/* -------------------------------------------------------------------------- */

/**
 * `GET /conversations/{id}` returns a flat message log. Fold it into
 * question/answer turns: each assistant message closes the turn opened by the
 * user message before it. A trailing user message with no reply still renders.
 */
export function fromConversation(raw: unknown, fallbackId: string): Conversation {
  const o = obj(raw);

  // The API sends a derived `turns` array alongside the raw message log.
  // Prefer it when present; fall back to folding `messages` ourselves.
  if (Array.isArray(o.turns) && o.turns.length) {
    return {
      id: str(o.id, fallbackId),
      title: str(o.title) || 'Untitled conversation',
      created_at: str(o.created_at) || undefined,
      turns: o.turns.map((t, i) => {
        const turn = obj(t);
        const envelope = turn.envelope
          ? fromEnvelope(turn.envelope)
          : { ...emptyEnvelope(), answer: str(turn.answer) };
        if (!envelope.answer) envelope.answer = str(turn.answer);
        return {
          id: str(turn.id, `m_${i}`),
          question: str(turn.question),
          envelope,
          created_at: str(turn.created_at),
          feedback:
            turn.feedback === 'up' || turn.feedback === 'down' ? turn.feedback : null,
        };
      }),
    };
  }

  const turns: ConversationTurn[] = [];
  let pendingQuestion: { text: string; created_at: string } | null = null;

  for (const message of arr(o.messages)) {
    const m = obj(message);
    const role = str(m.role).toLowerCase();
    const content = str(m.content);
    const createdAt = str(m.created_at);
    if (role === 'user') {
      if (pendingQuestion) {
        turns.push({
          id: `q_${turns.length}`,
          question: pendingQuestion.text,
          envelope: emptyEnvelope(),
          created_at: pendingQuestion.created_at,
          feedback: null,
        });
      }
      pendingQuestion = { text: content, created_at: createdAt };
      continue;
    }
    if (role !== 'assistant') continue;

    const envelope = m.envelope
      ? fromEnvelope(m.envelope)
      : { ...emptyEnvelope(), answer: content };
    // A stored envelope may carry an empty answer string; prefer the message body.
    if (!envelope.answer) envelope.answer = content;

    turns.push({
      id: str(m.id, `m_${turns.length}`),
      question: pendingQuestion?.text ?? '',
      envelope,
      created_at: createdAt || pendingQuestion?.created_at || '',
      feedback: null,
    });
    pendingQuestion = null;
  }

  if (pendingQuestion) {
    turns.push({
      id: `q_${turns.length}`,
      question: pendingQuestion.text,
      envelope: emptyEnvelope(),
      created_at: pendingQuestion.created_at,
      feedback: null,
    });
  }

  return {
    id: str(o.id, fallbackId),
    title: str(o.title) || 'Untitled conversation',
    created_at: str(o.created_at) || undefined,
    turns,
  };
}

/* -------------------------------------------------------------------------- */
/* Metrics                                                                    */
/* -------------------------------------------------------------------------- */

const UNITS = ['currency', 'count', 'ratio', 'duration'] as const;

const FORMATS = ['currency', 'percent', 'integer', 'decimal'] as const;

function toUnit(value: unknown): MetricUnit {
  const raw = str(value).toLowerCase();
  return (UNITS as readonly string[]).includes(raw) ? (raw as MetricUnit) : 'count';
}

function toFormat(value: unknown): MetricFormat | null {
  const raw = str(value).toLowerCase();
  return (FORMATS as readonly string[]).includes(raw) ? (raw as MetricFormat) : null;
}

function toGrain(value: unknown): TimeGrain | null {
  const raw = str(value).toLowerCase();
  return (['day', 'week', 'month', 'quarter', 'year'] as readonly string[]).includes(raw)
    ? (raw as TimeGrain)
    : null;
}

export function fromMetricsCatalog(raw: unknown): MetricsCatalog {
  // Older builds returned a bare array of metric definitions.
  const o = Array.isArray(raw) ? { metrics: raw, dimensions: [] } : obj(raw);
  return {
    metrics: arr(o.metrics).map((m) => {
      const item = obj(m);
      return {
        key: str(item.key),
        label: str(item.label) || str(item.key),
        description: str(item.description),
        unit: toUnit(item.unit),
        format: toFormat(item.format),
        grain: arr(item.grain).map((g) => str(g)),
        default_agg: 'sum',
        // Ratios and averages must never be summed across a breakdown.
        additive: item.additive === undefined ? undefined : item.additive === true,
        aliases: arr(item.aliases).map((a) => str(a)).filter(Boolean),
      };
    }),
    dimensions: arr(o.dimensions).map((d) => {
      const item = obj(d);
      return {
        key: str(item.key),
        label: str(item.label) || undefined,
        grains: arr(item.grains).map((g) => str(g)),
        default_grain: toGrain(item.default_grain),
        is_date: item.is_date === true,
      };
    }),
    time_grains: arr(o.time_grains)
      .map(toGrain)
      .filter((g): g is TimeGrain => g !== null),
    limits: Object.fromEntries(
      Object.entries(obj(o.limits)).flatMap(([k, v]) => {
        const parsed = num(v);
        return parsed == null ? [] : [[k, parsed] as const];
      }),
    ),
  };
}

export function fromMetricResult(raw: unknown): MetricResult {
  const o = obj(raw);
  const rows: Cell[][] = arr(o.rows).map((row) => arr(row).map(cell));
  const columns: ColumnSpec[] = arr(o.columns).map((col, i) => {
    const c = obj(col);
    return { name: str(c.name, `col_${i + 1}`), dtype: toDtype(c.dtype) };
  });
  const meta = obj(o.meta);
  return {
    columns,
    rows,
    records: Array.isArray(o.records)
      ? o.records.map((record) => {
          const out: Record<string, Cell> = {};
          for (const [k, v] of Object.entries(obj(record))) out[k] = cell(v);
          return out;
        })
      : undefined,
    meta: Object.keys(meta).length
      ? {
          metric: str(meta.metric) || undefined,
          label: str(meta.label) || undefined,
          unit: meta.unit === undefined ? undefined : toUnit(meta.unit),
          format: toFormat(meta.format),
          dimensions: arr(meta.dimensions).map((d) => str(d)),
          grain: toGrain(meta.grain),
          order: str(meta.order) || null,
          limit: num(meta.limit) ?? undefined,
        }
      : undefined,
    sql: str(o.sql),
    row_count: num(o.row_count) ?? rows.length,
    truncated: o.truncated === true,
  };
}

/* -------------------------------------------------------------------------- */
/* Reports                                                                    */
/* -------------------------------------------------------------------------- */

const REPORT_STATUSES = ['generating', 'ready', 'failed'] as const;

function toReportStatus(value: unknown): Report['status'] {
  const raw = str(value).toLowerCase();
  return (REPORT_STATUSES as readonly string[]).includes(raw)
    ? (raw as Report['status'])
    : 'generating';
}

export function fromReport(raw: unknown, fallbackId: string): Report {
  const o = obj(raw);
  const period = obj(o.period);
  const blocks: ReportBlock[] = arr(o.blocks).map((b, i) => {
    const block = obj(b);
    const tables = arr(block.tables).map(fromTable);
    return {
      // The API omits block ids — synthesize one that is stable across polls.
      id: str(block.id) || `${str(o.id, fallbackId)}_b${i}`,
      heading: str(block.heading) || `Section ${i + 1}`,
      prose: str(block.prose),
      chart_spec: fromChart(block.chart_spec ?? block.chart, tables),
      tables,
      citations: fromCitations(block.citations),
    };
  });
  return {
    id: str(o.id, fallbackId),
    status: toReportStatus(o.status),
    title: str(o.title) || 'Untitled report',
    period: {
      grain: typeof period.grain === 'string'
        ? (period.grain as Report['period']['grain'])
        : null,
      start: str(period.start),
      end: str(period.end),
    },
    blocks,
    created_at: str(o.created_at),
    error: typeof o.error === 'string' ? o.error : null,
  };
}

export function reportSummary(report: Report): {
  id: string;
  title: string;
  status: Report['status'];
  created_at: string;
} {
  return {
    id: report.id,
    title: report.title,
    status: report.status,
    created_at: report.created_at,
  };
}

/* -------------------------------------------------------------------------- */
/* System status                                                              */
/* -------------------------------------------------------------------------- */

const SERVICE_STATUSES: ServiceStatus[] = [
  'ok',
  'degraded',
  'error',
  'untested',
  'fixture',
];

export function toServiceStatus(value: unknown): ServiceStatus {
  const raw = str(value).toLowerCase();
  return SERVICE_STATUSES.find((s) => s === raw) ?? 'untested';
}

/** Turn an arbitrary `{key: scalar}` stats block into labelled display facts. */
function toFacts(raw: unknown): StatusFact[] {
  return Object.entries(obj(raw))
    .filter(([, value]) => value != null && typeof value !== 'object')
    .map(([key, value]) => ({
      label: key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
      value:
        typeof value === 'number'
          ? new Intl.NumberFormat('en-US').format(value)
          : String(value),
    }));
}

export function fromStatus(raw: unknown): SystemStatus {
  const o = obj(raw);
  const llm = obj(o.llm);
  const services: Record<string, ServiceHealth> = {};
  for (const [name, value] of Object.entries(obj(o.services))) {
    const svc = obj(value);
    services[name] = {
      status: toServiceStatus(svc.status),
      latency_ms: num(svc.latency_ms),
      detail: typeof svc.detail === 'string' ? svc.detail : null,
    };
  }
  return {
    status: str(o.status) === 'degraded' ? 'degraded' : 'ok',
    version: str(o.version) || undefined,
    uptime_s: num(o.uptime_s) ?? undefined,
    services,
    warehouse: toFacts(o.warehouse),
    index: toFacts(o.index),
    llm: {
      provider: str(llm.provider) || 'unknown',
      model: typeof llm.model === 'string' ? llm.model : null,
      reachable: llm.reachable === true,
    },
  };
}
