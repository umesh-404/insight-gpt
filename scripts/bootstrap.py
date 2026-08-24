"""First-run bootstrap: bring empty volumes to a demo-ready InsightGPT.

Run from inside the worker container (or any host with the worker deps + repo
on PYTHONPATH):

    python scripts/bootstrap.py            # full first-run bootstrap
    python scripts/bootstrap.py --skip-models   # don't pull Ollama models

It is **idempotent**: every step checks state before acting, so a second run is
a near no-op and a partial failure can simply be re-run. Each step logs `[n/N]`.
Missing services fail with a clear, actionable message rather than a traceback.

Steps
-----
1. Wait for postgres, qdrant, and ollama to be reachable.
2. Pull the Ollama models (embedding + reranker + chat) — skipped if present.
3. Build the warehouse: generate -> load raw -> dbt seed/run/test
   (delegates to scripts/seed.py, which is itself idempotent).
4. Create the Qdrant `documents` collection (skipped if it exists).
5. Index the built-in sample documents (skipped if the collection is non-empty).

Env (all have compose defaults):
    POSTGRES_DSN / POSTGRES_HOST ...  Postgres connection
    QDRANT_URL                        e.g. http://qdrant:6333
    OLLAMA_HOST                       e.g. http://ollama:11434
    LLM_PROVIDER, LLM_MODEL           chat model (pulled only for ollama)
    EMBED_MODEL, RERANK_MODEL         embedding + reranker models
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TOTAL_STEPS = 5

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434").rstrip("/")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "documents")


def _step(n: int, title: str) -> None:
    print(f"\n[{n}/{TOTAL_STEPS}] {title}", flush=True)


def _fail(msg: str) -> None:
    print(f"\nBOOTSTRAP FAILED: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
# 1. Wait for dependencies                                                     #
# --------------------------------------------------------------------------- #
def _wait_http(name: str, url: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=5.0)
            if r.status_code < 500:
                print(f"  - {name} ready ({url})")
                return
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - report and retry
            last = type(exc).__name__
        time.sleep(2.0)
    _fail(f"{name} not reachable at {url} after {timeout:.0f}s (last: {last}). "
          f"Is the '{name}' service healthy? Try `docker compose ps`.")


def _wait_postgres(timeout: float = 120.0) -> None:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        host = os.environ.get("POSTGRES_HOST", "postgres")
        port = os.environ.get("POSTGRES_PORT", "5432")
        user = os.environ.get("POSTGRES_USER", "insight")
        pw = os.environ.get("POSTGRES_PASSWORD", "insight")
        db = os.environ.get("POSTGRES_DB", "insight")
        dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"
    try:
        import psycopg
    except ImportError:
        _fail("psycopg is not installed — the worker image should provide it "
              "(psycopg[binary]). Cannot verify Postgres.")
        return
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                conn.execute("SELECT 1")
            print("  - postgres ready")
            return
        except Exception as exc:  # noqa: BLE001
            last = type(exc).__name__
        time.sleep(2.0)
    _fail(f"postgres not reachable after {timeout:.0f}s (last: {last}).")


def step_wait() -> None:
    _step(1, "Wait for postgres / qdrant / ollama")
    _wait_postgres()
    _wait_http("qdrant", f"{QDRANT_URL}/readyz")
    _wait_http("ollama", f"{OLLAMA_HOST}/api/tags")


# --------------------------------------------------------------------------- #
# 2. Pull Ollama models                                                       #
# --------------------------------------------------------------------------- #
def _installed_models() -> set[str]:
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=10.0)
        r.raise_for_status()
        return {m.get("name", "") for m in r.json().get("models", [])}
    except Exception:  # noqa: BLE001
        return set()


def _pull_model(name: str) -> None:
    installed = _installed_models()
    # Ollama reports names as "model:tag"; accept a bare name as ":latest".
    canonical = name if ":" in name else f"{name}:latest"
    if name in installed or canonical in installed:
        print(f"  - {name} already present, skipping")
        return
    print(f"  - pulling {name} ...", flush=True)
    try:
        with httpx.stream(
            "POST", f"{OLLAMA_HOST}/api/pull",
            json={"name": name}, timeout=None,
        ) as resp:
            resp.raise_for_status()
            for _ in resp.iter_lines():
                pass  # drain progress; stream ends when the pull completes
        print(f"    done: {name}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"failed to pull Ollama model '{name}': {exc}")


def step_models(skip: bool) -> None:
    _step(2, "Pull Ollama models (embedding + reranker + chat)")
    if skip:
        print("  - --skip-models set, skipping")
        return
    models: list[str] = []
    embed = os.environ.get("EMBED_MODEL", "nomic-embed-text")
    rerank = os.environ.get("RERANK_MODEL", "")
    models.append(embed)
    if rerank:
        models.append(rerank)
    if os.environ.get("LLM_PROVIDER", "ollama").lower() == "ollama":
        chat = os.environ.get("LLM_MODEL", "llama3.1:8b")
        if chat:
            models.append(chat)
    for m in dict.fromkeys(models):  # de-dupe, preserve order
        _pull_model(m)


# --------------------------------------------------------------------------- #
# 3. Build the warehouse (generate -> load -> dbt)                            #
# --------------------------------------------------------------------------- #
def step_warehouse() -> None:
    _step(3, "Build warehouse (generate -> load raw -> dbt seed/run/test)")
    seed = _REPO_ROOT / "scripts" / "seed.py"
    if not seed.exists():
        _fail(f"scripts/seed.py not found at {seed}")
    print(f"  $ python {seed}")
    completed = subprocess.run(
        [sys.executable, str(seed)], cwd=str(_REPO_ROOT), check=False,
    )
    if completed.returncode != 0:
        _fail("warehouse build failed (see seed.py output above).")


# --------------------------------------------------------------------------- #
# 4/5. Qdrant collection + sample document indexing                          #
# --------------------------------------------------------------------------- #
def _retrieval(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "retrieval.cli", *args],
        cwd=str(_REPO_ROOT), check=False, capture_output=True, text=True,
    )


def _collection_count() -> int:
    try:
        r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION}", timeout=10.0)
        if r.status_code == 404:
            return -1
        r.raise_for_status()
        return int(r.json().get("result", {}).get("points_count") or 0)
    except Exception:  # noqa: BLE001
        return -1


def step_collection() -> None:
    _step(4, f"Create Qdrant '{COLLECTION}' collection")
    if _collection_count() >= 0:
        print("  - collection already exists, skipping")
        return
    proc = _retrieval("setup")
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        _fail(f"`retrieval.cli setup` failed:\n{proc.stderr}")


def step_index() -> None:
    _step(5, "Index sample documents into Qdrant")
    count = _collection_count()
    if count > 0:
        print(f"  - collection already has {count} points, skipping index")
        return
    proc = _retrieval("index")  # no path -> built-in sample documents
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        _fail(f"`retrieval.cli index` failed:\n{proc.stderr}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-models", action="store_true",
                        help="do not pull Ollama models")
    parser.add_argument("--skip-warehouse", action="store_true",
                        help="do not build the warehouse")
    args = parser.parse_args(argv)

    print("InsightGPT bootstrap — idempotent, safe to re-run.")
    step_wait()
    step_models(args.skip_models)
    if not args.skip_warehouse:
        step_warehouse()
    else:
        _step(3, "Build warehouse")
        print("  - --skip-warehouse set, skipping")
    step_collection()
    step_index()

    print("\nBootstrap complete. The stack is demo-ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
