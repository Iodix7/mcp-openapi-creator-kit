"""Backward-compatible import surface for the packaged policy core."""
from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_openapi_creator_kit.policy import *  # noqa: F401,F403,E402
