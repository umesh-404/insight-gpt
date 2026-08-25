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
