"""Test fixtures — pin the offline stack before anything imports the server.

The whole suite runs with no external services: an in-process DuckDB fixture
warehouse, a keyword retriever over the sample documents, and the deterministic
``fake`` provider. Settings are read from the environment on first use, so the
environment is set here at import time, before ``insight_mcp.server`` is loaded.

The tool functions are async on the wire but synchronous underneath, so the
helpers below drive them with ``asyncio.run`` rather than pulling in an async
test plugin — one less dependency between the tests and the thing under test.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ["WAREHOUSE"] = "duckdb"
os.environ["RETRIEVER"] = "fixture"
os.environ["LLM_PROVIDER"] = "fake"

from mcp.types import CallToolResult, Tool  # noqa: E402

from insight_mcp.server import app  # noqa: E402


def invoke(name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
    """Call one tool the way the SDK does. Governance rejections raise ``ToolError``."""
    return asyncio.run(app.call_tool(name, arguments or {}))


def structured(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ``structuredContent`` of a successful tool call."""
    result = invoke(name, arguments)
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content


def tools() -> list[Tool]:
    return asyncio.run(app.list_tools())
