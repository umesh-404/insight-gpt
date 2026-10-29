# Deploying InsightGPT: Backend on Hugging Face Spaces & Frontend on Vercel

This guide walks you through hosting the **FastAPI backend** on **Hugging Face Spaces** (free Docker container) and deploying the **Next.js frontend** on **Vercel**.

---

## Architecture Overview

```
┌─────────────────────────────────┐          ┌───────────────────────────────────┐
│       Frontend on Vercel        │          │    Backend on Hugging Face Space  │
│  https://your-app.vercel.app    │          │  https://<user>-<space>.hf.space  │
│                                 │          │                                   │
│  - Next.js 14 App Router        │ ───────► │  - FastAPI Application            │
│  - Recharts & Tailwind CSS      │  HTTPS/  │  - DuckDB / Postgres Warehouse    │
│  - SSE Streaming & Attachments  │   SSE    │  - Gemini 2.5 Flash Synthesis     │
│  - NEXT_PUBLIC_API_URL          │          │  - File Attachment Parsers        │
└─────────────────────────────────┘          └───────────────────────────────────┘
```

---

## Part 1: Deploy Backend on Hugging Face Spaces

### Step 1: Create a Space on Hugging Face
1. Go to [Hugging Face: Create a new Space](https://huggingface.co/new-space).
2. Set your Space settings:
   - **Space name**: `insight-gpt-api` (or your preferred name)
   - **License**: `mit` or `apache-2.0`
   - **Space SDK**: **Docker** (Blank)
   - **Space hardware**: **CPU Basic • 2 vCPU • 16GB RAM** (Free)
3. Click **Create Space**.

### Step 2: Push the Backend to your Space
You can push using the included deployment script:

```bash
# Option A: With an HF User Access Token (Automated)
# Generate a Write token at: https://huggingface.co/settings/tokens
python scripts/deploy_hf.py --space <your-hf-username>/insight-gpt-api --push --token <your-hf-token>

# Option B: Git push manually
python scripts/deploy_hf.py --space <your-hf-username>/insight-gpt-api
cd dist/hf-space
git init
git checkout -b main
git remote add origin https://huggingface.co/spaces/<your-hf-username>/insight-gpt-api
git add .
git commit -m "Deploy InsightGPT backend"
git push -u origin main --force
```

### Step 3: Configure Space Secrets & Variables
In your Hugging Face Space, click **Settings** ➔ **Variables and secrets**:

#### Add Secrets (New secret):
| Name | Value | Description |
|---|---|---|
| `GEMINI_API_KEY` | `AQ.Ab8RN...` | Your Google Gemini API Key |
| `JWT_SECRET` | `your-secure-random-secret` | Any long random secret for JWT signing |

#### Add Variables (New variable):
| Name | Value | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | LLM provider |
| `LLM_MODEL` | `gemini-2.5-flash` | Gemini model |
| `WAREHOUSE` | `duckdb` | In-memory governed fixture warehouse |
| `COOKIE_SECURE` | `true` | Required for HTTPS cross-site cookies |
| `COOKIE_SAMESITE` | `none` | Allows Vercel frontend to authenticate with HF backend |
| `CORS_ORIGIN_REGEX` | `^https://.*\.vercel\.app$` | Permits all Vercel deployments |

Once saved, Hugging Face will automatically build your Docker container. Your API will be live at:
`https://<your-hf-username>-insight-gpt-api.hf.space`

Verify it is running:
- Health check: `https://<your-hf-username>-insight-gpt-api.hf.space/health`
- Interactive API Docs: `https://<your-hf-username>-insight-gpt-api.hf.space/api/v1/docs`

---

## Part 2: Deploy Frontend on Vercel

### Option A: Via Vercel Web Dashboard (Recommended)
1. Go to [Vercel Dashboard](https://vercel.com/new).
2. Connect your GitHub account and import the repository: `umesh-404/insight-gpt`.
3. In the project configuration screen:
   - **Framework Preset**: `Next.js`
   - **Root Directory**: Click `Edit` and select `web`
   - **Build Command**: `npm run build`
   - **Output Directory**: `.next`
4. Expand **Environment Variables** and add:
   - `NEXT_PUBLIC_API_URL` = `https://<your-hf-username>-insight-gpt-api.hf.space/api/v1`
   - `NEXT_PUBLIC_USE_MOCK` = `false`
5. Click **Deploy**.

### Option B: Via Vercel CLI
If you prefer deploying from your terminal:

```bash
cd web

# 1. Login to Vercel
vercel login

# 2. Link & Deploy to production
vercel --prod --env NEXT_PUBLIC_API_URL="https://<your-hf-username>-insight-gpt-api.hf.space/api/v1" --env NEXT_PUBLIC_USE_MOCK="false"
```

---

## Part 3: Test the Live Deployment

1. Visit your Vercel deployment URL: `https://<your-app>.vercel.app`.
2. Login with the seeded test accounts:
   - **Analyst**: `analyst@insightgpt.dev` / `analyst-pass`
   - **Executive**: `executive@insightgpt.dev` / `exec-pass`
   - **Admin**: `admin@insightgpt.dev` / `admin-pass`
3. Ask questions in the chat:
   - *"What was our total revenue last quarter?"*
   - *"Break down gross profit by region."*
4. Attach files using the paperclip button:
   - Attach a CSV, Excel, Word, or PDF file and ask *"Summarize the trends in this file"*.
   - Watch the streaming answer cite the attachment as `[1]`.
