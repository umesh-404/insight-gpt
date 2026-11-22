"""End-to-end proof for the dynamic-dataset feature (contract agent 5).

Read-only with respect to every other agent's code: this package asserts
behaviour, it never patches it. Two layers:

* **Offline** (always runs) — the API contract, the role gates, and the honesty
  rules of contract §6, exercised through ``fastapi.testclient.TestClient``
  with the deterministic ``fake`` provider and a stub retriever. No Qdrant, no
  Ollama, no network.
* **Live** (``RETRIEVAL_LIVE=1`` plus a reachable Qdrant and Ollama) — the real
  journey: upload a file through the API, watch it get parsed, redacted,
  chunked, embedded and stored, then ask a question and see the answer cite
  that dataset. Writes only to an ``insightgpt_test_*`` scratch collection.

Phase 2 added three claims and a module for each:

* ``test_durability.py`` — the dataset list survives a restart, and every row
  that survives still agrees with what the vector store holds. Reconciliation
  is driven with a chunk counter the test supplies, so the disagreements a
  real restart produces (points deleted out of band; a job killed mid-index)
  are inputs rather than accidents.
* ``test_tabular.py`` — a numeric question over an uploaded table returns the
  figure the file actually contains, attributed to that table. The expected
  value is computed from ``fixtures/quarry_sales.csv`` rather than recorded, so
  a plausible wrong total cannot pass. Also the four attacks on it: an unknown
  dataset, the two stores touching in either direction, and a total summed from
  a sample.
* ``test_progress.py`` — the bar moves because batches completed, asserted at
  the unit of work (one report per embedding request) rather than by watching
  numbers rise.
* ``test_live_phase2.py`` — claim 1 again with nothing simulated but the
  process boundary: real points in a real collection, restarted and re-read.

There is also a **second store** to be careful of from phase 2 on: uploaded
tables are materialized into a DuckDB database that defaults to
``data/uploads/datasets.duckdb`` in the repo. Every module here points
``DATASETS_DB_PATH`` at ``tmp_path`` and closes the connection afterwards, for
the same reason the Qdrant guard exists — a test has no business writing into
a store it does not own.
"""
