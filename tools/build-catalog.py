#!/usr/bin/env python3
"""Backward-compatible CLI wrapper for the packaged catalog builder."""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_openapi_creator_kit.catalog import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    main(REPO_ROOT)
