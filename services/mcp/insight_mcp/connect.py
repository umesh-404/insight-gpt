"""Print ready-to-paste MCP connection config for *this* machine.

The server speaks standard MCP over stdio, so it works with any MCP-compatible
client. What every client needs is the same three things — a command, its
arguments, and (optionally) environment variables — and the only part that is
machine-specific is the absolute paths. This module resolves them and prints
the exact snippet, so nothing has to be hand-edited.

    uv run --directory <repo>/services/mcp -m insight_mcp --print-config

It deliberately writes nothing. The output contains absolute paths that are
true only on this machine, so persisting it inside the repository would either
be committed by accident or need a new ignore rule; redirecting the command to
a file of the operator's choosing is the same convenience with neither problem.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent  # services/mcp

# The offline default. Spelled out so the pasted config is reproducible rather
# than dependent on whatever happens to be in the shell environment.
FIXTURE_ENV = {
    "WAREHOUSE": "duckdb",
    "RETRIEVER": "fixture",
    "LLM_PROVIDER": "fake",
}

# The same server against the real stack. Secrets are never written here — the
# DSN and any API key come from the operator's own environment.
REAL_ENV = {
    "WAREHOUSE": "postgres",
    "POSTGRES_DSN": "postgresql://insight_ro:<password>@127.0.0.1:5432/insight",
    "RETRIEVER": "qdrant",
    "QDRANT_URL": "http://127.0.0.1:6333",
    "LLM_PROVIDER": "ollama",
}


def find_uv() -> str:
    """Absolute path to ``uv``.

    MCP clients launch servers outside the user's shell profile, so a
    PATH-relative command breaks in ways that are miserable to debug. The
    resolved shim path is deliberately NOT expanded to its versioned target:
    on Windows the installer shim is the stable path, and the target moves on
    every upgrade.
    """
    found = shutil.which("uv")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "uv.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "uv"  # last resort; the printed notes tell the reader to fix PATH


def server_entry(env: dict[str, str] | None = None) -> dict[str, object]:
    """The ``{command, args, env}`` block every MCP client config is built from."""
    entry: dict[str, object] = {
        "command": find_uv(),
        "args": ["run", "--directory", str(PACKAGE_DIR), "-m", "insight_mcp"],
    }
    if env:
        entry["env"] = dict(env)
    return entry


def client_config(env: dict[str, str] | None = None) -> dict[str, object]:
    """The generic ``mcpServers`` document most clients accept verbatim."""
    return {"mcpServers": {"insightgpt": server_entry(env or FIXTURE_ENV)}}


def render() -> str:
    """The full human-facing connection guide."""
    offline = json.dumps(client_config(FIXTURE_ENV), indent=2)
    real = json.dumps(client_config(REAL_ENV), indent=2)
    entry = server_entry(FIXTURE_ENV)
    command = " ".join(
        [str(entry["command"])] + [f'"{a}"' if " " in a else a for a in entry["args"]]  # type: ignore[union-attr]
    )
    return f"""
================================================================
  InsightGPT MCP server — connection config for this machine
  Standard MCP over stdio, so any MCP-compatible client works.
================================================================

Server command:
  {command}

--- Offline fixture stack (no external services; start here) ---
{offline}

--- Real backends (Postgres + Qdrant; fill in your own secrets) ---
{real}

Notes
  * Paste the JSON into your client's MCP server configuration file. Clients
    that take a command instead of JSON get the same command and arguments.
  * Secrets (POSTGRES_DSN password, any provider API key) belong in the
    client's env block or your own environment — never in the repository.
  * The offline stack needs nothing running: an in-process DuckDB warehouse and
    a keyword retriever over the sample documents. Confirm the connection by
    calling `system_status`, then `list_metrics`.
  * If the command is not found, `uv` is not on PATH for GUI-launched clients;
    use its absolute path (this command prints the resolved one above).
  * Nothing is written to disk. To keep a copy, redirect this command's output
    to a file outside the repository.
"""


def print_config() -> int:
    print(render())
    return 0
