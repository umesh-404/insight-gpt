"""Command-line entry point for the retrieval service.

    insight-retrieval setup                 # create the `documents` collection
    insight-retrieval index [PATH]          # index a folder/JSON (default: samples)
    insight-retrieval search "question"     # hybrid search, print cited results
    insight-retrieval eval                  # run the golden-set scoreboard
    insight-retrieval status                # collection point count

All subcommands read ``config/retrieval.yaml`` (override with ``--config``). The
index, search, and eval commands need live Qdrant + Ollama; ``setup`` needs only
Qdrant.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
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


def _cmd_index(args) -> int:
    from .embedder import Embedder
    from .indexer import Indexer, load_documents
    from .store import Store

    cfg = load_config(args.config)
    if args.path:
        docs = load_documents(Path(args.path))
    else:
        docs = [Document.from_dict(d) for d in get_sample_documents()]
        print(f"no path given — indexing {len(docs)} built-in sample documents")

    store = Store(cfg)
    store.ensure_collection()
    with Embedder(cfg.embedding) as embedder:
        embedder.health()
        stats = Indexer(cfg, store, embedder).index_documents(docs)

    print(
        f"indexed {stats.documents} documents, {stats.chunks} chunks, "
        f"{stats.redactions} redactions"
    )
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
    from .eval import run

    return run(load_config(args.config))


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
    p_index = sub.add_parser("index", help="index a folder / JSON file of documents")
    p_index.add_argument("path", nargs="?", help="folder or JSON file (default: samples)")
    p_index.set_defaults(func=_cmd_index)
    p_search = sub.add_parser("search", help="hybrid search")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=5)
    p_search.set_defaults(func=_cmd_search)
    sub.add_parser("eval", help="golden-set scoreboard").set_defaults(func=_cmd_eval)
    sub.add_parser("status", help="collection point count").set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
