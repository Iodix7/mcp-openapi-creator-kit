"""Console entry point for the local stdio MCP server."""
from __future__ import annotations

import argparse
from pathlib import Path

from .server import create_server


def main():
    parser = argparse.ArgumentParser(
        prog="mcp-openapi-creator",
        description="Run the read-only MCP OpenAPI Creator server over stdio.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="MCP OpenAPI Creator Kit workspace (default: current directory)",
    )
    args = parser.parse_args()
    create_server(args.workspace).mcp.run("stdio")


if __name__ == "__main__":
    main()
