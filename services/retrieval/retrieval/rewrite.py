"""Pre-retrieval query rewriting.

A user's question is written for a human, not for a retriever: it is padded with
filler ("why are ...", "can you tell me ..."), uses abbreviations the corpus
spells out, and is often not self-contained. Rewriting it into a cleaner search
query before embedding and lexical matching is a cheap, well-attested recall win
(docs/04-retrieval-rag.md).

Two paths, chosen so the feature is always available:

* **Deterministic** (default, no network): lowercase, drop filler/stopwords,
  keep entities verbatim (SKUs, order ids, region and product names), and expand
  known abbreviations while keeping the abbreviation too. Pure function of
  ``(query, config)`` — unit-tested, offline, never fails.
* **LLM** (opt-in, live Ollama): ask a small chat model to produce a single
  standalone search query, and — when ``hyde`` is on — a one-sentence
  hypothetical answer to embed alongside the query (HyDE). Any failure falls
  back to the deterministic rewrite, so enabling it can only help.

The rewriter emits *text*; :mod:`retrieval.search` owns turning that text into
dense and sparse vectors, so the two representations never drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .chat import OllamaChat
from .config import EmbeddingConfig, QueryRewriteConfig
from .sparse import STOPWORDS

# Question scaffolding and polite filler that carries no retrieval signal. Union
# with the sparse stopword list so the two never disagree on what is noise.
_QUERY_FILLERS: frozenset[str] = STOPWORDS | frozenset(
    """
    why what which who whom whose when where how show tell give find list get
    please could would can explain describe summarize summarise summary
    happening happened going want need know see look looking regarding
    """.split()  # noqa: SIM905 — multiline string reads better than a 33-item literal
)

# Conservative, business/retail abbreviations that unambiguously expand. The
# expansion is ADDED, the abbreviation kept, so both forms match.
_DEFAULT_ABBREVIATIONS: dict[str, str] = {
    "eta": "estimated time of arrival",
    "sku": "stock keeping unit",
    "roi": "return on investment",
    "yoy": "year over year",
    "qoq": "quarter over quarter",
    "mom": "month over month",
    "aov": "average order value",
    "csat": "customer satisfaction",
    "nps": "net promoter score",
    "cx": "customer experience",
    "rma": "return merchandise authorization",
    "fc": "fulfilment centre",
    "wh": "warehouse",
    "qty": "quantity",
    "inv": "inventory",
}

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")


@dataclass
class RewrittenQuery:
    """The product of a rewrite: text for retrieval, plus provenance.

    ``search_query`` is what gets embedded and lexically tokenized. ``hyde`` is
    an optional hypothetical-answer sentence embedded as an extra dense branch.
    ``original`` is retained because reranking judges answerhood against the
    user's true question, not the rewrite.
    """

    original: str
    search_query: str
    hyde: str | None = None
    method: str = "deterministic"


def _is_entity(token: str) -> bool:
    """Preserve identifier-ish tokens verbatim: SKUs, order ids, codes.

    A token with a digit (``X230``, ``ORD-88213``, ``2026Q2``) or an internal
    uppercase / all-caps shape (``SKU``, ``UserRepo``) is a literal the lexical
    branch must keep exactly; stripping or lowercasing it loses the exact match.
    """
    if any(c.isdigit() for c in token):
        return True
    return len(token) > 1 and any(c.isupper() for c in token[1:]) or token.isupper()


class QueryRewriter:
    def __init__(
        self, cfg: QueryRewriteConfig, embedding: EmbeddingConfig | None = None
    ) -> None:
        self.cfg = cfg
        self.abbreviations = {**_DEFAULT_ABBREVIATIONS, **cfg.abbreviations}
        # Chat reuses the embedding host (same Ollama), with its own model name.
        base_url = embedding.base_url if embedding else "http://127.0.0.1:11434"
        self._chat = OllamaChat(base_url, cfg.model, cfg.timeout_seconds)

    # ------------------------------------------------------------------ public
    def rewrite(self, query: str) -> RewrittenQuery:
        """Rewrite ``query`` for retrieval, LLM-first when enabled, else deterministic."""
        original = query.strip()
        if not self.cfg.enabled or not original:
            return RewrittenQuery(original=original, search_query=original)

        clipped = original[: self.cfg.max_query_chars]
        if self.cfg.use_llm and self._chat.usable:
            llm = self._llm_rewrite(clipped)
            if llm is not None:
                return llm
        return RewrittenQuery(
            original=original,
            search_query=self.deterministic(clipped),
            method="deterministic",
        )

    # ----------------------------------------------------------- deterministic
    def deterministic(self, query: str) -> str:
        """Lowercase, drop filler, keep entities, expand abbreviations.

        Order-preserving. Falls back to the original query if it would otherwise
        strip everything (an all-stopword question), so retrieval never gets an
        empty string.
        """
        kept: list[str] = []
        for token in _WORD.findall(query):
            lowered = token.lower()
            # Known abbreviations expand FIRST, before the entity guard — an
            # all-caps abbreviation ("FC", "SKU") would otherwise be preserved
            # verbatim and never expanded. Keep the abbreviation too, so both
            # the short and long form match.
            expansion = self.abbreviations.get(lowered)
            if expansion:
                kept.append(lowered)
                kept.extend(expansion.split())
                continue
            if _is_entity(token):
                kept.append(token)
                continue
            if len(lowered) < 2 or lowered in _QUERY_FILLERS:
                continue
            kept.append(lowered)

        rewritten = " ".join(kept).strip()
        return rewritten or query.strip().lower()

    # -------------------------------------------------------------------- llm
    def _llm_rewrite(self, query: str) -> RewrittenQuery | None:
        system = (
            "You rewrite a user's question into a concise search query for a "
            "retail analytics document store (support tickets, product reviews, "
            "operational reports). Expand abbreviations, keep concrete entities "
            "(product codes, order ids, regions, categories), and drop filler. "
            "Reply with ONLY the rewritten query on a single line."
        )
        text = self._chat.complete(query, system=system, num_predict=64)
        if not text:
            return None
        search_query = text.splitlines()[0].strip().strip('"')
        if not search_query:
            return None
        hyde = self._hyde(query) if self.cfg.hyde else None
        return RewrittenQuery(
            original=query, search_query=search_query, hyde=hyde, method="llm"
        )

    def _hyde(self, query: str) -> str | None:
        """A one-sentence hypothetical answer to embed alongside the query (HyDE)."""
        system = (
            "Write one plausible sentence that a document answering the user's "
            "question might contain, about a retail business. Invent specifics if "
            "needed. Reply with ONLY that one sentence."
        )
        text = self._chat.complete(query, system=system, num_predict=96)
        if not text:
            return None
        return text.splitlines()[0].strip() or None
