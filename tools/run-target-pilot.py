"""Build fictional AI Gateway pilot artifacts without changing client configuration."""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mcp_openapi_creator_kit.consumer_export import load_client, preview_plan
from mcp_openapi_creator_kit.export_cli import write_artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pilot", choices=["ai-gateway-preview"])
    args = parser.parse_args()
    manifest, specs = load_client(ROOT, "sample")
    manifest["apis"][0]["mcpTools"] = ["get-customer-context"]
    manifest["targets"] = yaml.safe_load(
        (ROOT / "docs" / "pilots" / f"{args.pilot}.yaml").read_text("utf-8"))
    artifacts = preview_plan(manifest, specs)
    output = write_artifacts(ROOT, "sample", artifacts, namespace="pilot-ai-gateway")
    print(f"Offline fictional pilot: {output}. Not tenant-validated or deployed.")


if __name__ == "__main__":
    main()
