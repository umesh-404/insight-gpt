# InsightGPT — Frontend (`web/`)

The demo-facing surface of InsightGPT: a conversational analytics workspace,
interactive dashboards, data-source and pipeline administration, and executive
report generation. Built with **Next.js (App Router) + TypeScript + Tailwind +
shadcn/ui**, consuming the API described in [`../docs/06-api.md`](../docs/06-api.md).

Design system, routes, and screens follow
[`../docs/07-frontend.md`](../docs/07-frontend.md) (authoritative).

## Quick start

```bash
cd web
pnpm install          # or npm install / yarn
cp .env.example .env.local
pnpm dev              # http://localhost:3000
```

The app boots into **mock mode** by default (`NEXT_PUBLIC_USE_MOCK=true`), so
every screen renders — and the flagship question streams token-by-token —
without a running backend. On the login screen, any credentials sign you in.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000/api/v1` | Base URL of the FastAPI backend (include the `/api/v1` prefix). |
| `NEXT_PUBLIC_USE_MOCK` | `true` | When `true`, serve fixtures from `lib/mock.ts`. Set `false` to talk to the real API. |

### Mock mode vs. live mode

- **Mock (`true`)** — `lib/api.ts` resolves every method from `lib/mock.ts`, and
  `streamAsk` replays a realistic answer envelope for the three flagship
  questions with lifelike token pacing. Great for demos, screenshots, and UI
  work.
- **Live (`false`)** — the same typed client issues `fetch` calls against
  `NEXT_PUBLIC_API_URL`, consumes the real `/ask` SSE stream, and refreshes the
  access token transparently on `401`. No component code changes between modes.

## Scripts

| Command | What it does |
|---|---|
| `pnpm dev` | Start the dev server. |
| `pnpm build` | Production build. |
| `pnpm start` | Serve the production build. |
| `pnpm lint` | ESLint (Next core-web-vitals + Prettier). |
| `pnpm typecheck` | `tsc --noEmit` (strict). |
| `pnpm format` | Prettier write. |

## Architecture

```
web/
  app/
    (auth)/login/            # unauthenticated shell
    (app)/                   # guarded shell (sidebar, top bar, auth guard)
      ask/                   # Ask / Chat — streaming, reveal tabs, history
      dashboards/            # KPI tiles, trends, top products, at-risk
      pipelines/             # run history, triggers, run detail
      reports/               # generate, preview, export
      sources/               # data-source admin (admin only)
      settings/              # profile, theme, provider status (admin)
    layout.tsx, providers.tsx, globals.css
  components/
    ui/                      # shadcn-style primitives (Button, Card, Tabs, …)
    layout/                  # app shell, sidebar nav, theme toggle
    *.tsx                    # domain components (see below)
  lib/
    api.ts                   # typed client + SSE helper (+ mock gating)
    mock.ts                  # realistic fixtures for every screen
    types.ts                 # answer envelope + API types (mirror OpenAPI)
    hooks.ts                 # React Query hooks
    auth.tsx, theme.tsx      # cross-cutting context
    chart-colors.ts, utils.ts
```

### Route ↔ role (docs/07 §3.1)

| Route | Min role | Notes |
|---|---|---|
| `/ask`, `/dashboards`, `/reports` (read) | viewer | report generation is analyst+ |
| `/pipelines` | analyst | "Run now" trigger gated to admin |
| `/sources` | admin | hidden from nav for lower roles |
| `/settings` | viewer | provider/status shown to admin only |

Nav items a role can't use are hidden, and the API re-checks every call — the
client gate is UX, not the security boundary.

### Key domain components

| Component | Responsibility |
|---|---|
| `ConversationView` / `InsightCard` | Streamed answer + reveal tabs (Answer / Chart / SQL / Sources) |
| `ChartRenderer` | Maps a declarative `chart_spec` → Recharts, colored from `--chart-*` tokens, with an accessible data-table toggle |
| `SqlViewer` | Read-only, highlighted, copyable governed SQL |
| `CitationList` / `CaveatNote` | Grounding documents and assumption strips |
| `StatTile` / `TrendChart` | KPI tiles and time-series driven by the metric layer |
| `PipelineRunTable` / `StatusBadge` | Run history and status semantics |
| `InventoryAtRiskTable` | At-risk SKUs by sell-through vs. lead time |
| `ReportPreview` | Paged, PDF-like report layout |

## Design system

A single set of semantic CSS variables in `app/globals.css` drives both themes
and every chart series (`--background`, `--card`, `--primary`, `--success`,
`--chart-1…6`, …). Components consume tokens through Tailwind and never hardcode
hex. Dark/light is a class on `<html>`, set before paint to avoid a flash.

## Accessibility

- Keyboard-reachable controls with a visible `--ring` focus state.
- Radix-based primitives (Tabs) supply correct roles and focus behavior.
- Charts never rely on color alone — every chart offers a data-table
  alternative and a text caption.
- Streamed answers use an `aria-live` region; `prefers-reduced-motion` is
  respected for the typing caret and transitions.

## Notes on auth in this package

Per docs/07 §6.2, production keeps the access token in memory and the refresh
token in an httpOnly cookie, with the `(app)` layout validated server-side. This
standalone frontend implements the in-memory access token and transparent
refresh, plus a lightweight client session marker so the login flow and session
restore work in mock mode without a backend. Swapping `NEXT_PUBLIC_USE_MOCK` to
`false` points the identical client at the real endpoints.
