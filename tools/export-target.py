"""Source-checkout entry point for the packaged offline exporter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp_openapi_creator_kit.export_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
