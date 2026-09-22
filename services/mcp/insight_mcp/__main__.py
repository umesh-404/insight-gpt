"""Entry point: ``python -m insight_mcp`` (stdio) or ``--print-config``.

Two modes, both deliberately boring:

* no arguments — run the MCP server on stdio. This is how a client launches it.
* ``--print-config`` — print (and save) ready-to-paste client configuration
  with the absolute paths for this machine, and exit without touching a backend.
"""

from __future__ import annotations

import argparse
import sys

from . import SERVER_VERSION
from .connect import print_config


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="insight-mcp",
        description="InsightGPT MCP server — governed metrics over the Model Context Protocol.",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print ready-to-paste MCP client configuration for this machine and exit",
    )
    parser.add_argument("--version", action="version", version=SERVER_VERSION)
    args = parser.parse_args()

    if args.print_config:
        return print_config()

    # Imported here so --print-config never pays for the SDK or the engine.
    from .context import log
    from .server import TOOL_NAMES, app

    log(f"starting on stdio (tools: {', '.join(TOOL_NAMES)})")
    app.run()  # stdio transport
    return 0


if __name__ == "__main__":
    sys.exit(main())
