# 12 — Demo Walkthrough

A five-minute script for demonstrating InsightGPT live — to a viva panel, a
reviewer, or anyone who has not seen it before. It gives the exact sequence, what
to point at on each screen, and one line of what to say per beat. A terminal-only
version (pure `curl`) follows in §8 for when the projector, the browser or the
frontend is not cooperating.

**The three moments that matter.** Everything else is table stakes; budget your
time so these three land:

1. **The answer shows its SQL and its documents** (§3) — it is explainable.
2. **The abstention** (§4) — it refuses to guess. This is the differentiator.
3. **The insight feed with a root cause** (§5) — it finds the question for you.

---

## 1. Before you start (2 minutes, off-camera)

```bash
setup.cmd --doctor        # Windows
./setup.sh --doctor       # macOS / Linux
```

`--doctor` changes nothing and names the exact broken thing. Fix what it names,
or run `--repair` for a clean rebuild that preserves your `.env`. See §9.

Then confirm the stack is alive and warm the caches by loading each page once:

```bash
curl -s http://localhost:8000/health
```

Have ready in separate tabs: the chat page, the insights page, and a terminal.
Log in as **analyst** (`analyst@insightgpt.dev` / `analyst-pass`) — the insights,
pipeline-run and report endpoints all require at least the analyst role, so a
viewer session will lose you Beats 3 and 5. Keep the **viewer** account in mind
for the role question in §7.

---

## 2. Beat 0 — Frame it in one sentence (20 seconds)

**Do:** stay on the login or landing screen. Do not start clicking.

**Say:** *"This is an analytics assistant with a rule: the model never writes
SQL. It picks from a governed layer of eight metrics and eight dimensions, and a
query builder writes the SQL. So it cannot invent a join or a number — and when a
question falls outside that layer, it tells you instead of guessing. Let me show
you both halves of that."*

---

## 3. Beat 1 — The question (90 seconds)

**Do:** in chat, ask:

> Why did sales decline last quarter?

Let it stream. Then, in this order:

1. **Point at the narrative.** It names a percentage, a prior and current value,
   a region and a category, and it carries `[1]`–`[5]` citation markers.
2. **Open the SQL reveal.** Several statements — the scalar for each period, the
   quarter series, and one grouped query per dimension. Every one of them ends in
   a `LIMIT`, and every date is a `?` parameter, not string-interpolated.
3. **Open the tables.** The contribution breakdown by region and by category,
   with prior / current / delta columns. This is where the "-130,000 in North"
   in the narrative comes from.
4. **Open the citations.** Two support tickets, a review, an operations report —
   with dates inside the quarter.

**Say:** *"The number came from SQL over the warehouse. The cause came from
support tickets and reviews. The model's job was to join those two things into a
sentence and cite them — not to compute anything. That is why I can show you
every source it used."*

**Point out:** the route badge reads `hybrid` — the router decided this question
needs both the structured and the unstructured path. Confidence: `high`.

---

## 4. Beat 2 — The abstention (60 seconds) — **the differentiator**

**Do:** ask a question the semantic layer does not cover:

> What was our churn rate?

The response, captured verbatim:

```
I can't answer that reliably, so I won't guess. 'churn rate' is not a governed
metric, so I cannot compute it reliably.
```

…with route `abstain`, confidence `low`, and two suggestions drawn from the
metrics that *do* exist (`return_rate`, `units_sold`).

**Say:** *"There is no churn metric in the semantic layer, so there is no honest
answer — and instead of assembling something plausible out of the tables it can
see, it stops, says why, and offers the nearest governed metrics. An analytics
assistant that always produces a number is one you cannot trust with the numbers
that matter."*

**If asked "how do you know it isn't just failing?"** — the eval harness scores
abstention explicitly: 2/2 out-of-scope probes abstain, alongside 12/12 in-scope
questions that execute correctly. Show it in §6.

**Optional follow-up if you have time:** take one of the suggestions —
*"What is the return rate last quarter?"* — and watch it answer immediately. The
refusal was scoped, not a general failure.

---

## 5. Beat 3 — Insights it found on its own (60 seconds)

**Do:** open the insights page (or `GET /api/v1/insights`). Pick the revenue
card. Captured verbatim from the running API:

```
Revenue fell 11.4% in 2026Q2 vs 2026Q1, from $1.30M to $1.15M.
North (region) drove most of the move (-$130.0K, 88% of the change).
```

Expand it and point at, in order:

- **`root_cause`** — dimension `region`, segment `North`, delta `-130000`,
  contribution `-87.8%`. The system attributed the movement, it did not just
  report it.
- **`contributions`** — the full ranked decomposition across region, category and
  product, so you can see that North is 88% of the move and Electronics is the
  category underneath it.
- **`method`** — a plain-English statement of how it decided this was worth
  surfacing: *"Period-over-period change at quarter grain (threshold 5%, min
  magnitude 0); insufficient history for a z-score."* It says when it does
  **not** have enough history for a statistical test rather than pretending it
  does.
- **`evidence`** — the same tickets and reviews from Beat 1, attached to the
  insight with snippets.

**Say:** *"Beat 1 was me asking a question. This is the system asking it for me —
it scans the governed metrics period over period, ranks what moved, attributes
each movement to a segment, and attaches the documents. And it tells you the
method it used, including when the history is too short to be statistical."*

---

## 6. Beat 4 — Proof, not claims (60 seconds)

**Do:** switch to the terminal and run:

```bash
make eval
```

Two scoreboards print. The real numbers:

```
Text-to-SQL scores
metric                      score   floor   status
execution_accuracy          1.000   0.90    PASS
routing_accuracy            1.000   0.90    PASS
metric_selection_accuracy   1.000   0.90    PASS
abstention_rate             1.000   -       -

counts: execution 12/12, routing 13/13, metric-selection 12/12, abstention 2/2
```

```
Faithfulness scores
metric                      score   floor   status
groundedness_rate           1.000   0.80    PASS
citation_coverage           1.000   0.90    PASS
no_fabricated_number_rate   1.000   0.90    PASS

detail: grounded sentences 12/12, resolved markers 16/16, grounded numbers 8/8
```

**Say:** *"These are floors, not vanity metrics — the harness runs as a test in
CI and fails the build if a score drops below its floor. Groundedness means every
sentence traces to a retrieved chunk or a SQL result; no-fabricated-number means
every figure in the prose appears in a result set. Quality is measured, not
asserted."*

**If you have 30 more seconds**, show the guardrails directly:

```bash
cd services/api && uv run pytest tests/test_guardrails.py tests/test_engine_selfcorrect.py -v
```

Point at four test names as they scroll: `test_write_and_ddl_rejected`,
`test_non_allowlisted_table_rejected`, `test_reaching_unmodeled_table_in_join_rejected`,
and `test_bounded_retries_give_up_without_looping`.

**Say (self-correction):** *"When a metric selection fails validation or comes
back clearly wrong, the engine retries the selection — never free SQL — and
records each attempt: what it tried, why it was rejected, and whether it
corrected or gave up. The loop is bounded, so a bad question degrades into an
abstention rather than an infinite retry. Those two behaviours are the same
test file."*

---

## 7. Beat 5 — It is a real system, not a script (40 seconds)

Pick **one** of these depending on your audience, then stop:

- **Pipeline observability** — the pipeline runs page: each run has a status, a
  trigger (scheduled vs. manual), per-stage row counts and durations. *"The ELT
  is scheduled, and every run is a row you can inspect."*
- **Roles** — log out, log back in as `viewer@insightgpt.dev`. *"Roles are
  enforced server-side, on the endpoint: insights, pipeline runs and reports
  require analyst; triggering a pipeline requires admin. A viewer can ask
  questions and read metrics, and that is all."*
- **Redaction** — `services/ingestion/redact.py` plus its tests. *"Secrets and
  PII are stripped before a document is ever embedded — tokens, emails, phone
  numbers, Luhn-checked card numbers, private-key blocks — and a test asserts
  that nothing unredacted reaches the vector store."*
- **The stack** — `docker compose ps`. *"Six services, one command, and only two
  of them publish a port."*

---

## 8. Terminal-only demo (no browser)

Every beat above has a `curl` equivalent. Adding `-H "Accept: application/json"`
returns the whole answer envelope in one object instead of an SSE stream —
better for a live terminal, since the stream scrolls.

```bash
# 0. Log in and keep the token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"analyst@insightgpt.dev","password":"analyst-pass"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 1. Show the closed vocabulary the model has to choose from
curl -s http://localhost:8000/api/v1/metrics -H "Authorization: Bearer $TOKEN"

# 2. Beat 1 — the answer, with sql / tables / citations in one envelope
curl -s -X POST http://localhost:8000/api/v1/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -d '{"question":"Why did sales decline last quarter?"}'

# 2b. The same question as the UI sees it — a live SSE stream
curl -N -X POST http://localhost:8000/api/v1/ask \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"Why did sales decline last quarter?"}'

# 3. Beat 2 — the abstention
curl -s -X POST http://localhost:8000/api/v1/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -d '{"question":"What was our churn rate?"}'

# 4. Beat 3 — insights, root cause and evidence
curl -s "http://localhost:8000/api/v1/insights?limit=3" -H "Authorization: Bearer $TOKEN"

# 5. Beat 5 — pipeline runs
curl -s "http://localhost:8000/api/v1/pipeline-runs?limit=3" -H "Authorization: Bearer $TOKEN"

# 6. What every backing service is actually doing right now
curl -s http://localhost:8000/status -H "Authorization: Bearer $TOKEN"
```

Two things worth narrating from the raw JSON:

- Step 1 is the whole argument in one response — eight metric keys, eight
  dimensions, the time grains, and the hard `limits` block (`max_rows`,
  `default_rows`, `statement_timeout_ms`). *"That is the entire vocabulary. The
  model picks from this list; it does not write SQL."*
- Step 2's `"sql"` array is what the builder emitted, with `?` placeholders and a
  trailing `LIMIT` on every statement.

`GET /status` reports each backing service (`postgres`, `qdrant`, `worker`,
`llm`) as live or `fixture`, plus warehouse row counts and the index point count
— a good final slide, because it proves which mode you are actually demoing.

---

## 9. If it breaks mid-demo

**Rule one: `--doctor` first.** It diagnoses only and changes nothing, and it
names the specific broken thing rather than a generic failure.

```bash
setup.cmd --doctor        # Windows
./setup.sh --doctor       # macOS / Linux
```

| Symptom | First move |
|---|---|
| A page or endpoint refuses to load | `--doctor`, then read the named cause |
| Something is up but behaving wrongly | `--repair` — clean rebuild + recreate, re-seed, re-verify; `.env` is preserved |
| Answers are slow on a laptop with no GPU | Expected — the local LLM is the slow step. Fall back to the terminal script in §8 against the offline stack, or set a cloud `LLM_PROVIDER` |
| Model pulls are taking too long before the demo | `--skip-models` gets you a running stack fast; say out loud that retrieval quality is degraded in that mode |
| Nothing works and time is up | Run `make eval` and `make test` instead. The harnesses need no Docker, no network and no models, and they demonstrate the same guarantees with numbers |

Both scripts are **idempotent**: fix the cause the script names and run it
again — completed steps become fast no-ops.

---

## 10. Questions you will be asked

| Question | Answer |
|---|---|
| *"Isn't this just the model writing SQL?"* | No — the model emits a metric selection (metric key, dimensions, time range). A query builder emits the SQL, and a validator rejects non-`SELECT` statements, non-allow-listed tables, and joins reaching unmodeled tables, then enforces a row limit and a statement timeout. |
| *"What stops it hallucinating a number?"* | Numbers come from SQL result sets, and the faithfulness harness scores whether every figure in the prose appears in one — currently 8/8, floored at 0.90 in CI. |
| *"What if the question has no answer?"* | It abstains and says why, with suggestions. §4. |
| *"Does it need an API key / the internet?"* | No. Embeddings and reranking are always local (Ollama); the reasoning step defaults to local and takes a cloud provider only if you configure one. |
| *"Is the data real?"* | Synthetic retail data, generated with a deliberately planted quarterly dip so the slice question has a genuine, discoverable cause — see [`02-data-model.md`](02-data-model.md). |
| *"What isn't finished?"* | Job chaining, watermark incremental sync for SQL sources, the read-only Postgres role split, and a frontend test suite. All four are designed in the docs and listed in the README's status table. Say this plainly — it is a better answer than a feature list. |

---

## 11. Related documents

- Insight engine internals → [`05-insight-engine.md`](05-insight-engine.md)
- API surface and the SSE event contract → [`06-api.md`](06-api.md)
- Frontend screens → [`07-frontend.md`](07-frontend.md)
- Guardrails and threat model → [`08-security.md`](08-security.md)
- Harnesses, floors and CI → [`10-testing-eval.md`](10-testing-eval.md)
