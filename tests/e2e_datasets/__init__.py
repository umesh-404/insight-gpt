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
"""
