/**
 * Helpers for turning governed `/metrics/query` results into the shapes the
 * dashboard renders: KPI summaries, trend specs, and ranked tables.
 *
 * The API returns raw columns/rows only — there is no server-side `summary`
 * block — so period-over-period deltas are derived here from two queries
 * (current window + the immediately preceding window of equal length).
 */
import type {
  Cell,
  ChartSpec,
  ColumnDtype,
  MetricResult,
  MetricSummary,
  MetricUnit,
  TimeGrain,
  TimeRange,
} from './types';

/* -------------------------------------------------------------------------- */
/* Date ranges                                                                */
/* -------------------------------------------------------------------------- */

export type RangeKey = '7d' | '30d' | 'quarter' | 'ytd' | '12m';

export const RANGE_OPTIONS: Array<{ value: RangeKey; label: string }> = [
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: 'quarter', label: 'This quarter' },
  { value: 'ytd', label: 'Year to date' },
  { value: '12m', label: 'Last 12 months' },
];

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date.getTime());
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

/** Inclusive [start, end] window for a range key, relative to `today`. */
export function resolveRange(key: RangeKey, today = new Date()): TimeRange {
  const end = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()),
  );
  switch (key) {
    case '7d':
      return { start: iso(addDays(end, -6)), end: iso(end), grain: 'day' };
    case '30d':
      return { start: iso(addDays(end, -29)), end: iso(end), grain: 'day' };
    case 'quarter': {
      const quarterStartMonth = Math.floor(end.getUTCMonth() / 3) * 3;
      return {
        start: iso(new Date(Date.UTC(end.getUTCFullYear(), quarterStartMonth, 1))),
        end: iso(end),
        grain: 'month',
      };
    }
    case '12m': {
      const start = new Date(end.getTime());
      start.setUTCFullYear(start.getUTCFullYear() - 1);
      return { start: iso(addDays(start, 1)), end: iso(end), grain: 'month' };
    }
    case 'ytd':
    default:
      return {
        start: iso(new Date(Date.UTC(end.getUTCFullYear(), 0, 1))),
        end: iso(end),
        grain: 'month',
      };
  }
}

/** The window of equal length immediately before `range` — the delta baseline. */
export function previousRange(range: TimeRange): TimeRange {
  const start = new Date(`${range.start}T00:00:00Z`);
  const end = new Date(`${range.end}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return range;
  const days = Math.max(1, Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1);
  return {
    grain: range.grain,
    start: iso(addDays(start, -days)),
    end: iso(addDays(end, -days)),
  };
}

export function grainFor(range: TimeRange): TimeGrain {
  return (range.grain as TimeGrain | null) ?? 'month';
}

/**
 * Which direction of movement is an improvement for a metric.
 *
 * The API tells us *what* moved and by how much, never whether that is good
 * news — so this is a display convention, applied only to metric keys whose
 * meaning is unambiguous. Anything unrecognised returns `'neutral'`, and the UI
 * then shows the direction without claiming a verdict. Guessing here would
 * paint a falling return rate red, which is exactly backwards.
 */
export type GoodDirection = 'up' | 'down' | 'neutral';

/** Metrics where a *rise* is bad: rates of things going wrong. */
const LOWER_IS_BETTER = /(^|_)(return|refund|cancel|churn|defect|complaint|stockout|backorder)/;

/** Metrics where a rise is plainly good: volume and value. */
const HIGHER_IS_BETTER =
  /^(revenue|orders|units_sold|gross_margin|avg_order_value|profit|margin|units_on_hand)$/;

export function goodDirectionFor(metric: string): GoodDirection {
  const key = metric.toLowerCase();
  if (LOWER_IS_BETTER.test(key)) return 'down';
  if (HIGHER_IS_BETTER.test(key)) return 'up';
  return 'neutral';
}

/**
 * How the delta baseline reads on a KPI tile.
 *
 * `previousRange` always returns the equal-length window *immediately before*
 * the selection, so the wording has to match that exactly. A quarter-to-date or
 * year-to-date selection is therefore compared against the equal span that
 * preceded it — not against the whole prior quarter or the same span last year,
 * which is what a reader would otherwise assume.
 */
export function comparisonLabelFor(key: RangeKey): string {
  switch (key) {
    case '7d':
      return 'vs. prior 7 days';
    case '30d':
      return 'vs. prior 30 days';
    case '12m':
      return 'vs. prior 12 months';
    case 'quarter':
    case 'ytd':
    default:
      return 'vs. prior equal span';
  }
}

/* -------------------------------------------------------------------------- */
/* Result readers                                                             */
/* -------------------------------------------------------------------------- */

/** Index of the column holding the metric's value (the last numeric column). */
function valueIndex(result: MetricResult, metric: string): number {
  const named = result.columns.findIndex((c) => c.name === metric);
  if (named >= 0) return named;
  for (let i = result.columns.length - 1; i >= 0; i -= 1) {
    const dtype = result.columns[i]?.dtype;
    if (dtype === 'number' || dtype === 'currency' || dtype === 'ratio') return i;
  }
  return result.columns.length - 1;
}

function toNumber(value: Cell): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * Scalar total for an aggregate query; null when the result has no rows.
 *
 * `additive` comes from the metric catalog: ratios and averages must be
 * averaged across a breakdown, never summed.
 */
export function scalarValue(
  result: MetricResult | undefined,
  metric: string,
  additive?: boolean,
): number | null {
  if (!result || !result.rows.length) return null;
  const index = valueIndex(result, metric);
  let total = 0;
  let count = 0;
  for (const row of result.rows) {
    const value = toNumber(row[index] ?? null);
    if (value == null) continue;
    total += value;
    count += 1;
  }
  if (!count) return null;
  const summable =
    additive ?? result.columns[index]?.dtype !== 'ratio';
  return summable ? total : total / count;
}

/** Render unit for a metric: server hint first, then the catalog, then a guess. */
export function unitOf(
  result: MetricResult | undefined,
  fallback: MetricUnit,
): MetricUnit {
  const meta = result?.meta;
  if (meta?.unit) return meta.unit;
  switch (meta?.format) {
    case 'currency':
      return 'currency';
    case 'percent':
      return 'ratio';
    case 'integer':
    case 'decimal':
      return 'count';
    default:
      return fallback;
  }
}

/** Build a KPI summary from a current and a previous-period result. */
export function summarize(
  metric: string,
  unit: MetricUnit,
  current: MetricResult | undefined,
  previous: MetricResult | undefined,
  additive?: boolean,
): MetricSummary | null {
  const value = scalarValue(current, metric, additive);
  if (value == null) return null;
  const prior = scalarValue(previous, metric, additive);
  const summary: MetricSummary = { value, unit };
  if (prior != null && prior !== 0) {
    summary.previous = prior;
    summary.delta_pct = (value - prior) / Math.abs(prior);
  }
  return summary;
}

/**
 * The metric's values in row order — the shape a sparkline needs.
 *
 * Deliberately values-only: a sparkline shows the *contour* of the window and
 * must never be read as a source of figures. Rows whose value cannot be parsed
 * are dropped rather than coerced to zero, which would draw a false dip.
 */
export function seriesValues(
  result: MetricResult | undefined,
  metric: string,
): number[] {
  if (!result?.rows.length) return [];
  const index = valueIndex(result, metric);
  const out: number[] = [];
  for (const row of result.rows) {
    const value = toNumber(row[index] ?? null);
    if (value != null) out.push(value);
  }
  return out;
}

/** Convert a two-column [dimension, metric] result into chart rows. */
export function toChartRows(
  result: MetricResult | undefined,
  metric: string,
): { rows: Array<Record<string, Cell>>; xKey: string } | null {
  if (!result || !result.rows.length || result.columns.length < 2) return null;
  // `records` is the server's pre-zipped form of the same data.
  if (result.records?.length) {
    const vi = valueIndex(result, metric);
    const xi = result.columns.findIndex((_, i) => i !== vi);
    return {
      xKey: result.columns[xi]?.name ?? 'x',
      rows: result.records,
    };
  }
  const vi = valueIndex(result, metric);
  const xi = result.columns.findIndex((_, i) => i !== vi);
  const xKey = result.columns[xi]?.name ?? 'x';
  const yKey = result.columns[vi]?.name ?? metric;
  return {
    xKey,
    rows: result.rows.map((row) => ({
      [xKey]: row[xi] ?? null,
      [yKey]: toNumber(row[vi] ?? null),
    })),
  };
}

const UNIT_DTYPE: Record<MetricUnit, ColumnDtype> = {
  currency: 'currency',
  ratio: 'ratio',
  count: 'number',
  duration: 'number',
};

/** Chart spec for a metric broken down by one dimension. */
export function toMetricChart(
  result: MetricResult | undefined,
  metric: string,
  label: string,
  unit: MetricUnit,
  kind: ChartSpec['kind'] = 'bar',
): ChartSpec | null {
  if (!result) return null;
  const data = toChartRows(result, metric);
  if (!data) return null;
  const yKey = result.columns[valueIndex(result, metric)]?.name ?? metric;
  return {
    kind,
    x: data.xKey,
    series: [{ y: yKey, label }],
    options: { yFormat: UNIT_DTYPE[unit] },
    data: data.rows,
  };
}
