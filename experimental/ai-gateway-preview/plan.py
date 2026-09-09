"""Isolated preview automation boundary: export plans, never guess ARM writes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mcp_openapi_creator_kit.export_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
