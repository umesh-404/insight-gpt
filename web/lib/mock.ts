/**
 * Mock data layer (gated behind NEXT_PUBLIC_USE_MOCK). Returns realistic
 * fixtures so every screen renders — and the flagship demo question streams —
 * without a running backend. Shapes match lib/types.ts exactly.
 */
import type {
  AnswerEnvelope,
  AskStreamEvent,
  Conversation,
  ConversationSummary,
  MetricDef,
  MetricResult,
  Pipeline,
  PipelineRun,
  Report,
  ReportSummary,
  Source,
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
  'repeated — this single category accounts for roughly two-thirds of the ' +
  'total shortfall. Second, the West region underperformed by 15%, coinciding ' +
  'with a spike in delivery-delay complaints after a carrier change in April. ' +
  'Average order value held steady ($131 → $129), so the driver is order ' +
  'volume, not basket size. Customer sentiment corroborates the numbers: ' +
  'shipping-related complaints rose 41% over the same window.';

export const FLAGSHIP_ENVELOPE: AnswerEnvelope = {
  answer: FLAGSHIP_ANSWER,
  route: 'hybrid',
  confidence: 0.86,
  dialect: 'postgres',
  sql: `-- Governed text-to-SQL, compiled from the semantic layer (read-only)
SELECT
    d.category            AS category,
    d.region              AS region,
    date_trunc('quarter', f.order_date) AS quarter,
    SUM(f.net_revenue)    AS revenue,
    COUNT(DISTINCT f.order_id) AS orders,
    SUM(f.net_revenue) / NULLIF(COUNT(DISTINCT f.order_id), 0) AS aov
FROM marts.fct_orders    AS f
JOIN marts.dim_product   AS d ON d.product_key = f.product_key
WHERE f.order_date >= date '2026-01-01'
  AND f.order_date <  date '2026-07-01'
GROUP BY 1, 2, 3
ORDER BY quarter, revenue DESC
LIMIT 1000;`,
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
  ],
  citations: [
    {
      id: 'doc_812',
      title: 'Q2 support ticket themes — weekly digest',
      source: 'support_tickets',
      snippet:
        'Delivery delays dominated inbound volume in April–May, especially for ' +
        'West-region ZIP codes after the carrier switch. 41% WoW increase in ' +
        '“where is my order” contacts.',
      score: 0.88,
      uri: '#',
    },
    {
      id: 'doc_654',
      title: 'Outdoor promo retrospective (Q1)',
      source: 'reports',
      snippet:
        'The spring garden promotion drove a 3.1x lift in Outdoor & Garden ' +
        'units. The campaign concluded March 31 and was not extended into Q2.',
      score: 0.83,
      uri: '#',
    },
    {
      id: 'doc_501',
      title: 'Product review sentiment — shipping',
      source: 'reviews',
      snippet:
        '“Ordered a patio set, arrived two weeks late and one box was ' +
        'missing.” Shipping sentiment fell from 4.1 to 3.4 stars in the West.',
      score: 0.79,
      uri: '#',
    },
  ],
  caveats: [
    'Q2 figures reflect data through Jun 30; the last two days of June are still settling and may revise upward by ~1%.',
    'Revenue is net of returns and excludes tax and shipping, per the governed `net_revenue` metric.',
  ],
};

/** Turn the flagship envelope into an ordered SSE event sequence for streaming. */
export function mockStreamEvents(envelope: AnswerEnvelope): AskStreamEvent[] {
  const events: AskStreamEvent[] = [];
  events.push({
    type: 'meta',
    data: { conversation_id: 'c_demo', message_id: `m_${Date.now()}` },
  });
  if (envelope.route) {
    events.push({
      type: 'route',
      data: { route: envelope.route, confidence: envelope.confidence },
    });
  }
  // Chunk the narrative into word groups to simulate token streaming.
  const words = envelope.answer.split(' ');
  for (let i = 0; i < words.length; i += 3) {
    const chunk = words.slice(i, i + 3).join(' ');
    events.push({ type: 'token', data: { text: chunk + (i + 3 < words.length ? ' ' : '') } });
  }
  if (envelope.sql) {
    events.push({ type: 'sql', data: { sql: envelope.sql, dialect: envelope.dialect ?? 'postgres' } });
  }
  for (const table of envelope.tables) {
    events.push({ type: 'tables', data: table });
  }
  if (envelope.citations.length) {
    events.push({ type: 'citations', data: { items: envelope.citations } });
  }
  if (envelope.chart_spec) {
    events.push({ type: 'chart', data: { chart_spec: envelope.chart_spec } });
  }
  if (envelope.caveats.length) {
    events.push({ type: 'caveats', data: { items: envelope.caveats } });
  }
  events.push({
    type: 'done',
    data: { message_id: `m_${Date.now()}`, usage: { latency_ms: 3120, tokens: { in: 1840, out: 260 } } },
  });
  return events;
}

/** A generic fallback envelope for questions other than the flagship one. */
export function mockEnvelopeFor(question: string): AnswerEnvelope {
  if (/restock|inventory|reorder/i.test(question)) {
    return {
      answer:
        'Nine SKUs are at elevated stock-out risk within their lead time. The ' +
        'highest priority are the Cordless Drill 18V (6 days of cover, 11-day ' +
        'lead time) and the Trail Running Shoe (8 days cover). Prioritize ' +
        'reorders for the Tools and Footwear categories, which together hold ' +
        'seven of the nine flagged items.',
      route: 'structured',
      confidence: 0.82,
      dialect: 'postgres',
      sql: `SELECT p.sku, p.name, i.on_hand, m.avg_daily_units,
       (i.on_hand / NULLIF(m.avg_daily_units,0)) AS days_of_cover,
       s.lead_time_days
FROM marts.fct_inventory i
JOIN marts.dim_product p ON p.product_key = i.product_key
JOIN marts.mart_sell_through m ON m.product_key = i.product_key
JOIN marts.dim_supplier s ON s.supplier_key = p.supplier_key
WHERE (i.on_hand / NULLIF(m.avg_daily_units,0)) < s.lead_time_days
ORDER BY days_of_cover ASC
LIMIT 1000;`,
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
      caveats: ['Lead times use the supplier default where a SKU-specific value is missing.'],
    };
  }
  if (/complaint|sentiment|voice|review/i.test(question)) {
    return {
      answer:
        'This month’s complaints cluster into three themes: delivery delays ' +
        '(38% of negative contacts), missing or damaged items (24%), and ' +
        'sizing inaccuracy in apparel (17%). Delivery is both the largest and ' +
        'the fastest-growing theme, up 41% versus last month.',
      route: 'unstructured',
      confidence: 0.8,
      sql: null,
      chart_spec: {
        kind: 'pie',
        title: 'Complaint themes (share of negative contacts)',
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
      citations: FLAGSHIP_ENVELOPE.citations.slice(0, 2),
      caveats: ['Themes are derived from clustering; a contact can map to one primary theme only.'],
    };
  }
  return { ...FLAGSHIP_ENVELOPE, answer: FLAGSHIP_ENVELOPE.answer };
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
    message_count: 1,
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
    message_count: 1,
  },
];

export function mockConversation(id: string): Conversation {
  const summary = MOCK_CONVERSATIONS.find((c) => c.id === id) ?? MOCK_CONVERSATIONS[0]!;
  return {
    id: summary.id,
    title: summary.title,
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

export const MOCK_METRICS: MetricDef[] = [
  { key: 'revenue', label: 'Revenue', description: 'Net revenue after returns.', unit: 'currency', grain: ['order_date__month', 'region', 'category'], default_agg: 'sum' },
  { key: 'orders', label: 'Orders', description: 'Distinct completed orders.', unit: 'count', grain: ['order_date__month', 'region', 'category'], default_agg: 'count' },
  { key: 'aov', label: 'Average order value', description: 'Revenue per order.', unit: 'currency', grain: ['order_date__month', 'region'], default_agg: 'avg' },
  { key: 'return_rate', label: 'Return rate', description: 'Returned orders / total orders.', unit: 'ratio', grain: ['order_date__month', 'category'], default_agg: 'ratio' },
];

const KPI_SUMMARIES: Record<string, MetricResult['summary']> = {
  revenue: { value: 3680000, unit: 'currency', delta_pct: -0.124, previous: 4201000 },
  orders: { value: 26240, unit: 'count', delta_pct: -0.11, previous: 29480 },
  aov: { value: 129, unit: 'currency', delta_pct: -0.015, previous: 131 },
  return_rate: { value: 0.068, unit: 'ratio', delta_pct: 0.009, previous: 0.059 },
};

export function mockMetricResult(metric: string): MetricResult {
  const summary = KPI_SUMMARIES[metric];
  const isTrend = metric === 'revenue' || metric === 'orders';
  return {
    columns: [
      { name: 'month', dtype: 'string' },
      { name: metric, dtype: metric === 'return_rate' ? 'ratio' : metric === 'orders' ? 'number' : 'currency' },
    ],
    rows: isTrend
      ? REVENUE_TREND.map((r) => [r.month, metric === 'orders' ? r.orders : r.revenue])
      : [],
    sql: `SELECT date_trunc('month', order_date) AS month, ${metric === 'return_rate' ? 'SUM(returned)::float/COUNT(*)' : metric === 'orders' ? 'COUNT(DISTINCT order_id)' : 'SUM(net_revenue)'} AS ${metric}\nFROM marts.fct_orders\nWHERE order_date >= date '2026-01-01'\nGROUP BY 1 ORDER BY 1;`,
    row_count: isTrend ? REVENUE_TREND.length : 0,
    truncated: false,
    summary,
  };
}

export const MOCK_TREND_DATA = REVENUE_TREND;

export const MOCK_TOP_PRODUCTS = [
  { name: 'Wireless Earbuds Pro', revenue: 412000 },
  { name: 'Ceramic Dutch Oven', revenue: 356000 },
  { name: '4K Streaming Stick', revenue: 298000 },
  { name: 'Trail Running Shoe', revenue: 264000 },
  { name: 'Cordless Drill 18V', revenue: 231000 },
];

export const MOCK_BOTTOM_PRODUCTS = [
  { name: 'Patio Umbrella XL', revenue: 41000 },
  { name: 'Garden Hose 50ft', revenue: 38000 },
  { name: 'Bird Feeder Deluxe', revenue: 22000 },
];

export interface AtRiskRow {
  sku: string;
  name: string;
  category: string;
  days_of_cover: number;
  lead_time_days: number;
  severity: 'high' | 'medium' | 'low';
}

export const MOCK_AT_RISK: AtRiskRow[] = [
  { sku: 'TL-DR18', name: 'Cordless Drill 18V', category: 'Tools', days_of_cover: 6, lead_time_days: 11, severity: 'high' },
  { sku: 'FW-TRS9', name: 'Trail Running Shoe', category: 'Footwear', days_of_cover: 8, lead_time_days: 10, severity: 'high' },
  { sku: 'HK-DO55', name: 'Ceramic Dutch Oven', category: 'Home & Kitchen', days_of_cover: 9, lead_time_days: 12, severity: 'medium' },
  { sku: 'EL-EBP2', name: 'Wireless Earbuds Pro', category: 'Electronics', days_of_cover: 10, lead_time_days: 14, severity: 'medium' },
  { sku: 'OG-PU01', name: 'Patio Umbrella XL', category: 'Outdoor & Garden', days_of_cover: 13, lead_time_days: 15, severity: 'low' },
];

/* -------------------------------------------------------------------------- */
/* Pipelines                                                                  */
/* -------------------------------------------------------------------------- */

export const MOCK_PIPELINES: Pipeline[] = [
  {
    name: 'warehouse_elt',
    description: 'Land raw sources, run dbt transforms to the star schema and semantic marts.',
    schedule: '0 */6 * * *',
    last_run: { id: 'r_1001', status: 'success', started_at: '2026-08-24T06:00:00Z', finished_at: '2026-08-24T06:04:12Z' },
  },
  {
    name: 'document_index',
    description: 'Chunk, embed, and index tickets/reviews/reports into the vector store.',
    schedule: '0 */12 * * *',
    last_run: { id: 'r_1002', status: 'partial', started_at: '2026-08-24T00:00:00Z', finished_at: '2026-08-24T00:09:41Z' },
  },
  {
    name: 'metrics_refresh',
    description: 'Recompute rollups feeding the governed metric layer.',
    schedule: null,
    last_run: { id: 'r_1003', status: 'running', started_at: '2026-08-24T09:10:00Z', finished_at: null },
  },
];

export const MOCK_RUNS: PipelineRun[] = [
  {
    id: 'r_1001',
    pipeline: 'warehouse_elt',
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
    pipeline: 'document_index',
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
      { name: 'upsert_qdrant', rows_in: 18102, rows_out: 18102, ms: 24200 },
    ],
  },
  {
    id: 'r_1003',
    pipeline: 'metrics_refresh',
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
    pipeline: 'warehouse_elt',
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
/* Sources                                                                    */
/* -------------------------------------------------------------------------- */

export const MOCK_SOURCES: Source[] = [
  { id: 's_1', name: 'orders_pg', kind: 'postgres', status: 'ok', last_tested_at: '2026-08-24T06:00:00Z' },
  { id: 's_2', name: 'catalog_csv', kind: 'csv', status: 'ok', last_tested_at: '2026-08-23T18:00:00Z' },
  { id: 's_3', name: 'support_tickets', kind: 'documents', status: 'ok', last_tested_at: '2026-08-24T00:00:00Z' },
  { id: 's_4', name: 'legacy_mysql', kind: 'mysql', status: 'error', last_tested_at: '2026-08-20T12:00:00Z' },
  { id: 's_5', name: 'reviews_export', kind: 'excel', status: 'untested', last_tested_at: null },
];

/* -------------------------------------------------------------------------- */
/* Reports                                                                    */
/* -------------------------------------------------------------------------- */

export const MOCK_REPORTS: ReportSummary[] = [
  { id: 'rep_1', title: 'Q2 2026 Executive Summary', status: 'ready', created_at: '2026-08-24T08:00:00Z' },
  { id: 'rep_2', title: 'July Inventory Health', status: 'ready', created_at: '2026-08-01T08:00:00Z' },
  { id: 'rep_3', title: 'Voice of Customer — August', status: 'generating', created_at: '2026-08-24T09:15:00Z' },
];

export function mockReport(id: string): Report {
  const summary = MOCK_REPORTS.find((r) => r.id === id) ?? MOCK_REPORTS[0]!;
  return {
    id: summary.id,
    status: summary.status === 'generating' ? 'generating' : 'ready',
    title: summary.title,
    period: { grain: 'quarter', start: '2026-04-01', end: '2026-06-30' },
    created_at: summary.created_at,
    blocks: [
      {
        id: 'b_kpi',
        heading: 'Headline metrics',
        prose:
          'Revenue for the period was $3.68M, down 12.4% versus the prior ' +
          'quarter. Orders fell 11% while average order value was essentially ' +
          'flat, indicating a volume-driven decline rather than a pricing or ' +
          'basket-size effect.',
        chart_spec: FLAGSHIP_ENVELOPE.chart_spec,
      },
      {
        id: 'b_sales',
        heading: 'Sales decomposition',
        prose:
          'Two-thirds of the shortfall traces to Outdoor & Garden, where the ' +
          'Q1 seasonal promotion was not repeated. The West region ' +
          'underperformed by 15%, coinciding with elevated delivery-delay ' +
          'complaints after an April carrier change.',
        citations: FLAGSHIP_ENVELOPE.citations,
      },
      {
        id: 'b_voc',
        heading: 'Voice of customer',
        prose:
          'Shipping-related complaints rose 41% quarter-over-quarter, ' +
          'concentrated in the West. Sentiment on delivery fell from 4.1 to ' +
          '3.4 stars. Resolving the carrier issue is the single highest-' +
          'leverage action for Q3 recovery.',
        citations: FLAGSHIP_ENVELOPE.citations.slice(0, 2),
      },
    ],
  };
}

/* -------------------------------------------------------------------------- */
/* System status                                                              */
/* -------------------------------------------------------------------------- */

export const MOCK_STATUS: SystemStatus = {
  status: 'ok',
  services: {
    postgres: { status: 'ok', latency_ms: 4 },
    qdrant: { status: 'ok', latency_ms: 11 },
    worker: { status: 'ok', latency_ms: 2 },
    llm_provider: { status: 'ok', latency_ms: 320 },
  },
  warehouse: { marts_rows: 91240, last_dbt_run: '2026-08-24T06:04:12Z' },
  index: { collection_size: 18102, last_index: '2026-08-24T00:09:41Z' },
  llm: { provider: 'ollama', model: 'llama3.1:8b', reachable: true },
};
