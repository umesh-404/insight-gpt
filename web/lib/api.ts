/**
 * Typed API client for the InsightGPT backend plus an SSE helper for the
 * streaming `/ask` endpoint.
 *
 * A MOCK layer (gated behind NEXT_PUBLIC_USE_MOCK) makes every method resolve
 * from lib/mock.ts so the UI renders end-to-end without a backend. In real mode
 * the same signatures issue fetch calls against NEXT_PUBLIC_API_URL and pass
 * every response through lib/wire.ts.
 *
 * Auth model: the access token is held in memory only; the refresh token lives
 * in the httpOnly `igpt_refresh` cookie the browser sends automatically
 * (`credentials: 'include'`). This module never persists the access token.
 */
import {
  ApiError,
  emptyEnvelope,
  type AccessToken,
  type AnswerEnvelope,
  type ApiErrorBody,
  type AskStreamEvent,
  type Cell,
  type Conversation,
  type ConversationSummary,
  type LoginRequest,
  type MetricQuery,
  type MetricResult,
  type MetricsCatalog,
  type Paginated,
  type Pipeline,
  type PipelineRun,
  type Report,
  type ReportFormat,
  type ReportRequest,
  type ReportSummary,
  type Source,
  type SourceConfig,
  type SourceTestResult,
  type SseUsage,
  type SystemStatus,
  type TokenPair,
  type User,
} from './types';
import * as wire from './wire';
import * as mock from './mock';

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === 'true';

/* -------------------------------------------------------------------------- */
/* In-memory access token                                                     */
/* -------------------------------------------------------------------------- */

let accessToken: string | null = null;
/** Epoch ms at which the current access token expires (0 = unknown). */
let accessTokenExpiresAt = 0;

type TokenListener = (token: string | null, expiresInSeconds: number) => void;
const tokenListeners = new Set<TokenListener>();

export function setAccessToken(token: string | null, expiresIn?: number): void {
  accessToken = token;
  accessTokenExpiresAt =
    token && expiresIn ? Date.now() + expiresIn * 1000 : 0;
  for (const listener of tokenListeners) listener(token, expiresIn ?? 0);
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Seconds until the access token expires; `Infinity` when unknown. */
export function accessTokenTtl(): number {
  if (!accessTokenExpiresAt) return Number.POSITIVE_INFINITY;
  return Math.max(0, (accessTokenExpiresAt - Date.now()) / 1000);
}

/** Subscribe to token changes so the session layer can schedule refreshes. */
export function onAccessToken(listener: TokenListener): () => void {
  tokenListeners.add(listener);
  return () => {
    tokenListeners.delete(listener);
  };
}

/* -------------------------------------------------------------------------- */
/* Core fetch wrapper                                                         */
/* -------------------------------------------------------------------------- */

interface RequestOptions {
  retryOn401?: boolean;
  /** Skip the JSON Accept header (used for binary export downloads). */
  accept?: string;
}

async function rawRequest(
  path: string,
  init: RequestInit = {},
  { retryOn401 = true, accept = 'application/json' }: RequestOptions = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set('Accept', accept);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    // Sends the httpOnly `igpt_refresh` cookie; required for /auth/refresh.
    credentials: 'include',
  });

  // Transparent access-token refresh on 401. `refreshAccessToken` is
  // single-flight, so N concurrent 401s trigger exactly one refresh call.
  if (res.status === 401 && retryOn401 && !path.startsWith('/auth/refresh')) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return rawRequest(path, init, { retryOn401: false, accept });
    }
  }
  return res;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  const res = await rawRequest(path, init, options);
  if (!res.ok) throw new ApiError(res.status, await safeErrorBody(res));
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

async function safeErrorBody(res: Response): Promise<ApiErrorBody> {
  const body = await parseErrorBody(res);
  if (res.status === 429) {
    // Surface the throttle window so the UI can tell the user when to retry.
    const retryAfter = Number(res.headers.get('retry-after'));
    return {
      ...body,
      code: 'rate_limited',
      details: {
        ...(body.details ?? {}),
        ...(Number.isFinite(retryAfter) ? { retry_after: retryAfter } : {}),
        limit: res.headers.get('x-ratelimit-limit'),
        remaining: res.headers.get('x-ratelimit-remaining'),
      },
    };
  }
  return body;
}

async function parseErrorBody(res: Response): Promise<ApiErrorBody> {
  try {
    const json = (await res.json()) as {
      error?: ApiErrorBody;
      detail?: unknown;
    };
    if (json.error?.message) return json.error;
    if (typeof json.detail === 'string') {
      return { code: codeForStatus(res.status), message: json.detail };
    }
    if (Array.isArray(json.detail)) {
      // FastAPI validation errors: surface the first field message.
      const first = json.detail[0] as { loc?: unknown[]; msg?: string } | undefined;
      const field = Array.isArray(first?.loc) ? first?.loc.join('.') : '';
      return {
        code: 'validation_error',
        message: [field, first?.msg].filter(Boolean).join(': ') || 'Request validation failed.',
      };
    }
  } catch {
    /* fall through to a synthetic envelope */
  }
  return {
    code: codeForStatus(res.status),
    message: res.statusText || `Request failed (${res.status})`,
  };
}

function codeForStatus(status: number): string {
  if (status === 400) return 'bad_request';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 422) return 'validation_error';
  if (status === 429) return 'rate_limited';
  if (status === 503) return 'service_unavailable';
  return 'internal_error';
}

/* -------------------------------------------------------------------------- */
/* Single-flight refresh                                                      */
/* -------------------------------------------------------------------------- */

let refreshInFlight: Promise<boolean> | null = null;

/**
 * Exchange the httpOnly refresh cookie for a new access token.
 *
 * Sends **no body** — the cookie is the credential. Concurrent callers share
 * one in-flight request so a burst of 401s cannot fan out into N refreshes
 * (which would rotate the refresh token N times and log the user out).
 */
export function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      if (USE_MOCK) {
        if (!mock.hasMockSession()) return false;
        setAccessToken(mock.MOCK_TOKENS.access_token, mock.MOCK_TOKENS.expires_in);
        return true;
      }
      const token = await request<AccessToken>(
        '/auth/refresh',
        { method: 'POST' },
        { retryOn401: false },
      );
      if (!token?.access_token) return false;
      setAccessToken(token.access_token, token.expires_in);
      return true;
    } catch {
      setAccessToken(null);
      return false;
    } finally {
      // Release on the next microtask so callers awaiting this promise all
      // observe the same result before a new attempt can start.
      queueMicrotask(() => {
        refreshInFlight = null;
      });
    }
  })();
  return refreshInFlight;
}

/* -------------------------------------------------------------------------- */
/* Mock helpers                                                               */
/* -------------------------------------------------------------------------- */

function delay<T>(value: T, ms = 350): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* -------------------------------------------------------------------------- */
/* Governed metric queries                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The `/metrics/query` contract is mid-migration: the target shape takes a
 * `filters` **list** plus `order_by_metric`/`time_grain`, while the deployed
 * build still takes a filters **map** plus `order`. We send the target shape
 * first and, if the server rejects it as malformed, retry once with the legacy
 * shape and remember the answer for the rest of the session.
 */
type MetricsContract = 'unknown' | 'list-filters' | 'map-filters';
let metricsContract: MetricsContract = 'unknown';

function toListQuery(query: MetricQuery): Record<string, unknown> {
  return {
    metric: query.metric,
    ...(query.dimensions?.length ? { dimensions: query.dimensions } : {}),
    ...(query.filters?.length ? { filters: query.filters } : {}),
    ...(query.time_range ? { time_range: query.time_range } : {}),
    ...(query.time_grain ? { time_grain: query.time_grain } : {}),
    ...(query.order_by_metric
      ? {
          order_by_metric: query.order_by_metric,
          // Compatibility alias: a build that only knows `order` ignores the
          // field above and would otherwise return an *unordered* slice — a
          // silently wrong "top N". Servers on the new contract ignore this one.
          order: query.order_by_metric,
        }
      : {}),
    ...(query.limit ? { limit: query.limit } : {}),
  };
}

/** Fields that only exist in the target contract — the discriminating ones. */
function usesListContract(query: MetricQuery): boolean {
  return Boolean(query.filters?.length || query.order_by_metric || query.time_grain);
}

function toMapQuery(query: MetricQuery): Record<string, unknown> {
  const filters: Record<string, string | number | boolean | Array<string | number | boolean>> = {};
  let timeRange = query.time_range ?? null;

  for (const filter of query.filters ?? []) {
    if (filter.op === 'between' && filter.values.length === 2) {
      // A date `between` predicate is expressed as `time_range` in the map form.
      timeRange = {
        ...(timeRange ?? {}),
        start: String(filter.values[0]),
        end: String(filter.values[1]),
      };
      continue;
    }
    if (!filter.values.length) continue;
    filters[filter.dimension] =
      filter.op === 'in' ? filter.values : (filter.values[0] as string | number | boolean);
  }

  const grain = query.time_grain ?? timeRange?.grain ?? null;
  return {
    metric: query.metric,
    ...(query.dimensions?.length ? { dimensions: query.dimensions } : {}),
    ...(Object.keys(filters).length ? { filters } : {}),
    ...(timeRange ? { time_range: { ...timeRange, ...(grain ? { grain } : {}) } } : {}),
    ...(query.order_by_metric ? { order: query.order_by_metric } : {}),
    ...(query.limit ? { limit: query.limit } : {}),
  };
}

/** True when the failure is "you sent the wrong shape", not "your query is invalid". */
function isShapeRejection(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.status !== 422) return false;
  return /filters|order_by_metric|time_grain|extra_forbidden|dictionary|dict_type/i.test(
    err.message,
  );
}

/**
 * Belt-and-braces ordering. `order_by_metric` is honoured server-side, but a
 * build that ignores it would return an arbitrary slice for a "top N" panel —
 * wrong data rendered confidently. Re-sorting a sorted result is a no-op.
 */
function applyOrdering(result: MetricResult, query: MetricQuery): MetricResult {
  if (!query.order_by_metric || result.rows.length < 2) return result;
  const index = result.columns.findIndex((c) => c.name === query.metric);
  const valueIndex = index >= 0 ? index : result.columns.length - 1;
  const direction = query.order_by_metric === 'asc' ? 1 : -1;
  const rows = [...result.rows].sort((a, b) => {
    const av = Number(a[valueIndex] ?? 0);
    const bv = Number(b[valueIndex] ?? 0);
    if (!Number.isFinite(av) || !Number.isFinite(bv)) return 0;
    return (av - bv) * direction;
  });
  return { ...result, rows };
}

async function postMetricQuery(query: MetricQuery): Promise<MetricResult> {
  const send = (body: Record<string, unknown>) =>
    request<unknown>('/metrics/query', {
      method: 'POST',
      body: JSON.stringify(body),
    })
      .then(wire.fromMetricResult)
      .then((result) => applyOrdering(result, query));

  if (metricsContract === 'map-filters') return send(toMapQuery(query));
  try {
    const result = await send(toListQuery(query));
    // A query with no filters/ordering succeeds under BOTH contracts, so it
    // proves nothing — only record the contract when the request actually
    // exercised a discriminating field.
    if (usesListContract(query)) metricsContract = 'list-filters';
    return result;
  } catch (err) {
    if (metricsContract === 'list-filters' || !isShapeRejection(err)) throw err;
    metricsContract = 'map-filters';
    return send(toMapQuery(query));
  }
}

/* -------------------------------------------------------------------------- */
/* Locally tracked reports                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The API exposes `POST /reports` and `GET /reports/{id}` but no list endpoint.
 * Until it does, remember the ids generated in this browser so the Reports
 * screen can show real, fetchable history instead of an empty page.
 */
const REPORT_IDS_KEY = 'igpt-report-ids';

function readReportIds(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(REPORT_IDS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

function rememberReportId(id: string): void {
  if (typeof window === 'undefined' || !id) return;
  try {
    const ids = [id, ...readReportIds().filter((existing) => existing !== id)].slice(0, 50);
    window.localStorage.setItem(REPORT_IDS_KEY, JSON.stringify(ids));
  } catch {
    /* storage unavailable (private mode) — the list just stays empty */
  }
}

function forgetReportId(id: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      REPORT_IDS_KEY,
      JSON.stringify(readReportIds().filter((existing) => existing !== id)),
    );
  } catch {
    /* ignore */
  }
}

/* -------------------------------------------------------------------------- */
/* API surface                                                                */
/* -------------------------------------------------------------------------- */

export const api = {
  /* ---- Auth ------------------------------------------------------------- */

  async login(body: LoginRequest): Promise<{ tokens: TokenPair; user: User }> {
    if (USE_MOCK) {
      if (!body.email || !body.password) {
        throw new ApiError(422, {
          code: 'validation_error',
          message: 'Email and password are required.',
        });
      }
      mock.setMockSession(true);
      setAccessToken(mock.MOCK_TOKENS.access_token, mock.MOCK_TOKENS.expires_in);
      return delay({ tokens: mock.MOCK_TOKENS, user: mock.MOCK_USER });
    }
    const tokens = await request<TokenPair>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    setAccessToken(tokens.access_token, tokens.expires_in);
    return { tokens, user: await api.me() };
  },

  async me(): Promise<User> {
    if (USE_MOCK) return delay(mock.MOCK_USER, 150);
    return wire.fromUser(await request<unknown>('/auth/me'));
  },

  /** Restore a session from the refresh cookie. Returns null when signed out. */
  async restoreSession(): Promise<User | null> {
    const refreshed = await refreshAccessToken();
    if (!refreshed) return null;
    try {
      return await api.me();
    } catch {
      setAccessToken(null);
      return null;
    }
  },

  async logout(): Promise<void> {
    if (USE_MOCK) {
      mock.setMockSession(false);
      setAccessToken(null);
      return delay(undefined, 100);
    }
    try {
      await request<void>('/auth/logout', { method: 'POST' }, { retryOn401: false });
    } finally {
      // Always drop the local token, even if the server call failed.
      setAccessToken(null);
    }
  },

  /* ---- Conversations ---------------------------------------------------- */

  async listConversations(): Promise<Paginated<ConversationSummary>> {
    if (USE_MOCK) {
      return delay({
        items: mock.MOCK_CONVERSATIONS,
        total: mock.MOCK_CONVERSATIONS.length,
        limit: 20,
        offset: 0,
      });
    }
    try {
      const raw = await request<Paginated<ConversationSummary>>(
        '/conversations?limit=20&offset=0',
      );
      return { items: raw?.items ?? [], total: raw?.total ?? 0 };
    } catch (err) {
      // Persistence is optional in some deployments; an absent endpoint means
      // "no history", not "the Ask screen is broken".
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        return { items: [], total: 0 };
      }
      throw err;
    }
  },

  async getConversation(id: string): Promise<Conversation> {
    if (USE_MOCK) return delay(mock.mockConversation(id));
    return wire.fromConversation(
      await request<unknown>(`/conversations/${encodeURIComponent(id)}`),
      id,
    );
  },

  async sendFeedback(
    messageId: string,
    rating: 'up' | 'down',
    reason?: string,
  ): Promise<void> {
    if (USE_MOCK) return delay(undefined, 200);
    await request<void>('/feedback', {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId, rating, reason }),
    });
  },

  /** Non-streaming `/ask` — used to verify stream/batch envelope parity. */
  async ask(question: string, conversationId?: string): Promise<AnswerEnvelope> {
    if (USE_MOCK) return delay(mock.mockEnvelopeFor(question));
    return wire.fromEnvelope(
      await request<unknown>('/ask', {
        method: 'POST',
        body: JSON.stringify({
          question,
          conversation_id: conversationId ?? null,
          stream: false,
        }),
      }),
    );
  },

  /* ---- Dashboards & metrics --------------------------------------------- */

  async listMetrics(): Promise<MetricsCatalog> {
    if (USE_MOCK) return delay(mock.MOCK_CATALOG);
    return wire.fromMetricsCatalog(await request<unknown>('/metrics'));
  },

  async queryMetric(query: MetricQuery): Promise<MetricResult> {
    if (USE_MOCK) return delay(mock.mockMetricResult(query));
    return postMetricQuery(query);
  },

  /* ---- Pipelines -------------------------------------------------------- */

  async listPipelines(): Promise<Pipeline[]> {
    if (USE_MOCK) return delay(mock.MOCK_PIPELINES);
    const raw = await request<Pipeline[]>('/pipelines');
    return Array.isArray(raw) ? raw : [];
  },

  async listRuns(pipeline?: string): Promise<PipelineRun[]> {
    if (USE_MOCK) {
      return delay(
        pipeline ? mock.MOCK_RUNS.filter((r) => r.pipeline === pipeline) : mock.MOCK_RUNS,
      );
    }
    const query = pipeline ? `?pipeline=${encodeURIComponent(pipeline)}` : '';
    const raw = await request<PipelineRun[]>(`/pipeline-runs${query}`);
    return Array.isArray(raw) ? raw : [];
  },

  async getRun(id: string): Promise<PipelineRun> {
    if (USE_MOCK) return delay(mock.mockRun(id));
    const run = await request<PipelineRun>(`/pipeline-runs/${encodeURIComponent(id)}`);
    return {
      ...run,
      stages: run.stages ?? [],
      row_counts: run.row_counts ?? {},
    };
  },

  async triggerRun(pipeline: string): Promise<{ run_id: string; status: string }> {
    if (USE_MOCK) return delay({ run_id: `r_${Date.now()}`, status: 'queued' });
    return request<{ run_id: string; status: string }>(
      `/pipelines/${encodeURIComponent(pipeline)}/run`,
      { method: 'POST' },
    );
  },

  /* ---- Sources ---------------------------------------------------------- */

  async listSources(): Promise<Source[]> {
    if (USE_MOCK) return delay(mock.listMockSources());
    const raw = await request<Source[]>('/sources');
    return Array.isArray(raw) ? raw : [];
  },

  async createSource(config: SourceConfig): Promise<Source> {
    if (USE_MOCK) return delay(mock.addMockSource(config), 500);
    return request<Source>('/sources', {
      method: 'POST',
      body: JSON.stringify({
        name: config.name,
        kind: config.kind,
        ...(config.dsn ? { dsn: config.dsn } : {}),
        ...(config.options ? { options: config.options } : {}),
      }),
    });
  },

  async deleteSource(id: string): Promise<void> {
    if (USE_MOCK) {
      mock.removeMockSource(id);
      return delay(undefined, 300);
    }
    await request<void>(`/sources/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },

  async testSource(id: string): Promise<SourceTestResult> {
    if (USE_MOCK) {
      return delay(
        { ok: true, latency_ms: 42, tables_seen: 14, message: 'Connection OK' },
        800,
      );
    }
    return request<SourceTestResult>(`/sources/${encodeURIComponent(id)}/test`, {
      method: 'POST',
    });
  },

  /* ---- Reports ---------------------------------------------------------- */

  async listReports(): Promise<ReportSummary[]> {
    if (USE_MOCK) return delay(mock.MOCK_REPORTS);
    try {
      const raw = await request<ReportSummary[] | Paginated<ReportSummary>>('/reports');
      const items = Array.isArray(raw) ? raw : (raw?.items ?? []);
      for (const item of items) rememberReportId(item.id);
      return items;
    } catch (err) {
      if (
        !(err instanceof ApiError) ||
        (err.status !== 404 && err.status !== 405)
      ) {
        throw err;
      }
    }
    // No list endpoint: rebuild history from ids created in this browser.
    const ids = readReportIds();
    const settled = await Promise.allSettled(ids.map((id) => api.getReport(id)));
    return settled.flatMap((outcome, i) => {
      if (outcome.status === 'fulfilled') return [wire.reportSummary(outcome.value)];
      const id = ids[i];
      // Drop ids the server no longer knows about so the list self-heals.
      if (id && outcome.reason instanceof ApiError && outcome.reason.status === 404) {
        forgetReportId(id);
      }
      return [];
    });
  },

  async getReport(id: string): Promise<Report> {
    if (USE_MOCK) return delay(mock.mockReport(id));
    return wire.fromReport(
      await request<unknown>(`/reports/${encodeURIComponent(id)}`),
      id,
    );
  },

  async createReport(body: ReportRequest): Promise<{ report_id: string; status: string }> {
    if (USE_MOCK) return delay({ report_id: mock.addMockReport(body), status: 'generating' });
    const handle = await request<{ report_id: string; status: string }>('/reports', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    rememberReportId(handle.report_id);
    return handle;
  },

  reportExportUrl(id: string, format: ReportFormat = 'pdf'): string {
    return `${API_URL}/reports/${encodeURIComponent(id)}/export?format=${format}`;
  },

  /**
   * Download a report export. The endpoint is bearer-authenticated, so it
   * cannot be opened as a plain link — fetch it, then hand the browser a blob.
   */
  async downloadReport(
    id: string,
    format: ReportFormat = 'pdf',
    filenameHint?: string,
  ): Promise<void> {
    if (USE_MOCK) {
      await delay(undefined, 300);
      triggerDownload(
        new Blob([mock.mockReportMarkdown(id)], { type: 'text/markdown' }),
        `${filenameHint ?? id}.md`,
      );
      return;
    }
    const res = await rawRequest(
      `/reports/${encodeURIComponent(id)}/export?format=${format}`,
      { method: 'GET' },
      { accept: format === 'pdf' ? 'application/pdf' : 'text/markdown' },
    );
    if (!res.ok) throw new ApiError(res.status, await safeErrorBody(res));

    const blob = await res.blob();
    const disposition = res.headers.get('content-disposition') ?? '';
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    const fallback = `${slugify(filenameHint ?? id)}.${format === 'pdf' ? 'pdf' : 'md'}`;
    triggerDownload(blob, match?.[1] ? decodeURIComponent(match[1]) : fallback);
  },

  /* ---- System ----------------------------------------------------------- */

  async status(): Promise<SystemStatus> {
    if (USE_MOCK) return delay(mock.MOCK_STATUS);
    // /status is unversioned (outside /api/v1).
    const base = API_URL.replace(/\/api\/v1\/?$/, '');
    const res = await fetch(`${base}/status`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
      credentials: 'include',
    });
    if (!res.ok) throw new ApiError(res.status, await safeErrorBody(res));
    return wire.fromStatus(await res.json());
  },
};

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60) || 'report'
  );
}

function triggerDownload(blob: Blob, filename: string): void {
  if (typeof window === 'undefined') return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking immediately can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/* -------------------------------------------------------------------------- */
/* SSE — streaming /ask                                                       */
/* -------------------------------------------------------------------------- */

export interface AskOptions {
  conversationId?: string;
  signal?: AbortSignal;
  onEvent: (event: AskStreamEvent) => void;
}

/**
 * Open the `/ask` SSE stream and dispatch typed events as they arrive.
 * In mock mode, replays the flagship (or topic-matched) envelope with
 * realistic inter-token pacing.
 *
 * Errors are delivered as an `error` event rather than thrown, so a caller
 * that only listens to `onEvent` can never be left with a stuck spinner.
 */
export async function streamAsk(
  question: string,
  opts: AskOptions,
  /** Internal: guards the single 401 → refresh → replay retry. */
  { allowRetry = true }: { allowRetry?: boolean } = {},
): Promise<void> {
  if (USE_MOCK) {
    const envelope = mock.mockEnvelopeFor(question);
    for (const event of mock.mockStreamEvents(envelope)) {
      if (opts.signal?.aborted) return;
      await new Promise((r) => setTimeout(r, event.type === 'token' ? 55 : 240));
      if (opts.signal?.aborted) return;
      opts.onEvent(event);
    }
    return;
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

  let res: Response;
  try {
    res = await fetch(`${API_URL}/ask`, {
      method: 'POST',
      headers,
      credentials: 'include',
      signal: opts.signal,
      body: JSON.stringify({
        question,
        conversation_id: opts.conversationId ?? null,
        stream: true,
      }),
    });
  } catch (err) {
    if (opts.signal?.aborted) return;
    opts.onEvent({
      type: 'error',
      data: {
        code: 'network_error',
        message: err instanceof Error ? err.message : 'Could not reach the API.',
      },
    });
    return;
  }

  // A 401 here means the access token expired between render and submit.
  // Share the same single-flight refresh `request()` uses, then replay once.
  if (res.status === 401 && allowRetry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) return streamAsk(question, opts, { allowRetry: false });
  }

  if (!res.ok || !res.body) {
    opts.onEvent({ type: 'error', data: await safeErrorBody(res) });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // SSE framing: records separated by a blank line, each with an `event:` name
  // and one or more `data:` lines.
  const flush = (record: string) => {
    const trimmed = record.trim();
    if (!trimmed || trimmed.startsWith(':')) return; // heartbeat comment
    let eventName = 'message';
    const dataLines: string[] = [];
    for (const line of trimmed.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    let data: unknown;
    try {
      data = JSON.parse(dataLines.join('\n'));
    } catch {
      return; // malformed frame — skip rather than kill the stream
    }
    const event = coerceEvent(eventName, data);
    if (event) opts.onEvent(event);
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        flush(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) flush(buffer);
  } catch (err) {
    if (opts.signal?.aborted) return;
    opts.onEvent({
      type: 'error',
      data: {
        code: 'stream_error',
        message: err instanceof Error ? err.message : 'The answer stream ended unexpectedly.',
      },
    });
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

/** Map a raw SSE frame onto the typed event union; unknown names are dropped. */
export function coerceEvent(name: string, data: unknown): AskStreamEvent | null {
  const payload = (data ?? {}) as Record<string, unknown>;
  switch (name) {
    case 'meta':
      return {
        type: 'meta',
        data: {
          conversation_id: String(payload.conversation_id ?? ''),
          message_id: String(payload.message_id ?? ''),
        },
      };
    case 'token':
      return { type: 'token', data: { text: String(payload.text ?? '') } };
    case 'sql':
      return {
        type: 'sql',
        data: {
          sql: String(payload.sql ?? ''),
          dialect: typeof payload.dialect === 'string' ? payload.dialect : undefined,
        },
      };
    case 'tables':
    case 'table':
      return { type: 'tables', data: wire.fromTable(payload) };
    case 'citations':
      return { type: 'citations', data: { items: wire.fromCitations(payload.items) } };
    case 'chart': {
      const raw = payload.chart_spec ?? payload.chart ?? payload;
      // Tables may still be streaming, so `data_ref` is resolved by the
      // accumulator; keep the raw payload for that second pass.
      return { type: 'chart', data: { chart_spec: wire.fromChart(raw), raw } };
    }
    case 'caveats':
      return {
        type: 'caveats',
        data: {
          items: (Array.isArray(payload.items) ? payload.items : []).map((c) => String(c)),
        },
      };
    case 'route': {
      const route = wire.toRoute(payload.route);
      if (!route) return null;
      return {
        type: 'route',
        data: { route, confidence: wire.toConfidence(payload.confidence) },
      };
    }
    case 'clarify':
      return {
        type: 'clarify',
        data: { question: String(payload.question ?? payload.clarifying_question ?? '') },
      };
    case 'done':
      return {
        type: 'done',
        data: {
          message_id:
            typeof payload.message_id === 'string' ? payload.message_id : undefined,
          usage: normalizeUsage(payload.usage),
        },
      };
    case 'error':
      return {
        type: 'error',
        data: {
          code: String(payload.code ?? 'internal_error'),
          message: String(payload.message ?? 'The answer could not be completed.'),
          request_id:
            typeof payload.request_id === 'string' ? payload.request_id : undefined,
        },
      };
    default:
      return null;
  }
}

function normalizeUsage(raw: unknown): SseUsage {
  const usage = (raw ?? {}) as Record<string, unknown>;
  const tokens = (usage.tokens ?? null) as { in?: unknown; out?: unknown } | null;
  return {
    latency_ms: typeof usage.latency_ms === 'number' ? usage.latency_ms : undefined,
    confidence: wire.toConfidence(usage.confidence),
    tokens: tokens
      ? { in: Number(tokens.in ?? 0), out: Number(tokens.out ?? 0) }
      : undefined,
  };
}

/* -------------------------------------------------------------------------- */
/* Envelope reconstruction from a stream                                      */
/* -------------------------------------------------------------------------- */

/** Split the `";\n\n"`-joined SQL payload back into individual statements. */
function splitStatements(sql: string): string[] {
  return sql
    .split(/;\s*\n\s*\n/)
    .map((statement) => statement.trim().replace(/;$/, ''))
    .filter(Boolean);
}

/**
 * Folds SSE events into a complete `AnswerEnvelope`, so a streamed answer is
 * byte-identical to the same answer fetched with `stream: false`.
 *
 * Every event type contributes: tokens append, `tables` **appends** (multiple
 * tables per answer are normal), and `chart` is re-resolved against the tables
 * collected so far because the backend references them by `data_ref`.
 */
export class EnvelopeAccumulator {
  private rawChart: unknown = null;

  envelope: AnswerEnvelope = emptyEnvelope();

  conversationId: string | null = null;

  messageId: string | null = null;

  /** Set when an `error` frame arrived mid-stream. */
  error: ApiErrorBody | null = null;

  done = false;

  apply(event: AskStreamEvent): AnswerEnvelope {
    const next: AnswerEnvelope = { ...this.envelope };
    switch (event.type) {
      case 'meta':
        this.conversationId = event.data.conversation_id || this.conversationId;
        this.messageId = event.data.message_id || this.messageId;
        break;
      case 'token':
        next.answer += event.data.text;
        break;
      case 'sql':
        // One `sql` frame carries every statement joined by ";\n\n" — replace
        // rather than append, and split so each statement renders on its own.
        next.sql = splitStatements(event.data.sql);
        if (event.data.dialect) next.dialect = event.data.dialect;
        break;
      case 'tables':
        next.tables = [...next.tables, event.data];
        break;
      case 'citations':
        next.citations = event.data.items;
        break;
      case 'chart':
        this.rawChart = event.data.raw;
        break;
      case 'caveats':
        next.caveats = event.data.items;
        break;
      case 'route':
        next.route = event.data.route;
        if (event.data.confidence) next.confidence = event.data.confidence;
        break;
      case 'clarify':
        next.clarifying_question = event.data.question;
        break;
      case 'done':
        this.done = true;
        this.messageId = event.data.message_id ?? this.messageId;
        if (event.data.usage?.confidence) next.confidence = event.data.usage.confidence;
        break;
      case 'error':
        this.error = event.data;
        this.done = true;
        break;
    }
    // Re-resolve the chart on every update: `data_ref` may point at a table
    // that only arrives after the chart frame.
    if (this.rawChart) next.chart_spec = wire.fromChart(this.rawChart, next.tables);
    this.envelope = next;
    return next;
  }
}

/** Assemble a finished event list into an envelope (parity with batch `/ask`). */
export function assembleEnvelope(events: AskStreamEvent[]): AnswerEnvelope {
  const acc = new EnvelopeAccumulator();
  for (const event of events) acc.apply(event);
  return acc.envelope;
}

export type { Cell };
