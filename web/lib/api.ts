/**
 * Typed API client for the InsightGPT backend (docs/06-api.md) plus an SSE
 * helper for the streaming `/ask` endpoint.
 *
 * A MOCK layer (gated behind NEXT_PUBLIC_USE_MOCK) makes every method resolve
 * from lib/mock.ts so the UI renders end-to-end without a backend. In real
 * mode the same signatures issue fetch calls against NEXT_PUBLIC_API_URL.
 *
 * Auth model (docs/07-frontend.md §6.2): the access token is held in memory;
 * the refresh token lives in an httpOnly cookie the browser sends automatically
 * (`credentials: 'include'`). This module never persists the access token to
 * localStorage.
 */
import {
  ApiError,
  type AnswerEnvelope,
  type ApiErrorBody,
  type AskStreamEvent,
  type Conversation,
  type ConversationSummary,
  type LoginRequest,
  type MetricDef,
  type MetricQuery,
  type MetricResult,
  type Paginated,
  type Pipeline,
  type PipelineRun,
  type Report,
  type ReportRequest,
  type ReportSummary,
  type Source,
  type SystemStatus,
  type TokenPair,
  type User,
} from './types';
import * as mock from './mock';

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === 'true';

/* -------------------------------------------------------------------------- */
/* In-memory access token                                                     */
/* -------------------------------------------------------------------------- */

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/* -------------------------------------------------------------------------- */
/* Core fetch wrapper                                                         */
/* -------------------------------------------------------------------------- */

async function request<T>(
  path: string,
  init: RequestInit = {},
  { retryOn401 = true }: { retryOn401?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });

  // Transparent access-token refresh on 401 (docs/06-api.md §2.1).
  if (res.status === 401 && retryOn401 && path !== '/auth/refresh') {
    const refreshed = await tryRefresh();
    if (refreshed) return request<T>(path, init, { retryOn401: false });
  }

  if (!res.ok) {
    const body = await safeErrorBody(res);
    throw new ApiError(res.status, body);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function safeErrorBody(res: Response): Promise<ApiErrorBody> {
  try {
    const json = (await res.json()) as { error?: ApiErrorBody };
    if (json.error) return json.error;
  } catch {
    /* fall through to a synthetic envelope */
  }
  return {
    code: res.status === 401 ? 'unauthorized' : 'internal_error',
    message: res.statusText || 'Request failed',
  };
}

async function tryRefresh(): Promise<boolean> {
  try {
    const pair = await request<TokenPair>(
      '/auth/refresh',
      { method: 'POST' },
      { retryOn401: false },
    );
    setAccessToken(pair.access_token);
    return true;
  } catch {
    setAccessToken(null);
    return false;
  }
}

/* -------------------------------------------------------------------------- */
/* Mock helpers                                                               */
/* -------------------------------------------------------------------------- */

function delay<T>(value: T, ms = 350): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/* -------------------------------------------------------------------------- */
/* Auth                                                                       */
/* -------------------------------------------------------------------------- */

export const api = {
  async login(body: LoginRequest): Promise<{ tokens: TokenPair; user: User }> {
    if (USE_MOCK) {
      if (!body.email || !body.password) {
        throw new ApiError(422, { code: 'validation_error', message: 'Email and password are required.' });
      }
      setAccessToken(mock.MOCK_TOKENS.access_token);
      return delay({ tokens: mock.MOCK_TOKENS, user: mock.MOCK_USER });
    }
    const tokens = await request<TokenPair>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    setAccessToken(tokens.access_token);
    const user = await request<User>('/auth/me');
    return { tokens, user };
  },

  async me(): Promise<User> {
    if (USE_MOCK) return delay(mock.MOCK_USER, 150);
    return request<User>('/auth/me');
  },

  async logout(): Promise<void> {
    setAccessToken(null);
    if (USE_MOCK) return delay(undefined, 100);
    await request<void>('/auth/logout', { method: 'POST' });
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
    return request<Paginated<ConversationSummary>>('/conversations?limit=20&offset=0');
  },

  async getConversation(id: string): Promise<Conversation> {
    if (USE_MOCK) return delay(mock.mockConversation(id));
    return request<Conversation>(`/conversations/${encodeURIComponent(id)}`);
  },

  async sendFeedback(messageId: string, rating: 'up' | 'down', reason?: string): Promise<void> {
    if (USE_MOCK) return delay(undefined, 200);
    await request<void>('/feedback', {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId, rating, reason }),
    });
  },

  /* ---- Dashboards & metrics --------------------------------------------- */

  async listMetrics(): Promise<MetricDef[]> {
    if (USE_MOCK) return delay(mock.MOCK_METRICS);
    return request<MetricDef[]>('/metrics');
  },

  async queryMetric(query: MetricQuery): Promise<MetricResult> {
    if (USE_MOCK) return delay(mock.mockMetricResult(query.metric));
    return request<MetricResult>('/metrics/query', {
      method: 'POST',
      body: JSON.stringify(query),
    });
  },

  /* ---- Pipelines -------------------------------------------------------- */

  async listPipelines(): Promise<Pipeline[]> {
    if (USE_MOCK) return delay(mock.MOCK_PIPELINES);
    return request<Pipeline[]>('/pipelines');
  },

  async listRuns(): Promise<PipelineRun[]> {
    if (USE_MOCK) return delay(mock.MOCK_RUNS);
    return request<PipelineRun[]>('/pipeline-runs');
  },

  async getRun(id: string): Promise<PipelineRun> {
    if (USE_MOCK) return delay(mock.mockRun(id));
    return request<PipelineRun>(`/pipeline-runs/${encodeURIComponent(id)}`);
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
    if (USE_MOCK) return delay(mock.MOCK_SOURCES);
    return request<Source[]>('/sources');
  },

  async testSource(id: string): Promise<{ ok: boolean; latency_ms: number; tables_seen: number; message: string }> {
    if (USE_MOCK) {
      return delay({ ok: true, latency_ms: 42, tables_seen: 14, message: 'Connection OK' }, 800);
    }
    return request(`/sources/${encodeURIComponent(id)}/test`, { method: 'POST' });
  },

  /* ---- Reports ---------------------------------------------------------- */

  async listReports(): Promise<ReportSummary[]> {
    if (USE_MOCK) return delay(mock.MOCK_REPORTS);
    return request<ReportSummary[]>('/reports');
  },

  async getReport(id: string): Promise<Report> {
    if (USE_MOCK) return delay(mock.mockReport(id));
    return request<Report>(`/reports/${encodeURIComponent(id)}`);
  },

  async createReport(body: ReportRequest): Promise<{ report_id: string; status: string }> {
    if (USE_MOCK) return delay({ report_id: `rep_${Date.now()}`, status: 'generating' });
    return request<{ report_id: string; status: string }>('/reports', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  reportExportUrl(id: string): string {
    return `${API_URL}/reports/${encodeURIComponent(id)}/export?format=pdf`;
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
    return (await res.json()) as SystemStatus;
  },
};

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
 */
export async function streamAsk(question: string, opts: AskOptions): Promise<void> {
  if (USE_MOCK) {
    const envelope = mock.mockEnvelopeFor(question);
    const events = mock.mockStreamEvents(envelope);
    for (const event of events) {
      if (opts.signal?.aborted) return;
      // Tokens stream quickly; structured events get a small beat.
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

  const res = await fetch(`${API_URL}/ask`, {
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

  if (!res.ok || !res.body) {
    const body = await safeErrorBody(res);
    opts.onEvent({ type: 'error', data: body });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // Parse the SSE framing: records separated by a blank line, each with an
  // `event:` name and one or more `data:` lines.
  const flush = (raw: string) => {
    const record = raw.trim();
    if (!record || record.startsWith(':')) return; // heartbeat comment
    let eventName = 'message';
    const dataLines: string[] = [];
    for (const line of record.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    try {
      const data = JSON.parse(dataLines.join('\n'));
      opts.onEvent(coerceEvent(eventName, data));
    } catch {
      /* ignore malformed frame */
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const record = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      flush(record);
    }
  }
  if (buffer.trim()) flush(buffer);
}

function coerceEvent(name: string, data: unknown): AskStreamEvent {
  switch (name) {
    case 'meta':
    case 'token':
    case 'sql':
    case 'tables':
    case 'citations':
    case 'chart':
    case 'caveats':
    case 'route':
    case 'done':
    case 'error':
      return { type: name, data } as AskStreamEvent;
    default:
      return { type: 'token', data: { text: '' } };
  }
}

/** Assemble streamed events into a complete envelope (parity with §6 rules). */
export function assembleEnvelope(events: AskStreamEvent[]): AnswerEnvelope {
  const envelope: AnswerEnvelope = {
    answer: '',
    sql: null,
    tables: [],
    citations: [],
    chart_spec: null,
    caveats: [],
  };
  for (const event of events) {
    switch (event.type) {
      case 'token':
        envelope.answer += event.data.text;
        break;
      case 'sql':
        envelope.sql = event.data.sql;
        envelope.dialect = event.data.dialect;
        break;
      case 'tables':
        envelope.tables.push(event.data);
        break;
      case 'citations':
        envelope.citations = event.data.items;
        break;
      case 'chart':
        envelope.chart_spec = event.data.chart_spec;
        break;
      case 'caveats':
        envelope.caveats = event.data.items;
        break;
      case 'route':
        envelope.route = event.data.route;
        envelope.confidence = event.data.confidence;
        break;
      default:
        break;
    }
  }
  return envelope;
}
