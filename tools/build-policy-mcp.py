#!/usr/bin/env python3
"""Build manifest-driven MCP policy servers for APIM Consumption."""
import argparse
import sys
from pathlib import Path

import yaml

from mcp_policy import (POLICY_LIMIT_BYTES, PolicyBuildError, build_client_plan,
                        emit_clients_index, write_client_plan)

REPO_ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client", nargs="?", help="directory clients/<id>")
    parser.add_argument("--all", action="store_true", help="build all clients")
    parser.add_argument("--allow-incompatible", action="store_true",
                        help="with --all, emit stubs for non-mock clients")
    parser.add_argument("--limit-bytes", type=int, default=POLICY_LIMIT_BYTES)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    if args.all == bool(args.client):
        parser.error("specify a client or --all")
    client_dirs = (sorted(path.parent for path in (REPO_ROOT / "clients").glob(
                   "*/mcp-manifest.yaml")) if args.all else [(REPO_ROOT / args.client).resolve()])
    plans = []
    for client_dir in client_dirs:
        if not (client_dir / "mcp-manifest.yaml").exists():
            print(f"[build-policy-mcp] ERROR: manifest not found in {client_dir}",
                  file=sys.stderr)
            raise SystemExit(1)
        try:
            plan = build_client_plan(REPO_ROOT, client_dir, args.limit_bytes)
        except (PolicyBuildError, KeyError, TypeError, ValueError) as error:
            if args.all and args.allow_incompatible and "supports mock backend only" in str(error):
                manifest = yaml.safe_load(
                    (client_dir / "mcp-manifest.yaml").read_text(encoding="utf-8"))
                plan = {"client": manifest["client"],
                        "displayName": manifest["displayName"],
                        "limitBytes": args.limit_bytes, "servers": []}
                print(f"[build-policy-mcp] {plan['client']}: stub disabled "
                      f"({error})")
            else:
                print(f"[build-policy-mcp] ERROR: {error}", file=sys.stderr)
                raise SystemExit(1) from error
        plans.append(plan)
        if not args.report_only:
            output = write_client_plan(client_dir, plan)
            print(f"[build-policy-mcp] artifacts: {output.relative_to(REPO_ROOT)}")
        print(f"[build-policy-mcp] {plan['client']}: {len(plan['servers'])} server")
        for server in plan["servers"]:
            usage = server["sizeBytes"] / plan["limitBytes"] * 100
            print(f"  {server['resourceName']}: {server['sizeBytes']}/{plan['limitBytes']} "
                  f"byte ({usage:.1f}%) - {len(server['tools'])} tool: "
                  f"{', '.join(server['tools'])}")
    if args.all and not args.report_only:
        index = emit_clients_index([plan["client"] for plan in plans])
        (REPO_ROOT / "infra" / "policy-mcp-clients.gen.bicep").write_text(
            index, encoding="utf-8")
        print("[build-policy-mcp] infra/policy-mcp-clients.gen.bicep updated")


if __name__ == "__main__":
    main()
