"""Pre-demo check: walk the whole uploaded-dataset path and report each step.

One command to run before standing in front of an audience. It answers the only
question that matters at that moment — *will the demo work on this machine right
now* — and when the answer is no, it says which layer is broken and what to do
about it.

    uv run --project services/api --extra real python scripts/demo_check.py

What it checks, bottom up, stopping at the first layer that cannot support the
next one:

    1.  configuration is present and internally consistent
    2.  Qdrant is reachable
    3.  Ollama is up and the configured embedding + rerank models are pulled
    4.  the reasoning provider's key is valid (an unmetered catalogue call)
    5.  the retrieval backend is installed and its config loads
    6.  an upload is accepted
    7.  indexing completes and reports chunks
    8.  the chunks are really in the vector store, and no secret is
    9.  a query retrieves the uploaded content
    10. an answer in /ask cites the uploaded dataset by name

Safe to run repeatedly. It creates two datasets from the fixture files in
``tests/e2e_datasets/fixtures`` and removes them again through the API's own
``DELETE /datasets/{id}``, which is a filtered delete on ``dataset_id``.

SAFETY (contract §3): this Qdrant instance is shared with another project. The
script NEVER creates, drops, recreates or prunes a collection. ``--scratch``
indexes into ``insightgpt_test_democheck_<pid>`` instead of the configured
collection and drops that one collection at the end, through a guard that
asserts the ``insightgpt_test_`` prefix before the call.

Exit code 0 when every step the machine can support passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "e2e_datasets" / "fixtures"

for _candidate in (
    REPO_ROOT / "services" / "api",
    REPO_ROOT / "services" / "retrieval",
    REPO_ROOT / "tests",
):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from e2e_datasets.qdrant_safety import (  # noqa: E402
    FOREIGN_COLLECTIONS,
    drop_test_collection,
    scratch_collection_name,
)

LIGHTHOUSE = "ketterdam_lighthouse.md"
SECRET_FILE = "deploy_notes.txt"
PLANTED_FACT = "Hestia Marlow"
SECRET_BODIES = (
    "AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK",
    "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK",
    "sk_live_FAKEFAKEFAKEFAKEFAKE",
    "harbour-ops@example.com",
)

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
_MARK = {PASS: "[ PASS ]", FAIL: "[ FAIL ]", SKIP: "[ SKIP ]", WARN: "[ WARN ]"}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
@dataclass
class Step:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)

    def record(self, name: str, status: str, detail: str = "", fix: str = "") -> Step:
        step = Step(name, status, detail, fix)
        self.steps.append(step)
        print(f"{_MARK[status]} {name}")
        if detail:
            for line in detail.splitlines():
                print(f"         {line}")
        if status in (FAIL, WARN) and fix:
            for index, line in enumerate(fix.splitlines()):
                prefix = "         FIX: " if index == 0 else "              "
                print(f"{prefix}{line}")
        sys.stdout.flush()
        return step

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if s.status == FAIL]

    def summarise(self) -> int:
        counts = {key: sum(1 for s in self.steps if s.status == key) for key in _MARK}
        print("\n" + "-" * 70)
        print(
            f"{counts[PASS]} passed, {counts[FAIL]} failed, "
            f"{counts[WARN]} warning(s), {counts[SKIP]} skipped"
        )
        if not self.failed:
            print("The uploaded-dataset demo path is working on this machine.")
            return 0
        print("\nNot ready. Fix these, in this order:")
        for step in self.failed:
            print(f"  * {step.name}")
            for line in (step.fix or "no suggested fix recorded").splitlines():
                print(f"      {line}")
        return 1


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _service_up(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    import httpx

    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        return True, f"HTTP {response.status_code}"
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP {exc.response.status_code}"
    except (httpx.HTTPError, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _qdrant_url() -> str:
    return _env("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")


def _scroll(collection: str, dataset_id: str | None = None) -> list[dict[str, Any]]:
    import httpx

    body: dict[str, Any] = {"limit": 500, "with_payload": True, "with_vector": False}
    if dataset_id is not None:
        body["filter"] = {"must": [{"key": "dataset_id", "match": {"value": dataset_id}}]}
    points: list[dict[str, Any]] = []
    while True:
        response = httpx.post(
            f"{_qdrant_url()}/collections/{collection}/points/scroll", json=body, timeout=60
        )
        response.raise_for_status()
        result = response.json()["result"]
        points.extend(result.get("points", []))
        offset = result.get("next_page_offset")
        if not offset:
            return points
        body["offset"] = offset


# --------------------------------------------------------------------------
# Steps 1-5: the machine
# --------------------------------------------------------------------------
def check_config(report: Report) -> bool:
    """Configuration present and internally consistent."""
    # The reasoning key is reported but is NOT fatal to the rest of the walk:
    # indexing and retrieval are entirely local, so a missing cloud key must not
    # hide a broken local RAG path (or the other way round).
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        report.record(
            "1. configuration file",
            FAIL,
            f"{env_file} does not exist",
            "cp .env.example .env    then fill in GEMINI_API_KEY and JWT_SECRET",
        )
        return False

    # app.config loads .env into os.environ for anything not already set.
    from app.config import get_settings

    settings = get_settings()
    report.record("1. configuration file", PASS, f"{env_file}")

    ok = True
    provider = _env("LLM_PROVIDER", "ollama").lower()
    if provider == "fake":
        report.record(
            "1b. reasoning provider",
            FAIL,
            "LLM_PROVIDER=fake answers from a fixed template",
            "set LLM_PROVIDER=gemini in .env. With 'fake', every answer is retail "
            "boilerplate regardless of what was uploaded - it will read as a "
            "fabricated answer in front of an audience.",
        )
    elif provider in ("gemini", "openai", "groq"):
        key_env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}[
            provider
        ]
        if not _env(key_env):
            report.record(
                "1b. reasoning provider",
                FAIL,
                f"LLM_PROVIDER={provider} but {key_env} is empty",
                f"put {key_env}=<your key> in .env (never in the repository).\n"
                "The API refuses to start without it, so every step below this one "
                "will report that it could not boot the app.",
            )
        else:
            report.record(
                "1b. reasoning provider",
                PASS,
                f"{provider}, model={_env('LLM_MODEL') or '(provider default)'}",
            )
    else:
        report.record("1b. reasoning provider", WARN, f"LLM_PROVIDER={provider}")

    if settings.retriever != "qdrant":
        report.record(
            "1c. retriever selection",
            FAIL,
            f"RETRIEVER={settings.retriever!r}",
            "set RETRIEVER=qdrant in .env. The fixture retriever reads a fixed in-repo "
            "corpus and can never see an uploaded file, so an upload would be indexed "
            "and then never searched.",
        )
        ok = False
    else:
        report.record("1c. retriever selection", PASS, "RETRIEVER=qdrant")

    for name in ("EMBED_MODEL", "RERANK_MODEL", "JWT_SECRET"):
        if not _env(name):
            report.record(
                f"1d. {name}",
                FAIL,
                f"{name} is not set",
                f"set {name} in .env (see .env.example for the reference value)",
            )
            ok = False
    return ok


def check_qdrant(report: Report) -> bool:
    url = _qdrant_url()
    up, detail = _service_up(f"{url}/readyz")
    if not up:
        report.record(
            "2. Qdrant reachable",
            FAIL,
            f"{url}/readyz -> {detail}",
            "start the container that is already configured for it:\n"
            "  docker start rememory-qdrant\n"
            "then re-run. Do NOT recreate or reset it: this instance holds another "
            "project's data.",
        )
        return False

    from e2e_datasets.qdrant_safety import list_collections

    collections = list_collections(url=url)
    shared = [c for c in collections if c in FOREIGN_COLLECTIONS]
    report.record(
        "2. Qdrant reachable",
        PASS,
        f"{url} ({len(collections)} collection(s))\n"
        + (
            f"shared with another project: {', '.join(shared)} - not touched"
            if shared
            else "no foreign collections present"
        ),
    )
    return True


def check_ollama(report: Report) -> bool:
    host = _env("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    up, detail = _service_up(f"{host}/api/tags")
    if not up:
        report.record(
            "3. Ollama reachable",
            FAIL,
            f"{host}/api/tags -> {detail}",
            "start Ollama (`ollama serve`, or the desktop app) and re-run. Embeddings "
            "and reranking are local, so nothing can be indexed without it.",
        )
        return False

    import httpx

    tags = httpx.get(f"{host}/api/tags", timeout=10).json().get("models", [])
    available = {m.get("name", "") for m in tags}
    # Ollama reports "name:tag"; a configured name without a tag means ":latest".
    normalised = {name.split(":")[0] for name in available} | available
    report.record("3. Ollama reachable", PASS, f"{host} ({len(available)} model(s))")

    ok = True
    for variable in ("EMBED_MODEL", "RERANK_MODEL"):
        model = _env(variable)
        if not model:
            continue
        if model in available or model.split(":")[0] in normalised:
            report.record(f"3b. {variable} pulled", PASS, model)
        else:
            report.record(
                f"3b. {variable} pulled",
                FAIL,
                f"{model} is not among the pulled models",
                f"ollama pull {model}\n"
                "(or run `python scripts/bootstrap.py`, which pulls both)",
            )
            ok = False
    return ok


def check_reasoning_key(report: Report) -> bool:
    provider_name = _env("LLM_PROVIDER", "ollama").lower()
    if provider_name not in ("gemini", "openai", "groq"):
        report.record("4. reasoning key valid", SKIP, f"LLM_PROVIDER={provider_name}")
        return True

    from app.providers.base import ProviderError
    from app.providers.factory import get_provider

    try:
        provider = get_provider(provider_name)
    except ProviderError as exc:
        report.record(
            "4. reasoning key valid",
            FAIL,
            str(exc),
            "put the provider's key in .env and re-run",
        )
        return False

    health = getattr(provider, "health_check", None)
    if health is None:
        report.record("4. reasoning key valid", SKIP, "provider exposes no health check")
        return True
    ok, detail = health(timeout=15.0)
    if ok:
        report.record("4. reasoning key valid", PASS, detail)
        return True
    report.record(
        "4. reasoning key valid",
        FAIL,
        detail,
        "check GEMINI_API_KEY in .env, and that LLM_MODEL names a model the key can "
        "reach. A retired model id reads as an authentication failure.\n"
        "The key is issued at Google AI Studio; the check above is an unmetered "
        "catalogue call, so a failure here is configuration, not quota.",
    )
    return False


def check_retrieval_backend(report: Report, collection: str) -> bool:
    try:
        import retrieval  # noqa: F401
    except ImportError as exc:
        report.record(
            "5. retrieval backend installed",
            FAIL,
            f"{exc}",
            "run this script through the API project with the retrieval extra:\n"
            "  uv run --project services/api --extra real python scripts/demo_check.py",
        )
        return False

    try:
        from retrieval.config import get_config

        get_config.cache_clear()
        config = get_config()
    except SystemExit as exc:
        report.record(
            "5. retrieval backend installed",
            FAIL,
            f"its config could not be loaded: {exc}",
            "config/retrieval.yaml is not shipped inside the installed package. Either "
            "run from the repository root (the source tree is found on sys.path), or "
            "install the retrieval package editable:\n"
            "  uv pip install -e services/retrieval",
        )
        return False

    if not config.collection.startswith("insightgpt_"):
        report.record(
            "5. retrieval backend installed",
            FAIL,
            f"collection={config.collection!r}",
            "set QDRANT_COLLECTION=insightgpt_documents in .env. The prefix is what "
            "keeps this deployment from ever addressing the other project's "
            f"collections ({', '.join(FOREIGN_COLLECTIONS)}).",
        )
        return False

    report.record(
        "5. retrieval backend installed",
        PASS,
        f"collection={config.collection}, embed={config.embedding.model} "
        f"({config.embedding.dimensions}-d)",
    )
    if config.collection != collection:
        report.record(
            "5b. collection in use",
            WARN,
            f"indexing into {config.collection!r}, not the requested {collection!r}",
        )
    return True


# --------------------------------------------------------------------------
# Steps 6-10: the journey
# --------------------------------------------------------------------------
API = "/api/v1"


def _login(client: Any) -> str | None:
    response = client.post(
        f"{API}/auth/login",
        json={"email": "analyst@insightgpt.dev", "password": "analyst-pass"},
    )
    if response.status_code != 200:
        return None
    return response.json()["access_token"]


def run_journey(report: Report, collection: str) -> None:
    from fastapi.testclient import TestClient

    created: list[dict[str, Any]] = []
    try:
        # Imported inside the guard on purpose: app/api/main.py builds the app at
        # module scope, so a configuration the app refuses to boot with raises on
        # the IMPORT, not on the call.
        from app.api.main import create_app

        app = create_app()
    except Exception as exc:  # noqa: BLE001 — a boot refusal is the finding
        report.record(
            "6. upload accepted",
            FAIL,
            f"the API refused to start: {type(exc).__name__}: {exc}",
            "fix the configuration named in the message above, then re-run. The checks "
            "below cannot run until the app boots.",
        )
        return
    client = TestClient(app)
    try:
        token = _login(client)
        if token is None:
            report.record(
                "6. upload accepted",
                FAIL,
                "could not sign in as the demo analyst account",
                "check JWT_SECRET in .env, then re-run",
            )
            return
        headers = {"Authorization": f"Bearer {token}"}

        # --- 6. upload ----------------------------------------------------
        for name in (LIGHTHOUSE, SECRET_FILE):
            response = client.post(
                f"{API}/datasets",
                files=[("files", (name, (FIXTURES / name).read_bytes(), "text/plain"))],
                headers=headers,
            )
            if response.status_code != 202:
                report.record(
                    "6. upload accepted",
                    FAIL,
                    f"{name}: HTTP {response.status_code} {response.text[:300]}",
                    "read the message above: a 403 is a role problem, a 400 a file-type "
                    "problem, a 503 means the vector backend is not wired up.",
                )
                return
            created.append({"id": response.json()["dataset_id"], "name": name})
        report.record(
            "6. upload accepted",
            PASS,
            ", ".join(f"{d['name']} -> {d['id']}" for d in created),
        )

        # --- 7. indexing --------------------------------------------------
        indexing_ok = True
        for dataset in created:
            final = _poll(client, headers, dataset["id"])
            body = client.get(f"{API}/datasets/{dataset['id']}", headers=headers).json()
            dataset.update(body)
            if final.get("status") != "ready":
                indexing_ok = False
                report.record(
                    f"7. indexing completes ({dataset['name']})",
                    FAIL,
                    f"status={final.get('status')!r} error={body.get('error')!r}",
                    "the reason above is the parser's or the embedder's. A dimension "
                    "mismatch means EMBED_MODEL changed since the collection was "
                    "created and the collection needs re-creating BY HAND, with the "
                    "owner's agreement - never by this script.",
                )
            elif not body.get("chunks"):
                indexing_ok = False
                report.record(
                    f"7. indexing completes ({dataset['name']})",
                    FAIL,
                    "status=ready but 0 chunks were written",
                    "a ready dataset with no chunks indexed nothing; check the API log "
                    "for the parse result for this file.",
                )
            else:
                report.record(
                    f"7. indexing completes ({dataset['name']})",
                    PASS,
                    f"{body['chunks']} chunk(s), {body['redactions']} redaction(s)",
                )
        if not indexing_ok:
            return

        # --- 8. the chunks are in the store, the secrets are not ----------
        _check_store(report, collection, created)

        # --- 9. a query retrieves the uploaded content --------------------
        retrieval_ok = _check_retrieval(report)

        # --- 10. an answer cites the dataset ------------------------------
        _check_citation(report, client, headers, retrieval_ok)
    finally:
        _cleanup(report, client, created)
        client.close()


def _poll(client: Any, headers: dict[str, str], dataset_id: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"{API}/datasets/{dataset_id}/status", headers=headers).json()
        if body.get("status") in ("ready", "failed"):
            return body
        time.sleep(0.5)
    return body


def _check_store(report: Report, collection: str, created: list[dict[str, Any]]) -> None:
    try:
        for dataset in created:
            points = _scroll(collection, dataset["id"])
            if len(points) != dataset["chunks"]:
                report.record(
                    f"8. chunks in the vector store ({dataset['name']})",
                    FAIL,
                    f"the API reports {dataset['chunks']} chunk(s) but {len(points)} "
                    f"point(s) in {collection} carry its dataset_id",
                    "the count shown in the UI is not what was written; check that the "
                    "indexer stamps dataset_id onto every point.",
                )
            else:
                report.record(
                    f"8. chunks in the vector store ({dataset['name']})",
                    PASS,
                    f"{len(points)} point(s) in {collection}",
                )

        secret_dataset = next(d for d in created if d["name"] == SECRET_FILE)
        text = " ".join(
            str(value)
            for point in _scroll(collection, secret_dataset["id"])
            for value in (point.get("payload") or {}).values()
        )
        leaked = [secret for secret in SECRET_BODIES if secret in text]
        if leaked:
            report.record(
                "8b. no secret reached Qdrant",
                FAIL,
                f"{len(leaked)} credential-shaped value(s) are in the vector store",
                "redaction must run before embedding. Delete this dataset, fix the "
                "ingestion path, and re-index. Do NOT demo until this passes.",
            )
        else:
            report.record(
                "8b. no secret reached Qdrant",
                PASS,
                f"{len(SECRET_BODIES)} planted secret(s), none stored "
                f"({secret_dataset['redactions']} redaction(s) reported)",
            )
    except Exception as exc:  # noqa: BLE001 — a check that cannot run is a FAIL
        report.record(
            "8. chunks in the vector store",
            FAIL,
            f"{type(exc).__name__}: {exc}",
            f"could not read {collection} from {_qdrant_url()}; check QDRANT_URL",
        )


def _check_retrieval(report: Report) -> bool:
    try:
        from retrieval.config import load_config
        from retrieval.retriever import QdrantRetriever

        results = QdrantRetriever(config=load_config()).search(
            "who replaced the lighthouse lamp and what visibility did they log?", k=5
        )
    except Exception as exc:  # noqa: BLE001
        report.record(
            "9. a query retrieves the upload",
            FAIL,
            f"{type(exc).__name__}: {exc}",
            "the retriever could not run; steps 2, 3 and 5 above say which layer",
        )
        return False

    if not results:
        report.record(
            "9. a query retrieves the upload",
            FAIL,
            "the retriever returned nothing for a document it just indexed",
            "check that EMBED_MODEL is the same model the collection was built with: "
            "querying with a different embedder returns noise or nothing.",
        )
        return False
    if not any(PLANTED_FACT in result.body for result in results):
        report.record(
            "9. a query retrieves the upload",
            FAIL,
            f"top {len(results)} results do not contain {PLANTED_FACT!r}, which appears "
            "in the uploaded file and nowhere else: "
            + ", ".join(f"{r.title}#{r.doc_id}" for r in results),
            "retrieval is returning something, but not the uploaded document. Check "
            "the collection in use and whether reranking is discarding it.",
        )
        return False
    report.record(
        "9. a query retrieves the upload",
        PASS,
        f"the planted fact came back (top score {results[0].score:.3f})",
    )
    return True


def _check_citation(
    report: Report, client: Any, headers: dict[str, str], retrieval_ok: bool
) -> None:
    if not retrieval_ok:
        report.record("10. the answer cites the dataset", SKIP, "step 9 did not pass")
        return
    response = client.post(
        f"{API}/ask",
        json={"question": f"Summarize {LIGHTHOUSE} and the keeper's log", "stream": False},
        headers={**headers, "Accept": "application/json"},
    )
    if response.status_code != 200:
        report.record(
            "10. the answer cites the dataset",
            FAIL,
            f"HTTP {response.status_code} {response.text[:300]}",
            "a 503 is the reasoning provider (step 4); anything else is in the API log",
        )
        return
    envelope = response.json()
    citations = envelope.get("citations") or []
    if not citations:
        report.record(
            "10. the answer cites the dataset",
            FAIL,
            f"route={envelope.get('route')} abstained={envelope.get('abstained')} "
            f"answer={envelope.get('answer', '')[:160]!r}",
            "the document was retrievable in step 9 but /ask returned no citations, so "
            "the engine is not reaching the same points. The known cause is the date "
            "window the router attaches to every document search: an uploaded file's "
            "created_at is its UPLOAD date, and retrieval/search.py::build_filter "
            "applies date_range as a hard filter on created_at, so the upload is "
            "filtered out. See tests/e2e_datasets/test_live_roundtrip.py.",
        )
        return
    named = any(
        LIGHTHOUSE.lower() in str(value).lower()
        for citation in citations
        for value in citation.values()
    )
    if not named:
        report.record(
            "10. the answer cites the dataset",
            FAIL,
            f"{len(citations)} citation(s), none naming {LIGHTHOUSE}: {citations}",
            "an answer has to say which dataset it came from. Carry the dataset name "
            "into the citation.",
        )
        return
    report.record(
        "10. the answer cites the dataset",
        PASS,
        f"{len(citations)} citation(s), naming {LIGHTHOUSE}",
    )


def _cleanup(report: Report, client: Any, created: list[dict[str, Any]]) -> None:
    """Remove only the datasets this run created, through the API's own delete."""
    if not created:
        return
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    removed, stuck = [], []
    for dataset in created:
        response = client.delete(f"{API}/datasets/{dataset['id']}", headers=headers)
        (removed if response.status_code == 200 else stuck).append(dataset["id"])
    if stuck:
        report.record(
            "11. cleanup",
            WARN,
            f"removed {len(removed)}, could not remove: {', '.join(stuck)}",
            "delete them from the Sources page, or re-run once Qdrant is reachable. "
            "They are inert, but they will show up in the demo's dataset list.",
        )
    else:
        report.record("11. cleanup", PASS, f"removed {len(removed)} dataset(s) this run created")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-demo check for the uploaded-dataset path.",
        epilog=(
            "Never creates, drops or resets a Qdrant collection unless --scratch is "
            "given, and then only its own insightgpt_test_* one."
        ),
    )
    parser.add_argument(
        "--scratch",
        action="store_true",
        help=(
            "index into a throwaway insightgpt_test_democheck_<pid> collection and drop "
            "it afterwards, instead of using the configured collection"
        ),
    )
    args = parser.parse_args(argv)

    scratch = scratch_collection_name("democheck") if args.scratch else None
    if scratch:
        os.environ["QDRANT_COLLECTION"] = scratch

    print("InsightGPT demo check")
    print("=" * 70)
    print(f"repository: {REPO_ROOT}")
    print(f"collection: {scratch or _env('QDRANT_COLLECTION') or '(from config)'}")
    print(
        "This script reads the shared Qdrant and writes only its own datasets. It "
        "never drops a collection it did not create."
    )
    print("-" * 70)

    report = Report()
    try:
        if not check_config(report):
            return report.summarise()
        if not check_qdrant(report):
            return report.summarise()
        if not check_ollama(report):
            return report.summarise()
        check_reasoning_key(report)

        collection = scratch or ""
        if not check_retrieval_backend(report, collection or "(configured)"):
            return report.summarise()
        if not collection:
            from retrieval.config import get_config

            collection = get_config().collection

        run_journey(report, collection)
        return report.summarise()
    finally:
        if scratch:
            dropped = drop_test_collection(scratch)
            print(
                f"\nscratch collection {scratch}: "
                + ("dropped" if dropped else "nothing to drop")
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
