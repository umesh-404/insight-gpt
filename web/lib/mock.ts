/**
 * Mock data layer, gated behind NEXT_PUBLIC_USE_MOCK.
 *
 * Nothing in this module may be imported by a component: mock fixtures must be
 * reachable **only** through lib/api.ts, which checks `USE_MOCK` before calling
 * in. That keeps demo data from leaking into a real-mode screen.
 *
 * Shapes match lib/types.ts exactly, so the mock and live paths exercise the
 * same rendering code.
 */
import type {
  AnswerEnvelope,
  AskStreamEvent,
  Cell,
  ColumnSpec,
  ForecastCapabilityReport,
  ForecastPoint,
  ForecastResult,
  Conversation,
  ConversationSummary,
  Insight,
  InsightPage,
  MetricQuery,
  MetricResult,
  MetricsCatalog,
  Pipeline,
  PipelineRun,
  Report,
  ReportRequest,
  ReportSummary,
  Source,
  SourceConfig,
  SourceTestResult,
  SystemStatus,
  TokenPair,
  User,
} from './types';

export const MOCK_USER: User = {
  id: 'u_demo',
  email: 'demo@insightgpt.local',
  name: 'Demo Analyst',
  role: 'admin',
};

export const MOCK_TOKENS: TokenPair = {
  access_token: 'mock-access-token',
  refresh_token: 'mock-refresh-token',
  token_type: 'bearer',
  expires_in: 900,
};

/* -------------------------------------------------------------------------- */
/* Mock session (stands in for the httpOnly refresh cookie)                   */
/* -------------------------------------------------------------------------- */

const SESSION_KEY = 'igpt-mock-session';

export function hasMockSession(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(SESSION_KEY) === '1';
  } catch {
    return false;
  }
}

export function setMockSession(active: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    if (active) window.localStorage.setItem(SESSION_KEY, '1');
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable */
  }
}

/* -------------------------------------------------------------------------- */
/* Flagship answer envelope: "Why did sales decline last quarter?"            */
/* -------------------------------------------------------------------------- */

const REVENUE_TREND = [
  { month: 'Jan', revenue: 1284000, orders: 9120 },
  { month: 'Feb', revenue: 1361000, orders: 9640 },
  { month: 'Mar', revenue: 1420000, orders: 10180 },
  { month: 'Apr', revenue: 1305000, orders: 9410 },
  { month: 'May', revenue: 1198000, orders: 8730 },
  { month: 'Jun', revenue: 1112000, orders: 8190 },
];

export const FLAGSHIP_ANSWER =
  'Revenue fell 12.4% quarter-over-quarter, from $4.20M in Q1 to $3.68M in Q2. ' +
  'The decline is concentrated in two areas. First, the Outdoor & Garden ' +
  'category dropped 28% as the seasonal promotion that ran in Q1 was not ' +
  'repeated [1] — this single category accounts for roughly two-thirds of the ' +
  'total shortfall. Second, the West region underperformed by 15%, coinciding ' +
  'with a spike in delivery-delay complaints after a carrier change in April [2]. ' +
  'Average order value held steady ($131 → $129), so the driver is order ' +
  'volume, not basket size. Customer sentiment corroborates the numbers: ' +
  'shipping-related complaints rose 41% over the same window [3].';

export const FLAGSHIP_ENVELOPE: AnswerEnvelope = {
  answer: FLAGSHIP_ANSWER,
  route: 'hybrid',
  confidence: 'high',
  dialect: 'postgres',
  sql: [
    `-- Governed text-to-SQL, compiled from the semantic layer (read-only)
SELECT
    d.category            AS category,
    d.region              AS region,
    date_trunc('quarter', f.order_date) AS quarter,
    SUM(f.net_revenue)    AS revenue,
    COUNT(DISTINCT f.order_id) AS orders
FROM marts.fct_orders    AS f
JOIN marts.dim_product   AS d ON d.product_key = f.product_key
WHERE f.order_date >= date '2026-01-01'
  AND f.order_date <  date '2026-07-01'
GROUP BY 1, 2, 3
ORDER BY quarter, revenue DESC
LIMIT 1000`,
  ],
  chart_spec: {
    kind: 'area',
    title: 'Revenue by month',
    x: 'month',
    series: [{ y: 'revenue', label: 'Revenue' }],
    options: { yFormat: 'currency', unit: 'currency' },
    data: REVENUE_TREND,
  },
  tables: [
    {
      name: 'Revenue by category (QoQ)',
      columns: [
        { name: 'Category', dtype: 'string' },
        { name: 'Q1 revenue', dtype: 'currency' },
        { name: 'Q2 revenue', dtype: 'currency' },
        { name: 'Change', dtype: 'ratio' },
      ],
      rows: [
        ['Outdoor & Garden', 980000, 706000, -0.28],
        ['Electronics', 1120000, 1064000, -0.05],
        ['Home & Kitchen', 890000, 845000, -0.05],
        ['Apparel', 640000, 615000, -0.04],
        ['Toys & Games', 570000, 452000, -0.21],
      ],
    },
    {
      name: 'Revenue by region (QoQ)',
      columns: [
        { name: 'Region', dtype: 'string' },
        { name: 'Q2 revenue', dtype: 'currency' },
        { name: 'Change', dtype: 'ratio' },
      ],
      rows: [
        ['North', 1024000, -0.04],
        ['South', 986000, -0.06],
        ['East', 902000, -0.09],
        ['West', 768000, -0.15],
      ],
    },
  ],
  citations: [
    {
      n: 1,
      doc_id: 'REPORT-Q1-PROMO',
      source_type: 'report',
      title: 'Outdoor promo retrospective (Q1)',
      date: '2026-04-04',
      score: 0.83,
      snippet:
        'The spring garden promotion drove a 3.1x lift in Outdoor & Garden ' +
        'units. The campaign concluded March 31 and was not extended into Q2.',
    },
    {
      n: 2,
      doc_id: 'TICKET-40122',
      source_type: 'ticket',
      title: 'Q2 support ticket themes — weekly digest',
      date: '2026-05-08',
      score: 0.88,
      snippet:
        'Delivery delays dominated inbound volume in April–May, especially for ' +
        'West-region ZIP codes after the carrier switch.',
    },
    {
      n: 3,
      doc_id: 'REVIEW-9931',
      source_type: 'review',
      title: 'Product review sentiment — shipping',
      date: '2026-05-19',
      score: 0.79,
      snippet:
        'Shipping sentiment fell from 4.1 to 3.4 stars in the West region.',
    },
  ],
  caveats: [
    'Q2 figures reflect data through Jun 30; the last two days of June are still settling and may revise upward by ~1%.',
    'Revenue is net of returns and excludes tax and shipping, per the governed `net_revenue` metric.',
  ],
};

/** Turn an envelope into the ordered SSE event sequence the backend emits. */
export function mockStreamEvents(envelope: AnswerEnvelope): AskStreamEvent[] {
  const events: AskStreamEvent[] = [];
  events.push({
    type: 'meta',
    data: { conversation_id: 'c_demo', message_id: `m_${Date.now()}` },
  });
  if (envelope.route) {
    events.push({
      type: 'route',
      data: {
        route: envelope.route,
        confidence: envelope.confidence,
        abstained: envelope.abstained ?? false,
      },
    });
  }
  const words = envelope.answer.split(' ');
  for (let i = 0; i < words.length; i += 3) {
    const chunk = words.slice(i, i + 3).join(' ');
    events.push({
      type: 'token',
      data: { text: chunk + (i + 3 < words.length ? ' ' : '') },
    });
  }
  if (envelope.sql.length) {
    events.push({
      type: 'sql',
      data: {
        // The live stream sends one frame with statements joined by ";\n\n".
        sql: envelope.sql.join(';\n\n'),
        dialect: envelope.dialect ?? 'postgres',
      },
    });
  }
  // One frame per table — the client must accumulate, not overwrite.
  for (const table of envelope.tables) {
    events.push({ type: 'tables', data: table });
  }
  if (envelope.citations.length) {
    events.push({ type: 'citations', data: { items: envelope.citations } });
  }
  if (envelope.chart_spec) {
    events.push({
      type: 'chart',
      data: { chart_spec: envelope.chart_spec, raw: envelope.chart_spec },
    });
  }
  if (envelope.caveats.length) {
    events.push({ type: 'caveats', data: { items: envelope.caveats } });
  }
  if (envelope.attempts?.length) {
    events.push({ type: 'corrections', data: { items: envelope.attempts } });
  }
  if (envelope.abstained) {
    events.push({
      type: 'abstain',
      data: {
        reason: envelope.abstain_reason ?? '',
        suggestions: envelope.suggestions ?? [],
      },
    });
  }
  events.push({
    type: 'done',
    data: {
      message_id: `m_${Date.now()}`,
      usage: { latency_ms: 3120, confidence: envelope.confidence },
    },
  });
  return events;
}

/**
 * Metric names a real business asks about that InsightGPT does **not** govern.
 * The live engine abstains on these; demo mode must show the same behaviour,
 * because "we refuse rather than guess" is a headline property of the product.
 */
const UNGOVERNED_METRICS =
  /\b(churn|retention|attrition|nps|csat|ltv|lifetime value|cac|conversion rate|bounce rate)\b/i;

/** A topic-matched fallback envelope for questions other than the flagship one. */
export function mockEnvelopeFor(question: string): AnswerEnvelope {
  if (UNGOVERNED_METRICS.test(question)) {
    const requested = UNGOVERNED_METRICS.exec(question)?.[1] ?? 'that metric';
    const reason =
      `'${requested}' is not a governed metric, so I cannot compute it reliably.`;
    return {
      answer: `I can't answer that reliably, so I won't guess. ${reason}`,
      route: 'abstain',
      confidence: 'low',
      sql: [],
      tables: [],
      citations: [],
      chart_spec: null,
      caveats: [],
      abstained: true,
      abstain_reason: reason,
      suggestions: [
        "Try the governed metric 'return_rate'.",
        "Try the governed metric 'orders'.",
        "Try the governed metric 'avg_order_value'.",
      ],
      attempts: [],
    };
  }
  if (/restock|inventory|reorder|stock/i.test(question)) {
    return {
      answer:
        'Nine SKUs are at elevated stock-out risk within their lead time. The ' +
        'highest priority are the Cordless Drill 18V (6 days of cover, 11-day ' +
        'lead time) and the Trail Running Shoe (8 days cover). Prioritize ' +
        'reorders for the Tools and Footwear categories, which together hold ' +
        'seven of the nine flagged items.',
      route: 'structured',
      confidence: 'high',
      dialect: 'postgres',
      sql: [
        `SELECT p.sku, p.name, i.on_hand, s.lead_time_days
FROM marts.fct_inventory i
JOIN marts.dim_product p ON p.product_key = i.product_key
JOIN marts.dim_supplier s ON s.supplier_key = p.supplier_key
ORDER BY i.on_hand ASC
LIMIT 1000`,
      ],
      chart_spec: {
        kind: 'bar',
        title: 'Days of cover vs. lead time (at-risk SKUs)',
        x: 'sku',
        series: [
          { y: 'days_of_cover', label: 'Days of cover' },
          { y: 'lead_time_days', label: 'Lead time' },
        ],
        data: [
          { sku: 'Cordless Drill 18V', days_of_cover: 6, lead_time_days: 11 },
          { sku: 'Trail Running Shoe', days_of_cover: 8, lead_time_days: 10 },
          { sku: 'Ceramic Dutch Oven', days_of_cover: 9, lead_time_days: 12 },
          { sku: 'Wireless Earbuds Pro', days_of_cover: 10, lead_time_days: 14 },
        ],
      },
      tables: [],
      citations: [],
      caveats: [
        'Lead times use the supplier default where a SKU-specific value is missing.',
      ],
      // Demonstrates the bounded self-correction loop: the first governed
      // selection was rejected, the engine narrowed it and recovered.
      attempts: [
        {
          attempt: 1,
          stage: 'grouped:product',
          selection: { metric: 'units_on_hand', dimensions: ['product', 'supplier'] },
          error: "Dimension 'supplier' is not available at the inventory grain.",
          resolution: 'corrected',
        },
      ],
    };
  }
  if (/complaint|sentiment|voice|review/i.test(question)) {
    return {
      answer:
        'This month’s complaints cluster into three themes: delivery delays ' +
        '(38% of negative contacts), missing or damaged items (24%), and ' +
        'sizing inaccuracy in apparel (17%) [1]. Delivery is both the largest ' +
        'and the fastest-growing theme, up 41% versus last month [2].',
      route: 'unstructured',
      confidence: 'medium',
      sql: [],
      chart_spec: {
        kind: 'pie',
        title: 'Complaint themes (share of negative contacts)',
        x: 'theme',
        series: [{ y: 'share', label: 'Share' }],
        options: { nameKey: 'theme', valueKey: 'share' },
        data: [
          { theme: 'Delivery delays', share: 38 },
          { theme: 'Missing / damaged', share: 24 },
          { theme: 'Sizing inaccuracy', share: 17 },
          { theme: 'Billing', share: 12 },
          { theme: 'Other', share: 9 },
        ],
      },
      tables: [],
      citations: FLAGSHIP_ENVELOPE.citations.slice(1, 3).map((c, i) => ({
        ...c,
        n: i + 1,
      })),
      caveats: [
        'Themes are derived from clustering; a contact maps to one primary theme only.',
      ],
    };
  }
  return { ...FLAGSHIP_ENVELOPE };
}

/* -------------------------------------------------------------------------- */
/* Conversations                                                              */
/* -------------------------------------------------------------------------- */

export const MOCK_CONVERSATIONS: ConversationSummary[] = [
  {
    id: 'c_demo',
    title: 'Why did sales decline last quarter?',
    created_at: '2026-08-24T09:12:00Z',
    updated_at: '2026-08-24T09:14:00Z',
    message_count: 2,
  },
  {
    id: 'c_2',
    title: 'Which products should we restock?',
    created_at: '2026-08-22T15:40:00Z',
    updated_at: '2026-08-22T15:41:00Z',
    message_count: 2,
  },
  {
    id: 'c_3',
    title: 'Summarize customer complaints this month',
    created_at: '2026-08-20T11:05:00Z',
    updated_at: '2026-08-20T11:06:00Z',
    message_count: 2,
  },
];

/**
 * Rename in place so mock mode behaves like the real backend: the sidebar
 * refetch after a rename must show the new title, not the seeded one.
 */
export function mockRenameConversation(
  id: string,
  title: string,
): ConversationSummary {
  const found = MOCK_CONVERSATIONS.find((c) => c.id === id);
  const updated: ConversationSummary = {
    ...(found ?? MOCK_CONVERSATIONS[0]!),
    id,
    title,
  };
  if (found) Object.assign(found, updated);
  else MOCK_CONVERSATIONS.unshift(updated);
  return { ...updated };
}

/** Drop a mock conversation; a no-op when the id is unknown. */
export function mockDeleteConversation(id: string): void {
  const index = MOCK_CONVERSATIONS.findIndex((c) => c.id === id);
  if (index >= 0) MOCK_CONVERSATIONS.splice(index, 1);
}

export function mockConversation(id: string): Conversation {
  // The list is mutable in mock mode (rename/delete), so it can legitimately be
  // empty — synthesize a summary rather than reading off the end of the array.
  const summary: ConversationSummary = MOCK_CONVERSATIONS.find(
    (c) => c.id === id,
  ) ??
    MOCK_CONVERSATIONS[0] ?? {
      id,
      title: 'Untitled conversation',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 0,
    };
  return {
    id: summary.id,
    title: summary.title,
    created_at: summary.created_at,
    turns: [
      {
        id: 'm_1',
        question: summary.title,
        envelope: mockEnvelopeFor(summary.title),
        created_at: summary.created_at,
        feedback: null,
      },
    ],
  };
}

/* -------------------------------------------------------------------------- */
/* Governed metrics + dashboard                                               */
/* -------------------------------------------------------------------------- */

export const MOCK_CATALOG: MetricsCatalog = {
  metrics: [
    { key: 'revenue', label: 'Revenue', description: 'Net revenue after returns.', unit: 'currency', grain: ['date', 'region', 'category', 'product'], default_agg: 'sum' },
    { key: 'orders', label: 'Orders', description: 'Distinct completed orders.', unit: 'count', grain: ['date', 'region', 'category'], default_agg: 'count' },
    { key: 'avg_order_value', label: 'Average order value', description: 'Revenue per order.', unit: 'currency', grain: ['date', 'region'], default_agg: 'avg' },
    { key: 'return_rate', label: 'Return rate', description: 'Returned units / total units.', unit: 'ratio', grain: ['date', 'category'], default_agg: 'ratio' },
    { key: 'units_on_hand', label: 'Units on hand', description: 'Latest inventory snapshot.', unit: 'count', grain: ['date', 'product', 'category'], default_agg: 'sum' },
  ],
  dimensions: [
    { key: 'date', grains: ['day', 'week', 'month', 'quarter', 'year'], is_date: true },
    { key: 'region', grains: [], is_date: false },
    { key: 'category', grains: [], is_date: false },
    { key: 'product', grains: [], is_date: false },
    { key: 'channel', grains: [], is_date: false },
  ],
};

const MOCK_TOTALS: Record<string, number> = {
  revenue: 3680000,
  orders: 26240,
  avg_order_value: 129,
  return_rate: 0.068,
  units_on_hand: 41820,
};

const MOCK_BY_PRODUCT: Array<[string, number, number]> = [
  // [product, revenue, units_on_hand]
  ['Wireless Earbuds Pro', 412000, 180],
  ['Ceramic Dutch Oven', 356000, 240],
  ['4K Streaming Stick', 298000, 96],
  ['Trail Running Shoe', 264000, 310],
  ['Cordless Drill 18V', 231000, 64],
  ['Patio Umbrella XL', 41000, 420],
];

const MOCK_BY_REGION: Array<[string, number]> = [
  ['North', 1024000],
  ['South', 986000],
  ['East', 902000],
  ['West', 768000],
];

/** Builds a `MetricResult` that honours dimensions, ordering, and limits. */
export function mockMetricResult(query: MetricQuery): MetricResult {
  const metric = query.metric;
  const unitDtype =
    metric === 'return_rate'
      ? ('ratio' as const)
      : metric === 'revenue' || metric === 'avg_order_value'
        ? ('currency' as const)
        : ('number' as const);
  const dimension = query.dimensions?.[0];
  // Mock filters shift the totals so the dashboard visibly reacts to controls.
  const scale = query.filters?.length ? 0.4 : 1;

  let rows: Cell[][];
  let columns: ColumnSpec[] = [{ name: metric, dtype: unitDtype }];

  if (!dimension) {
    rows = [[round(MOCK_TOTALS[metric] ?? 0, scale, metric)]];
  } else if (dimension === 'date') {
    columns = [{ name: 'date', dtype: 'string' as const }, ...columns];
    rows = REVENUE_TREND.map((r) => [
      r.month,
      round(metric === 'orders' ? r.orders : r.revenue, scale, metric),
    ]);
  } else if (dimension === 'region') {
    columns = [{ name: 'region', dtype: 'string' as const }, ...columns];
    rows = MOCK_BY_REGION.map(([name, value]) => [name, round(value, scale, metric)]);
  } else {
    columns = [{ name: dimension, dtype: 'string' as const }, ...columns];
    rows = MOCK_BY_PRODUCT.map(([name, revenue, onHand]) => [
      name,
      round(metric === 'units_on_hand' ? onHand : revenue, scale, metric),
    ]);
  }

  if (query.order_by_metric && rows.length > 1) {
    const valueIndex = columns.length - 1;
    rows = [...rows].sort((a, b) => {
      const av = Number(a[valueIndex] ?? 0);
      const bv = Number(b[valueIndex] ?? 0);
      return query.order_by_metric === 'asc' ? av - bv : bv - av;
    });
  }
  const truncated = typeof query.limit === 'number' && rows.length > query.limit;
  if (truncated) rows = rows.slice(0, query.limit);

  return {
    columns,
    rows,
    sql: `SELECT ${dimension ? `${dimension}, ` : ''}${metric}\nFROM marts.metric_${metric}\n${
      query.time_range ? `WHERE date BETWEEN '${query.time_range.start}' AND '${query.time_range.end}'\n` : ''
    }${query.order_by_metric ? `ORDER BY ${metric} ${query.order_by_metric.toUpperCase()}\n` : ''}LIMIT ${query.limit ?? 1000}`,
    row_count: rows.length,
    truncated,
  };
}

function round(value: number, scale: number, metric: string): number {
  const scaled = value * scale;
  return metric === 'return_rate' ? Number(scaled.toFixed(4)) : Math.round(scaled);
}

/* -------------------------------------------------------------------------- */
/* Pipelines                                                                  */
/* -------------------------------------------------------------------------- */

export const MOCK_PIPELINES: Pipeline[] = [
  {
    name: 'full_ingest',
    description: 'Full reload of every registered source into the raw layer.',
    schedule: '0 */6 * * *',
    last_run: { id: 'r_1001', pipeline: 'full_ingest', status: 'success', started_at: '2026-08-24T06:00:00Z', finished_at: '2026-08-24T06:04:12Z' },
  },
  {
    name: 'reindex_docs',
    description: 'Chunk, embed, and index tickets, reviews, and reports.',
    schedule: '0 */12 * * *',
    last_run: { id: 'r_1002', pipeline: 'reindex_docs', status: 'partial', started_at: '2026-08-24T00:00:00Z', finished_at: '2026-08-24T00:09:41Z' },
  },
  {
    name: 'incremental_sync',
    description: 'Pull only rows changed since the last successful run.',
    schedule: '*/30 * * * *',
    last_run: { id: 'r_1004', pipeline: 'incremental_sync', status: 'success', started_at: '2026-08-24T09:30:00Z', finished_at: '2026-08-24T09:30:48Z' },
  },
  {
    name: 'dbt_build',
    description: 'Run dbt transforms into the star schema and semantic marts.',
    schedule: null,
    last_run: { id: 'r_1003', pipeline: 'dbt_build', status: 'running', started_at: '2026-08-24T09:10:00Z', finished_at: null },
  },
];

export const MOCK_RUNS: PipelineRun[] = [
  {
    id: 'r_1001',
    pipeline: 'full_ingest',
    status: 'success',
    trigger: 'scheduled',
    started_at: '2026-08-24T06:00:00Z',
    finished_at: '2026-08-24T06:04:12Z',
    row_counts: { raw: 184320, staging: 184320, marts: 91240 },
    error: null,
    stages: [
      { name: 'extract', rows_in: 0, rows_out: 184320, ms: 41200 },
      { name: 'load_raw', rows_in: 184320, rows_out: 184320, ms: 38900 },
      { name: 'dbt_run', rows_in: 184320, rows_out: 91240, ms: 158000 },
      { name: 'dbt_test', rows_in: 91240, rows_out: 91240, ms: 13200 },
    ],
  },
  {
    id: 'r_1002',
    pipeline: 'reindex_docs',
    status: 'partial',
    trigger: 'scheduled',
    started_at: '2026-08-24T00:00:00Z',
    finished_at: '2026-08-24T00:09:41Z',
    row_counts: { documents: 4210, chunks: 18640, embedded: 18102 },
    error: 'Rerank model timed out on 538 chunks; retried and skipped after 3 attempts.',
    stages: [
      { name: 'fetch_documents', rows_in: 0, rows_out: 4210, ms: 22100 },
      { name: 'chunk', rows_in: 4210, rows_out: 18640, ms: 31400 },
      { name: 'embed', rows_in: 18640, rows_out: 18102, ms: 168000, error: '538 chunks failed embedding' },
      { name: 'upsert_index', rows_in: 18102, rows_out: 18102, ms: 24200 },
    ],
  },
  {
    id: 'r_1003',
    pipeline: 'dbt_build',
    status: 'running',
    trigger: 'manual',
    started_at: '2026-08-24T09:10:00Z',
    finished_at: null,
    row_counts: { rollups: 0 },
    error: null,
    stages: [{ name: 'aggregate', rows_in: 91240, rows_out: 0, ms: 0 }],
  },
  {
    id: 'r_0999',
    pipeline: 'full_ingest',
    status: 'failed',
    trigger: 'scheduled',
    started_at: '2026-08-24T00:00:00Z',
    finished_at: '2026-08-24T00:01:03Z',
    row_counts: {},
    error: 'Connection to source `orders_pg` refused (ECONNREFUSED). Retried 3×.',
    stages: [{ name: 'extract', rows_in: 0, rows_out: 0, ms: 63000, error: 'ECONNREFUSED orders_pg' }],
  },
];

export function mockRun(id: string): PipelineRun {
  return MOCK_RUNS.find((r) => r.id === id) ?? MOCK_RUNS[0]!;
}

/* -------------------------------------------------------------------------- */
/* Sources (mutable so the register/delete flows are exercised in mock mode)  */
/* -------------------------------------------------------------------------- */

let mockSources: Source[] = [
  { id: 's_1', name: 'warehouse (postgres)', kind: 'postgres', status: 'ok', last_tested_at: '2026-08-24T06:00:00Z', location: 'db.internal:5432', detail: 'Connected and introspected 18 table(s).' },
  { id: 's_2', name: 'orders.csv', kind: 'csv', status: 'ok', last_tested_at: '2026-08-23T18:00:00Z', location: 'data/generated/orders.csv', detail: 'File is readable (482911 bytes).' },
  { id: 's_3', name: 'document corpus', kind: 'documents', status: 'ok', last_tested_at: '2026-08-24T00:00:00Z', location: 'data/ingested/documents.json', detail: 'File is readable (1204873 bytes).' },
  { id: 's_4', name: 'legacy_mysql', kind: 'mysql', status: 'error', last_tested_at: '2026-08-20T12:00:00Z', location: 'legacy.internal:3306', detail: 'TimeoutError: timed out' },
  { id: 's_5', name: 'reviews_export', kind: 'excel', status: 'untested', last_tested_at: null, location: 'data/generated/reviews.xlsx', detail: null },
];

export function listMockSources(): Source[] {
  return [...mockSources];
}

/** Mirrors the API's non-secret `location`: a path, or `host:port` for a DSN. */
function mockLocation(config: SourceConfig): string | null {
  if (config.dsn) {
    const match = /@([^/?#]+)/.exec(config.dsn);
    return match?.[1] ?? null;
  }
  const path = config.options?.path;
  return typeof path === 'string' && path.trim() ? path.trim() : null;
}

export function addMockSource(config: SourceConfig): Source {
  const source: Source = {
    id: `s_${Date.now()}`,
    name: config.name,
    kind: config.kind,
    status: 'untested',
    last_tested_at: null,
    active: true,
    location: mockLocation(config),
    detail: 'Registered. Run a test to verify connectivity.',
  };
  mockSources = [...mockSources, source];
  return source;
}

/** Runs a plausible probe and, like the API, records its outcome on the row. */
export function testMockSource(id: string): SourceTestResult {
  const source = mockSources.find((s) => s.id === id);
  const ok = source?.kind !== 'mysql';
  const result: SourceTestResult = {
    ok,
    latency_ms: ok ? 42 : 5008,
    tables_seen: ok ? 14 : 0,
    message: ok
      ? 'Connected and introspected 14 table(s).'
      : 'TimeoutError: timed out',
    checked: ok ? 'connect+introspect' : 'tcp',
    error_code: ok ? null : 'connect_failed',
  };
  mockSources = mockSources.map((s) =>
    s.id === id
      ? {
          ...s,
          status: ok ? 'ok' : 'error',
          last_tested_at: new Date().toISOString(),
          detail: result.message,
        }
      : s,
  );
  return result;
}

export function removeMockSource(id: string): void {
  mockSources = mockSources.filter((s) => s.id !== id);
}

/* -------------------------------------------------------------------------- */
/* Reports                                                                    */
/* -------------------------------------------------------------------------- */

export const MOCK_REPORTS: ReportSummary[] = [
  { id: 'rep_1', title: 'Q2 2026 Executive Summary', status: 'ready', created_at: '2026-08-24T08:00:00Z' },
  { id: 'rep_2', title: 'July Inventory Health', status: 'ready', created_at: '2026-08-01T08:00:00Z' },
  { id: 'rep_3', title: 'Voice of Customer — August', status: 'generating', created_at: '2026-08-24T09:15:00Z' },
];

export function addMockReport(body: ReportRequest): string {
  const id = `rep_${Date.now()}`;
  MOCK_REPORTS.unshift({
    id,
    title: body.title,
    status: 'ready',
    created_at: new Date().toISOString(),
  });
  return id;
}

export function mockReport(id: string): Report {
  const summary = MOCK_REPORTS.find((r) => r.id === id) ?? MOCK_REPORTS[0]!;
  return {
    id: summary.id,
    status: summary.status,
    title: summary.title,
    period: { grain: 'quarter', start: '2026-04-01', end: '2026-06-30' },
    created_at: summary.created_at,
    blocks: [
      {
        id: `${summary.id}_b0`,
        heading: 'Headline metrics',
        prose:
          'Revenue for the period was $3.68M, down 12.4% versus the prior ' +
          'quarter. Orders fell 11% while average order value was essentially ' +
          'flat, indicating a volume-driven decline rather than a pricing or ' +
          'basket-size effect.',
        chart_spec: FLAGSHIP_ENVELOPE.chart_spec,
        citations: [],
      },
      {
        id: `${summary.id}_b1`,
        heading: 'Sales decomposition',
        prose:
          'Two-thirds of the shortfall traces to Outdoor & Garden, where the ' +
          'Q1 seasonal promotion was not repeated [1]. The West region ' +
          'underperformed by 15%, coinciding with elevated delivery-delay ' +
          'complaints after an April carrier change [2].',
        chart_spec: null,
        citations: FLAGSHIP_ENVELOPE.citations,
      },
      {
        id: `${summary.id}_b2`,
        heading: 'Voice of customer',
        prose:
          'Shipping-related complaints rose 41% quarter-over-quarter, ' +
          'concentrated in the West. Sentiment on delivery fell from 4.1 to ' +
          '3.4 stars.',
        chart_spec: null,
        citations: FLAGSHIP_ENVELOPE.citations.slice(1, 3),
      },
    ],
  };
}

/** Stand-in for the server-rendered export so the download flow is testable. */
export function mockReportMarkdown(id: string): string {
  const report = mockReport(id);
  const blocks = report.blocks
    .map((block) => `## ${block.heading}\n\n${block.prose}\n`)
    .join('\n');
  return `# ${report.title}\n\n_${report.period.start} — ${report.period.end}_\n\n${blocks}`;
}

/* -------------------------------------------------------------------------- */
/* System status                                                              */
/* -------------------------------------------------------------------------- */

export const MOCK_STATUS: SystemStatus = {
  status: 'ok',
  version: '0.1.0',
  uptime_s: 4820,
  services: {
    postgres: { status: 'ok', latency_ms: 4 },
    vector_index: { status: 'ok', latency_ms: 11 },
    worker: { status: 'ok', latency_ms: 2 },
    llm: { status: 'ok', latency_ms: 320 },
  },
  warehouse: [
    { label: 'Marts rows', value: '91,240' },
    { label: 'Last dbt run', value: '2026-08-24T06:04:12Z' },
  ],
  index: [
    { label: 'Collection size', value: '18,102' },
    { label: 'Last index', value: '2026-08-24T00:09:41Z' },
  ],
  llm: { provider: 'ollama', model: 'llama3.1:8b', reachable: true },
};

/* -------------------------------------------------------------------------- */
/* Proactive insight digest                                                   */
/* -------------------------------------------------------------------------- */

export const MOCK_INSIGHTS: Insight[] = [
  {
    id: 'ins_revenue_2026q2',
    metric: 'revenue',
    metric_label: 'Revenue',
    metric_format: 'currency',
    grain: 'quarter',
    period: '2026Q2',
    prior_period: '2026Q1',
    current: 1_152_000,
    prior: 1_300_000,
    change_abs: -148_000,
    change_pct: -0.114,
    direction: 'down',
    severity: 'high',
    z_score: null,
    method:
      'Period-over-period change at quarter grain (threshold 5%, min magnitude 0); insufficient history for a z-score.',
    headline:
      'Revenue fell 11.4% in 2026Q2 vs 2026Q1, from $1.30M to $1.15M. North (region) drove most of the move (-$130.0K, 88% of the change).',
    root_cause: {
      dimension: 'region',
      segment: 'North',
      current: 270_000,
      prior: 400_000,
      delta: -130_000,
      contribution_pct: 87.8,
    },
    contributions: [
      { dimension: 'region', segment: 'North', current: 270_000, prior: 400_000, delta: -130_000, contribution_pct: 87.8 },
      { dimension: 'region', segment: 'South', current: 313_600, prior: 320_000, delta: -6_400, contribution_pct: 4.3 },
      { dimension: 'region', segment: 'West', current: 294_000, prior: 300_000, delta: -6_000, contribution_pct: 4.1 },
      { dimension: 'region', segment: 'East', current: 274_400, prior: 280_000, delta: -5_600, contribution_pct: 3.8 },
      { dimension: 'category', segment: 'Electronics', current: 501_600, prior: 620_000, delta: -118_400, contribution_pct: 80.0 },
      { dimension: 'category', segment: 'Apparel', current: 387_300, prior: 405_000, delta: -17_700, contribution_pct: 12.0 },
      { dimension: 'category', segment: 'Home', current: 263_100, prior: 275_000, delta: -11_900, contribution_pct: 8.0 },
    ],
    trend: [
      { period: '2026Q1', value: 1_300_000 },
      { period: '2026Q2', value: 1_152_000 },
    ],
    evidence: [
      {
        n: 1,
        doc_id: 'TICKET-40122',
        source_type: 'ticket',
        title: 'Late delivery — North region electronics',
        date: '2026-05-08',
        score: 0.62,
        snippet:
          'Customer in the North region reports their electronics order arrived two weeks late due to a fulfilment centre backlog.',
      },
      {
        n: 2,
        doc_id: 'REPORT-Q2-OPS',
        source_type: 'report',
        title: 'Q2 operations review',
        date: '2026-06-30',
        score: 0.55,
        snippet:
          'The North fulfilment centre backlog was the dominant operational issue of the quarter, concentrated in electronics.',
      },
    ],
    created_at: '2026-08-26T06:30:00Z',
  },
  {
    id: 'ins_units_sold_2026q2',
    metric: 'units_sold',
    metric_label: 'Units sold',
    metric_format: 'integer',
    grain: 'quarter',
    period: '2026Q2',
    prior_period: '2026Q1',
    current: 116,
    prior: 141,
    change_abs: -25,
    change_pct: -0.177,
    direction: 'down',
    severity: 'high',
    z_score: null,
    method:
      'Period-over-period change at quarter grain (threshold 5%, min magnitude 0); insufficient history for a z-score.',
    headline:
      'Units sold fell 17.7% in 2026Q2 vs 2026Q1. Electronics (category) drove most of the move.',
    root_cause: {
      dimension: 'category',
      segment: 'Electronics',
      current: 45,
      prior: 66,
      delta: -21,
      contribution_pct: 84.0,
    },
    contributions: [
      { dimension: 'category', segment: 'Electronics', current: 45, prior: 66, delta: -21, contribution_pct: 84.0 },
      { dimension: 'category', segment: 'Apparel', current: 41, prior: 44, delta: -3, contribution_pct: 12.0 },
      { dimension: 'category', segment: 'Home', current: 30, prior: 31, delta: -1, contribution_pct: 4.0 },
    ],
    trend: [
      { period: '2026Q1', value: 141 },
      { period: '2026Q2', value: 116 },
    ],
    evidence: [],
    created_at: '2026-08-26T06:30:00Z',
  },
];

export function mockInsightPage(limit = 20, offset = 0): InsightPage {
  return {
    items: MOCK_INSIGHTS.slice(offset, offset + limit),
    total: MOCK_INSIGHTS.length,
    limit,
    offset,
    backend: 'memory (on-demand)',
  };
}

export function mockInsight(id: string): Insight {
  return MOCK_INSIGHTS.find((i) => i.id === id) ?? MOCK_INSIGHTS[0]!;
}

/* ----------------------------------------------------------------------------
 * Forecasting
 * ------------------------------------------------------------------------- */

/**
 * Mock mode carries enough history to actually project, so the happy path is
 * reachable without a backend. The live fixture warehouse holds only two
 * quarters and therefore refuses — `mockForecastRefusal` reproduces that state
 * so the refusal UI can be exercised too.
 */
const MOCK_FORECAST_HISTORY: ForecastPoint[] = [
  { period: '2025Q1', value: 1_010_000 },
  { period: '2025Q2', value: 1_075_000 },
  { period: '2025Q3', value: 1_140_000 },
  { period: '2025Q4', value: 1_265_000 },
  { period: '2026Q1', value: 1_300_000 },
  { period: '2026Q2', value: 1_152_000 },
];

export function mockForecast(metric: string, grain = 'quarter'): ForecastResult {
  const def = MOCK_CATALOG.metrics.find((m) => m.key === metric);
  const last =
    MOCK_FORECAST_HISTORY[MOCK_FORECAST_HISTORY.length - 1]?.value ?? 1_000_000;
  const forecast: ForecastPoint[] = [
    { period: '2026Q3', value: last * 0.99, lower: last * 0.9, upper: last * 1.08 },
    { period: '2026Q4', value: last * 1.01, lower: last * 0.86, upper: last * 1.16 },
  ];
  return {
    metric,
    metric_label: def?.label ?? metric,
    format: def?.format ?? 'currency',
    additive: def?.additive ?? true,
    grain,
    horizon: forecast.length,
    history: MOCK_FORECAST_HISTORY,
    forecast,
    method: 'damped Holt trend (pure-Python)',
    method_family: 'fallback',
    n_history: MOCK_FORECAST_HISTORY.length,
    interval_level: 0.8,
    confidence: 'low',
    low_confidence: true,
    caveats: [
      'Only 6 period(s) of history exist at quarter grain; the interval is wide.',
    ],
    headline: `${def?.label ?? metric} is projected to stay roughly flat next quarter.`,
  };
}

/** The state the live demo data actually produces: not enough history. */
export function mockForecastRefusal(metric: string, grain = 'quarter'): ForecastResult {
  const base = mockForecast(metric, grain);
  return {
    ...base,
    history: MOCK_FORECAST_HISTORY.slice(-2),
    forecast: [],
    method: 'none - insufficient history',
    method_family: 'none',
    n_history: 2,
    confidence: 'none',
    low_confidence: true,
    caveats: [
      `Refused to forecast: 2 ${grain}(s) of history, 4 required. A projection `
      + 'from this little data would be a guess dressed as an estimate.',
    ],
    headline: `Not enough history to forecast ${base.metric_label} at ${grain} grain.`,
  };
}

export function mockForecastCapabilities(grain = 'quarter'): ForecastCapabilityReport {
  return {
    grain,
    min_history: 4,
    method_family: 'fallback',
    method: 'damped Holt trend (pure-Python)',
    metrics: MOCK_CATALOG.metrics.map((m) => ({
      metric: m.key,
      label: m.label,
      format: m.format ?? 'decimal',
      additive: m.additive ?? true,
      grain,
      n_history: MOCK_FORECAST_HISTORY.length,
      forecastable: true,
      reason: null,
    })),
  };
}
