"""External test-only stdio launcher: replace only the Azure command boundary."""
from pathlib import Path
import runpy
import sys


def main():
    if len(sys.argv) != 2:
        raise ValueError("Expected the isolated customer directory")
    root = Path(sys.argv[1]).resolve(strict=True)
    fixture_path = Path(__file__).with_name("offline_scenarios.py")
    fixture = runpy.run_path(str(fixture_path))["SyntheticAzure"](root)
    from mcp_openapi_creator_kit import gateway
    from mcp_openapi_creator_kit.__main__ import main as serve
    gateway.run_azure = fixture.run
    sys.argv = ["mcp-openapi-creator", "--workspace", str(root)]
    serve()


if __name__ == "__main__":
    main()
