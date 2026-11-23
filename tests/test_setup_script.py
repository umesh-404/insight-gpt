"""Tests for ``scripts/setup.py`` — the first thing a new machine runs.

If setup is wrong, nothing else gets a chance to be right, so the parts that
touch a user's files are pinned here. The Docker-driving steps are not covered
(they need a live engine); what *is* covered is everything that edits ``.env``,
picks ports, and decides whether the environment is usable — the logic that has
to behave identically on a stranger's laptop.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_setup():
    """Import scripts/setup.py by path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location("igpt_setup", REPO / "scripts" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


setup = _load_setup()


# --------------------------------------------------------------------------- #
# .env parsing / editing                                                       #
# --------------------------------------------------------------------------- #
def test_read_env_ignores_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nFOO=bar\n  BAZ = qux  \n# KEY=not_set\nEMPTY=\n",
        encoding="utf-8",
    )
    values = setup.read_env(env)
    assert values["FOO"] == "bar"
    assert values["BAZ"] == "qux"
    assert values["EMPTY"] == ""
    assert "KEY" not in values, "commented-out keys must not be read as set"


def test_read_env_on_missing_file_is_empty(tmp_path: Path) -> None:
    assert setup.read_env(tmp_path / "nope.env") == {}


def test_set_env_value_preserves_comments_and_order(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# leading comment\nFIRST=1\nJWT_SECRET=\n# trailing note\nLAST=9\n",
        encoding="utf-8",
    )
    setup.set_env_value(env, "JWT_SECRET", "abc123")
    text = env.read_text(encoding="utf-8")

    assert "JWT_SECRET=abc123" in text
    assert "# leading comment" in text and "# trailing note" in text
    # Ordering is preserved: the edit is in place, not appended.
    assert text.index("FIRST=1") < text.index("JWT_SECRET=") < text.index("LAST=9")


def test_set_env_value_appends_when_key_is_absent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    setup.set_env_value(env, "NEW_KEY", "value")
    assert setup.read_env(env)["NEW_KEY"] == "value"
    assert setup.read_env(env)["FOO"] == "bar"


def test_set_env_value_does_not_touch_similarly_named_keys(tmp_path: Path) -> None:
    """``API_PORT`` must never be rewritten when setting ``PORT``."""
    env = tmp_path / ".env"
    env.write_text("PORT=1\nAPI_PORT=8000\nWEB_PORT=3000\n", encoding="utf-8")
    setup.set_env_value(env, "PORT", "2")
    values = setup.read_env(env)
    assert values == {"PORT": "2", "API_PORT": "8000", "WEB_PORT": "3000"}


# --------------------------------------------------------------------------- #
# Environment preparation                                                      #
# --------------------------------------------------------------------------- #
def test_step_environment_creates_env_and_fills_secret(tmp_path, monkeypatch) -> None:
    example = tmp_path / ".env.example"
    example.write_text("JWT_SECRET=\nWEB_PORT=3000\n", encoding="utf-8")
    target = tmp_path / ".env"
    monkeypatch.setattr(setup, "ENV_FILE", target)
    monkeypatch.setattr(setup, "ENV_EXAMPLE", example)

    env = setup.step_environment()

    assert target.exists()
    assert len(env["JWT_SECRET"]) >= 32, "a real random secret must be generated"


def test_step_environment_never_overwrites_existing_values(tmp_path, monkeypatch) -> None:
    example = tmp_path / ".env.example"
    example.write_text("JWT_SECRET=\nLLM_PROVIDER=ollama\n", encoding="utf-8")
    target = tmp_path / ".env"
    target.write_text("JWT_SECRET=mine\nLLM_PROVIDER=openai\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV_FILE", target)
    monkeypatch.setattr(setup, "ENV_EXAMPLE", example)

    env = setup.step_environment()

    assert env["JWT_SECRET"] == "mine"
    assert env["LLM_PROVIDER"] == "openai", "a user's provider choice must survive setup"


def test_step_environment_backfills_new_variables(tmp_path, monkeypatch) -> None:
    """A git pull that adds a variable must not require hand-editing .env."""
    example = tmp_path / ".env.example"
    example.write_text("JWT_SECRET=\nOLD=1\nBRAND_NEW=default\n", encoding="utf-8")
    target = tmp_path / ".env"
    target.write_text("JWT_SECRET=mine\nOLD=1\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV_FILE", target)
    monkeypatch.setattr(setup, "ENV_EXAMPLE", example)

    env = setup.step_environment()

    assert env["BRAND_NEW"] == "default"
    assert env["JWT_SECRET"] == "mine"


def test_step_environment_is_idempotent(tmp_path, monkeypatch) -> None:
    example = tmp_path / ".env.example"
    example.write_text("JWT_SECRET=\nFOO=bar\n", encoding="utf-8")
    target = tmp_path / ".env"
    monkeypatch.setattr(setup, "ENV_FILE", target)
    monkeypatch.setattr(setup, "ENV_EXAMPLE", example)

    first = setup.step_environment()
    after_first = target.read_text(encoding="utf-8")
    second = setup.step_environment()

    assert first == second
    assert after_first == target.read_text(encoding="utf-8"), "a re-run must change nothing"


# --------------------------------------------------------------------------- #
# Ports                                                                        #
# --------------------------------------------------------------------------- #
def test_find_free_port_skips_a_bound_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy = taken.getsockname()[1]
        chosen = setup.find_free_port(busy)
        assert chosen != busy
        assert setup.port_is_free(chosen)


def test_step_ports_moves_a_busy_port_and_keeps_urls_consistent(
    tmp_path, monkeypatch
) -> None:
    """Moving API_PORT must also move the URL the browser is told to call.

    Changing one without the other is exactly the kind of half-wiring that
    leaves a UI pointing at a port nothing listens on.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy_api = taken.getsockname()[1]

        target = tmp_path / ".env"
        target.write_text(
            f"WEB_PORT=3000\nAPI_PORT={busy_api}\n"
            f"NEXT_PUBLIC_API_URL=http://localhost:{busy_api}/api/v1\n"
            "CORS_ORIGINS=http://localhost:3000\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(setup, "ENV_FILE", target)
        monkeypatch.setattr(setup, "compose_ps", lambda: [])

        env = setup.step_ports(setup.read_env(target))

    new_api = int(env["API_PORT"])
    assert new_api != busy_api, "a busy port must be reassigned"
    assert env["NEXT_PUBLIC_API_URL"] == f"http://localhost:{new_api}/api/v1"


def test_step_ports_keeps_our_own_running_container_port(tmp_path, monkeypatch) -> None:
    """A port held by our own container is not a conflict — don't move it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy_api = taken.getsockname()[1]

        target = tmp_path / ".env"
        target.write_text(
            f"WEB_PORT=3000\nAPI_PORT={busy_api}\n"
            f"NEXT_PUBLIC_API_URL=http://localhost:{busy_api}/api/v1\n"
            "CORS_ORIGINS=http://localhost:3000\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(setup, "ENV_FILE", target)
        monkeypatch.setattr(
            setup, "compose_ps",
            lambda: [{"Service": "api", "State": "running"},
                     {"Service": "web", "State": "running"}],
        )

        env = setup.step_ports(setup.read_env(target))

    assert int(env["API_PORT"]) == busy_api


# --------------------------------------------------------------------------- #
# Repository sanity                                                            #
# --------------------------------------------------------------------------- #
def test_repo_sanity_passes_on_this_checkout() -> None:
    setup.step_repo_sanity()  # must not raise in a complete clone


def test_repo_sanity_fails_loudly_on_a_partial_checkout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(setup, "REPO", tmp_path)
    with pytest.raises(setup.SetupError) as excinfo:
        setup.step_repo_sanity()
    assert "InsightGPT checkout" in excinfo.value.problem
    assert excinfo.value.fix, "a failure must always carry a fix, not just a symptom"


def test_setup_error_carries_a_fix() -> None:
    err = setup.SetupError("something broke", "do this to fix it")
    assert err.problem == "something broke"
    assert err.fix == "do this to fix it"


# --------------------------------------------------------------------------- #
# Preflight                                                                    #
# --------------------------------------------------------------------------- #
def test_model_present_accepts_a_bare_name_as_latest() -> None:
    """Ollama lists ``nomic-embed-text:latest``; .env names it without the tag."""
    installed = {"nomic-embed-text:latest", "dengcao/Qwen3-Reranker-0.6B:F16"}
    assert setup._model_is_present(installed, "nomic-embed-text")
    assert setup._model_is_present(installed, "nomic-embed-text:latest")
    assert setup._model_is_present(installed, "dengcao/Qwen3-Reranker-0.6B:F16")
    assert not setup._model_is_present(installed, "mxbai-embed-large")
    assert not setup._model_is_present(installed, "")


class _FakeQdrant:
    """Records every URL asked for, so a test can prove what was NOT asked."""

    def __init__(self, collections: list[str]) -> None:
        self.collections = collections
        self.urls: list[str] = []

    def __call__(self, url, timeout=6.0, headers=None):
        self.urls.append(url)
        if url.endswith("/collections"):
            return 200, {"result": {"collections": [{"name": n} for n in self.collections]}}, ""
        name = url.rsplit("/", 1)[-1]
        if name in self.collections:
            return 200, {
                "result": {
                    "points_count": 42,
                    "config": {"params": {"vectors": {"dense": {"size": 768}}}},
                }
            }, ""
        return 404, {"status": {"error": "not found"}}, ""


def test_preflight_rejects_a_collection_outside_our_namespace(monkeypatch) -> None:
    """The instance is shared. A name we do not own is a hard stop, not a warning.

    The assertion that matters is the second one: having refused the name, the
    check must not go on to probe — let alone write to — that collection.
    """
    fake = _FakeQdrant(["code", "docs", "memory"])
    monkeypatch.setattr(setup, "_get_json", fake)
    rep = setup._Report()

    setup._check_qdrant({"QDRANT_URL": "http://127.0.0.1:6333", "QDRANT_COLLECTION": "docs"}, rep)

    assert rep.failures, "a foreign collection name must fail, not warn"
    assert any("namespace" in msg for msg, _ in rep.failures)
    assert not any(url.endswith("/collections/docs") for url in fake.urls), (
        "a collection we do not own must never be probed"
    )


def test_preflight_reports_a_missing_collection_without_failing(monkeypatch) -> None:
    """Before the first upload the collection is absent — that is not broken."""
    fake = _FakeQdrant(["code", "docs", "memory"])
    monkeypatch.setattr(setup, "_get_json", fake)
    rep = setup._Report()

    setup._check_qdrant(
        {"QDRANT_URL": "http://127.0.0.1:6333", "QDRANT_COLLECTION": "insightgpt_documents"},
        rep,
    )

    assert not rep.failures


def test_preflight_catches_a_vector_width_mismatch(monkeypatch) -> None:
    """768-d vectors queried against a 3072-d collection return nonsense, not an error."""
    fake = _FakeQdrant(["insightgpt_documents"])
    monkeypatch.setattr(setup, "_get_json", fake)
    rep = setup._Report()

    setup._check_qdrant(
        {
            "QDRANT_URL": "http://127.0.0.1:6333",
            "QDRANT_COLLECTION": "insightgpt_documents",
            "EMBED_DIMENSIONS": "3072",
        },
        rep,
    )

    assert any("EMBED_DIMENSIONS" in msg for msg, _ in rep.failures)


def test_preflight_fails_when_the_retriever_cannot_see_uploads(tmp_path, monkeypatch) -> None:
    """RETRIEVER=fixture cannot search an uploaded dataset, so it blocks the demo."""
    env_file = tmp_path / ".env"
    env_file.write_text("JWT_SECRET=" + "a" * 64 + "\nRETRIEVER=fixture\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ENV_FILE", env_file)
    rep = setup._Report()

    setup._check_config(setup.read_env(env_file), rep)

    assert any("RETRIEVER" in msg for msg, _ in rep.failures)


def test_preflight_requires_the_provider_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    rep = setup._Report()

    setup._check_provider(
        {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-flash"}, rep, offline=True
    )

    assert any("GEMINI_API_KEY" in msg for msg, _ in rep.failures)


def test_preflight_offline_makes_no_outbound_call(monkeypatch) -> None:
    """--offline must validate what it can locally and call nobody."""
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("--offline must not reach the network")

    monkeypatch.setattr(setup, "_get_json", _boom)
    rep = setup._Report()

    setup._check_provider(
        {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-flash", "GEMINI_API_KEY": "k" * 39},
        rep,
        offline=True,
    )

    assert not rep.failures
