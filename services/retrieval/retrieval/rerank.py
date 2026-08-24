"""Cross-encoder reranking — the second retrieval stage.

Bi-encoder retrieval compares a query vector with document vectors computed in
isolation, so it can only measure similarity, not *answerhood*. A cross-encoder
reads the query and document together and judges directly whether this document
answers this query. It is far more accurate and far slower — which is why the
pipeline is staged: hybrid RRF casts a wide cheap net (recall), reranking orders
the top few candidates precisely (precision).

Scoring, since Ollama has no rerank endpoint (rememory's technique): a
Qwen3-Reranker-style model is a causal LM fine-tuned to answer a binary question
with a single token. We send the official prompt template in RAW mode primed
with an empty ``<think>`` block, generate exactly ONE token, and read the
logprobs for that position. ``score = softmax`` over the ``yes`` and ``no``
logits.

Failure policy: reranking is an ENHANCEMENT. Any failure — model missing,
timeout, budget exceeded — logs to stderr and returns the first-stage (RRF)
order untouched. A search must never fail because its second stage did. A hard
404 (model not pulled) disables reranking for the process lifetime so it does
not add a timeout to every subsequent query.
"""

from __future__ import annotations

import asyncio
import math
import sys
import time

import httpx

from .config import RetrievalConfig
from .search import SearchHit


class Reranker:
    def __init__(self, config: RetrievalConfig) -> None:
        cfg = config.reranker
        self.enabled = cfg.enabled
        self.model = cfg.model
        self.candidates = cfg.candidates
        self.concurrency = cfg.concurrency
        self.keep_alive = cfg.keep_alive
        self.timeout = cfg.timeout_seconds
        self.max_batch_seconds = cfg.max_batch_seconds
        self.instruct = cfg.instruct.strip()
        self.base_url = config.embedding.base_url.rstrip("/")
        # After a hard failure, disable for the process lifetime: a missing model
        # would otherwise add a timeout to EVERY search.
        self._dead = False

    # ------------------------------------------------------------------ score
    def _prompt(self, query: str, document: str) -> str:
        return (
            "<|im_start|>system\nJudge whether the Document meets the requirements "
            'based on the Query and the Instruct provided. Note that the answer can '
            'only be "yes" or "no".<|im_end|>\n'
            f"<|im_start|>user\n<Instruct>: {self.instruct}\n"
            f"<Query>: {query}\n<Document>: {document}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )

    async def _score_one(
        self, client: httpx.AsyncClient, query: str, doc: str
    ) -> float | None:
        resp = await client.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": self._prompt(query, doc),
                "raw": True,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 1, "num_ctx": 8192},
                "logprobs": True,
                "top_logprobs": 20,
                "keep_alive": self.keep_alive,
            },
        )
        resp.raise_for_status()
        logprobs = resp.json().get("logprobs") or []
        if not logprobs:
            return None
        yes = no = None
        for cand in logprobs[0].get("top_logprobs", []):
            token = cand["token"].strip().lower()
            if token == "yes" and yes is None:
                yes = cand["logprob"]
            elif token == "no" and no is None:
                no = cand["logprob"]
        if yes is None and no is None:
            return None
        e_yes = math.exp(yes) if yes is not None else 0.0
        e_no = math.exp(no) if no is not None else 0.0
        return e_yes / (e_yes + e_no) if (e_yes + e_no) else 0.0

    async def _score_batch(self, query: str, docs: list[str]) -> list[float | None]:
        sem = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(timeout=self.timeout) as client:

            async def bounded(doc: str) -> float | None:
                async with sem:
                    return await self._score_one(client, query, doc)

            return list(await asyncio.gather(*(bounded(d) for d in docs)))

    # ------------------------------------------------------------------ rerank
    def rerank(
        self, query: str, hits: list[SearchHit], pool_size: int | None = None
    ) -> list[SearchHit]:
        """Reorder hits by cross-encoder score; annotate each with ``rerank``.

        On any failure the input (RRF) order is returned untouched. ``pool_size``
        widens the scored window beyond ``candidates`` so the head of the
        response never mixes scored and unscored entries.
        """
        if not self.enabled or self._dead or self.model == "" or len(hits) < 2:
            return hits

        window = max(self.candidates, pool_size or 0)
        pool = hits[:window]
        rest = hits[window:]
        started = time.perf_counter()

        def run_batch() -> list[float | None]:
            return asyncio.run(
                asyncio.wait_for(
                    self._score_batch(query, [h.content for h in pool]),
                    timeout=self.max_batch_seconds,
                )
            )

        try:
            # asyncio.run() raises if this thread already has a running loop;
            # run the batch in its own thread+loop in that case.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                scores = run_batch()
            else:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool_ex:
                    scores = pool_ex.submit(run_batch).result(
                        timeout=self.max_batch_seconds + 5
                    )
        except Exception as exc:
            print(
                f"rerank failed ({type(exc).__name__}: {exc}); using RRF order",
                file=sys.stderr,
            )
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                self._dead = True  # model not pulled — stop trying this process
                print(
                    f"reranker disabled for this session. Fix: ollama pull {self.model}",
                    file=sys.stderr,
                )
            return hits

        elapsed = time.perf_counter() - started
        scored: list[tuple[float, SearchHit]] = []
        for hit, s in zip(pool, scores, strict=True):
            if s is not None:
                hit.rerank = round(s, 4)
            # A None score keeps its position value low but present, so it sorts
            # below anything scored.
            scored.append((s if s is not None else -1.0, hit))
        scored.sort(key=lambda t: t[0], reverse=True)

        print(f"reranked {len(pool)} candidates in {elapsed:.1f}s", file=sys.stderr)
        return [h for _, h in scored] + rest
