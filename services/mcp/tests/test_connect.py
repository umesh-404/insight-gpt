"""The connection helper prints something a client can actually use — and the
printed command really does bring up a working MCP session over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from insight_mcp.connect import (
    FIXTURE_ENV,
    PACKAGE_DIR,
    REAL_ENV,
    client_config,
    render,
    server_entry,
)


def test_server_entry_uses_absolute_paths() -> None:
    entry = server_entry()
    args = entry["args"]
    assert args[:2] == ["run", "--directory"]
    assert args[-2:] == ["-m", "insight_mcp"]
    # An MCP client launches the server outside the user's shell, so a relative
    # working directory would resolve to the wrong place (or nowhere).
    assert args[2] == str(PACKAGE_DIR)
    assert PACKAGE_DIR.is_absolute()


def test_client_config_defaults_to_the_offline_stack() -> None:
    config = client_config()
    server = config["mcpServers"]["insightgpt"]
    assert server["env"] == FIXTURE_ENV
    assert server["env"]["WAREHOUSE"] == "duckdb"
    assert server["env"]["RETRIEVER"] == "fixture"
    # It must be valid JSON — it is meant to be pasted verbatim.
    assert json.loads(json.dumps(config)) == config


def test_real_backend_template_carries_no_secret_value() -> None:
    assert "<password>" in REAL_ENV["POSTGRES_DSN"]
    for key, value in REAL_ENV.items():
        assert "API_KEY" not in key, "provider keys belong in the operator's environment"
        assert value


def test_render_shows_both_stacks_and_the_command() -> None:
    text = render()
    assert "insight_mcp" in text
    assert "Offline fixture stack" in text
    assert "Real backends" in text
    assert "system_status" in text


def test_a_real_client_can_connect_over_stdio_and_query() -> None:
    """The end-to-end proof: launch the server as a subprocess and speak MCP.

    Everything above tests the server in-process. This is the only test that
    exercises what a client actually does — spawn the command, complete the
    initialize handshake, list tools, and get a governed number back — so a
    protocol-level regression (a stray write to stdout, a bad schema) cannot
    pass unnoticed.
    """
    env = dict(
        os.environ,
        WAREHOUSE="duckdb",
        RETRIEVER="fixture",
        LLM_PROVIDER="fake",
        PYTHONIOENCODING="utf-8",
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "insight_mcp"], env=env
    )

    async def session() -> tuple[str, list[str], dict]:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            initialized = await client.initialize()
            listed = await client.list_tools()
            result = await client.call_tool(
                "query_metric", {"metric": "orders", "dimensions": ["channel"], "limit": 3}
            )
            assert result.is_error is False
            assert result.structured_content is not None
            return initialized.server_info.name, [t.name for t in listed.tools], (
                result.structured_content
            )

    name, tool_names, body = asyncio.run(asyncio.wait_for(session(), timeout=60))
    assert name == "insightgpt"
    assert "query_metric" in tool_names
    assert body["metric"] == "orders"
    assert body["rows"]
