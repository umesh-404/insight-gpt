"""Command-line entry point for the retrieval service.

    insight-retrieval setup                 # create the `documents` collection
    insight-retrieval index                 # index the ingestion corpus (changed only)
    insight-retrieval index --full          # re-embed everything
    insight-retrieval index --samples       # index the built-in demo documents
    insight-retrieval index PATH            # index a folder / JSON file
    insight-retrieval search "question"     # hybrid search, print cited results
    insight-retrieval eval [--samples]      # run the golden-set scoreboard
    insight-retrieval status                # collection point count

With no path, ``index`` reads the corpus ``services/ingestion`` produces
(``data/ingested/documents.json``) and re-embeds only the documents whose
content hash changed. The built-in sample set is an EXPLICIT fallback
(``--samples``), never a silent one — indexing six demo documents when the real
corpus is missing looks like success and is not.

All subcommands read ``config/retrieval.yaml`` (override with ``--config``). The
index, search, and eval commands need live Qdrant + Ollama; ``setup`` needs only
Qdrant.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .corpus import default_corpus_path, load_corpus
from .models import Document
from .sample_docs import get_sample_documents


def _cmd_setup(args) -> int:
    from .store import Store

    cfg = load_config(args.config)
    store = Store(cfg)
    created = store.ensure_collection()
    print(
        f"collection '{cfg.collection}' "
        f"{'created' if created else 'already present'} at {cfg.qdrant_url}"
    )
    return 0


def resolve_corpus(path: str | None, *, samples: bool = False) -> tuple[list[Document], str]:
    """Pick the documents to index and say where they came from.

    Order of precedence, with NO silent fallback:

    1. ``--samples`` — the built-in demo set, chosen explicitly;
    2. an explicit path;
    3. the ingestion corpus (``data/ingested/documents.json``).

    If (3) is missing the caller gets a clear error naming the command that
    produces it, rather than quietly indexing six sample documents and looking
    like it worked.
    """
    if samples:
        docs = [Document.from_dict(d) for d in get_sample_documents()]
        return docs, "built-in sample documents"
    if path:
        target = Path(path)
        if not target.exists():
            raise SystemExit(f"corpus not found: {target}")
        return load_corpus(target), str(target)
    target = default_corpus_path()
    if not target.exists():
        raise SystemExit(
            f"no ingestion corpus at {target}.\n"
            "Produce it first with:  python -m services.ingestion run "
            "--job full_ingest --source documents\n"
            "or index the demo set explicitly with:  insight-retrieval index --samples"
        )
    return load_corpus(target), str(target)


def _cmd_index(args) -> int:
    from .corpus import IndexState, state_path_for
    from .embedder import Embedder
    from .indexer import Indexer
    from .store import Store

    cfg = load_config(args.config)
    docs, origin = resolve_corpus(args.path, samples=args.samples)
    print(f"indexing {len(docs)} documents from {origin}")

    state_path = Path(args.state) if args.state else state_path_for(
        Path(args.path) if args.path else default_corpus_path()
    )
    state = IndexState(state_path, cfg.collection)

    store = Store(cfg)
    store.ensure_collection()
    with Embedder(cfg.embedding) as embedder:
        embedder.health()
        stats = Indexer(cfg, store, embedder).index_changed(docs, state, full=args.full)

    print(f"indexed {stats.summary()}")
    for err in stats.errors:
        print(f"  ! {err}", file=sys.stderr)
    return 1 if stats.errors else 0


def _cmd_search(args) -> int:
    from .retriever import QdrantRetriever

    cfg = load_config(args.config)
    results = QdrantRetriever(cfg).search(args.query, k=args.k)
    if not results:
        print("(no results)")
        return 0
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r.score:.3f}] {r.source_type} · {r.doc_id} · {r.date or '-'}")
        print(f"   {r.title}")
        print(f"   {r.body[:160].strip()}")
    return 0


def _cmd_eval(args) -> int:
    from .eval import run, run_offline_proxy

    cfg = load_config(args.config)
    if args.offline:
        return run_offline_proxy(cfg)
    return run(cfg, samples=args.samples)


def _cmd_status(args) -> int:
    from .store import Store

    cfg = load_config(args.config)
    print(f"collection '{cfg.collection}': {Store(cfg).count()} points")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insight-retrieval", description=__doc__)
    parser.add_argument("--config", help="path to retrieval.yaml (default: bundled)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="create the documents collection").set_defaults(
        func=_cmd_setup
    )
    p_index = sub.add_parser(
        "index", help="index the ingestion corpus (or a folder / JSON file)"
    )
    p_index.add_argument(
        "path",
        nargs="?",
        help=f"folder or JSON file (default: {default_corpus_path()})",
    )
    p_index.add_argument(
        "--samples",
        action="store_true",
        help="index the built-in demo documents instead of the ingestion corpus",
    )
    p_index.add_argument(
        "--full",
        action="store_true",
        help="re-embed every document, ignoring the changed-only state",
    )
    p_index.add_argument(
        "--state", help="path to the index-state file (default: beside the corpus)"
    )
    p_index.set_defaults(func=_cmd_index)
    p_search = sub.add_parser("search", help="hybrid search")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=5)
    p_search.set_defaults(func=_cmd_search)
    p_eval = sub.add_parser("eval", help="golden-set scoreboard")
    p_eval.add_argument(
        "--samples",
        action="store_true",
        help="score the built-in demo golden set instead of the generated-corpus one",
    )
    p_eval.add_argument(
        "--offline",
        action="store_true",
        help="deterministic lexical-proxy scoreboard (no Qdrant/Ollama); reports "
        "the query-rewrite and augmentation deltas",
    )
    p_eval.set_defaults(func=_cmd_eval)
    sub.add_parser("status", help="collection point count").set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
