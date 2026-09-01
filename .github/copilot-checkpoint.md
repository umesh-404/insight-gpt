# Copilot checkpoint

## Objective
Implement a persistent, resumable data-analysis workflow for this project while keeping orchestration responsibilities with the GitHub Copilot agent instead of embedding checkpoint memory in the underlying LLM.

## Current status
- Project: InsightGPT
- Active task: multimodal, resumable analysis workflow
- Agent ownership: Copilot agent manages checkpoints, progress tracking, and resume state
- Model ownership: the local LLM is not responsible for persistent memory management

## Installed local Ollama inventory
- Text model installed: `qwen3:4b-q4_K_M`
- Reranker installed: `dengcao/Qwen3-Reranker-0.6B:F16`
- Embedding model installed: `nomic-embed-text:latest`
- Vision-capable model: none detected in `ollama list`

## Vision model status
No suitable vision model is currently installed in the local Ollama runtime.

This is a blocking requirement for image/chart/PDF screenshot understanding, and the project must not silently download a large model without approval.

Likely candidate families if the user wants this enabled:
- `llava`
- `qwen2.5vl`
- `gemma3`
- other small-to-medium multimodal Ollama models depending on hardware and appetite for runtime cost

## Architecture to use
1. Text reasoning model
   - Use the installed local model: `qwen3:4b-q4_K_M`
   - Role: natural-language synthesis, explanation, and final answer drafting

2. Vision model
   - Separate model, only used for image/chart interpretation tasks
   - Role: OCR, chart understanding, image-based evidence extraction
   - Must stay isolated from the text reasoning workflow unless explicitly invoked

3. Structured analysis
   - Use deterministic Pandas / warehouse / SQL logic for metrics, deltas, trends, and anomaly detection
   - This is the trusted calculation path and should be preferred over opaque model inference for numeric work

4. File handling
   - CSV: parse and compute metrics directly with Pandas
   - PDF: extract text/tables when possible; use fallback OCR/vision path for unreadable pages
   - Image: pass only to the separate vision model when a chart or diagram needs interpretation

## Persistent workflow contract
Each analysis run should persist a resumable checkpoint with these fields:
- workflow_id
- status: queued | running | paused | succeeded | failed
- source files and file types
- current stage
- completed stages
- extracted metadata
- metrics and findings
- next action / resume point
- created_at / updated_at

The checkpoint file must be human-readable and readable by a new Copilot session without any hidden memory.

## Implementation plan
### Phase 1: checkpoint + orchestration
- Create a clear workflow record at the repo root under `.github/copilot-checkpoint.md`
- Record completed steps, blockers, and next actions in plain text
- Ensure resume is possible by reading this file and continuing from the last successful stage

### Phase 2: data ingestion and analysis
- CSV path: parse, summarize, calculate deltas, trends, and anomalies
- PDF path: extract document text; if needed, pass unreadable pages to a dedicated vision model
- Image path: analyze chart labels, series, axes, trends, and anomalies with the vision model

### Phase 3: synthesis
- Combine deterministic metric output + extracted evidence + document context
- Keep the text model focused on final explanation, not raw image parsing or arithmetic

### Phase 4: verification
- Test each data source path independently
- Confirm checkpoint resume skips already-completed work
- Ensure a new session can continue without relying on prior model memory

## Current checkpoint state
Status: in progress

Completed:
- Confirmed local Ollama install
- Confirmed installed text model: `qwen3:4b-q4_K_M`
- Confirmed no vision model is currently installed
- Recorded the requirement to avoid automatic large-model downloads without approval
- Created the persistent checkpoint file for this task

Blocked / pending user input:
- Decide whether to install a vision-capable Ollama model for local multimodal analysis
- If approved, choose a model size and family appropriate for the local environment

## Next action
Before implementing the vision integration, wait for approval to install a suitable vision model. The project will continue once the user confirms the model choice or explicitly prefers the current text-only path.
