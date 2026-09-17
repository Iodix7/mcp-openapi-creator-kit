#!/usr/bin/env python3
"""Build manifest-driven MCP policy servers for APIM Consumption."""
import argparse
import sys
from pathlib import Path

import yaml

from mcp_openapi_creator_kit.policy import (POLICY_LIMIT_BYTES, PolicyBuildError, build_client_plan,
                        emit_clients_index, preflight_client_plan, write_client_plan)

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
                   "*/mcp-manifest.yaml")) if args.all else [REPO_ROOT / args.client])
    plans = []
    for client_dir in client_dirs:
        if __package__:
            from .deployment import client_path
            client_dir = client_path(REPO_ROOT, str(client_dir))
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
        preflight_client_plan(client_dir, plan)
        print(f"[build-policy-mcp] {plan['client']}: {len(plan['servers'])} server")
        for server in plan["servers"]:
            usage = server["sizeBytes"] / plan["limitBytes"] * 100
            print(f"  {server['resourceName']}: {server['sizeBytes']}/{plan['limitBytes']} "
                  f"byte ({usage:.1f}%) - {len(server['tools'])} tool: "
                  f"{', '.join(server['tools'])}")
    if not args.report_only:
        if __package__:
            from mcp_openapi_creator_kit.progress import begin_step, finish_step, receipt_path
            for directory in client_dirs:
                receipt_path(REPO_ROOT, directory.name, "build-policy")
            attempts = {directory: begin_step(REPO_ROOT, directory.name, "build-policy")
                        for directory in client_dirs}
        if args.all and not __package__:
            from mcp_openapi_creator_kit.data_paths import preflight_outputs
            preflight_outputs(REPO_ROOT, REPO_ROOT / "infra",
                              [REPO_ROOT / "infra" / "policy-mcp-clients.gen.bicep"])
        for client_dir, plan in zip(client_dirs, plans):
            output = write_client_plan(client_dir, plan)
            print(f"[build-policy-mcp] artifacts: {output.relative_to(REPO_ROOT)}")
            if __package__:
                finish_step(REPO_ROOT, client_dir.name, "build-policy", attempts[client_dir])
    if args.all and not args.report_only and not __package__:
        index = emit_clients_index([plan["client"] for plan in plans])
        (REPO_ROOT / "infra" / "policy-mcp-clients.gen.bicep").write_text(
            index, encoding="utf-8", newline="\n")
        print("[build-policy-mcp] infra/policy-mcp-clients.gen.bicep updated")


if __name__ == "__main__":
    main()
