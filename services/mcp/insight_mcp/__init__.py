"""InsightGPT MCP server — the governed semantic layer over the Model Context Protocol.

The package is ``insight_mcp``, not ``mcp``: a local package named ``mcp`` would
shadow the official SDK package of the same name and break its own imports.
"""

from __future__ import annotations

__all__ = ["SERVER_NAME", "SERVER_VERSION"]

SERVER_NAME = "insightgpt"
SERVER_VERSION = "0.1.0"
