#!/usr/bin/env python3
"""Diff and optionally remove APIM resources orphaned by one client manifest."""
import argparse
import subprocess
import sys
from pathlib import Path

from lifecycle import (AzRestClient, DesiredState, ReconcileError, apply_plan, build_plan,
                       desired_state, discover_owned_apis, format_plan)

REPO_ROOT = Path(__file__).resolve().parent.parent


def azd_env() -> dict[str, str]:
    try:
        process = subprocess.run(["azd", "env", "get-values"], cwd=REPO_ROOT,
                                 capture_output=True, text=True)
    except FileNotFoundError as error:
        raise ReconcileError("command 'azd' not found") from error
    if process.returncode != 0:
        raise ReconcileError(f"azd env get-values failed: {process.stderr.strip()}")
    values = {}
    for line in process.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client", nargs="?", help="directory clients/<id>")
    parser.add_argument("--removed-client",
                        help="removed client: empty desired state")
    parser.add_argument("--apply", action="store_true",
                        help="apply deletions; default is dry-run")
    parser.add_argument("--profile")
    parser.add_argument("--subscription")
    parser.add_argument("--resource-group")
    parser.add_argument("--apim-name")
    args = parser.parse_args()

    if bool(args.client) == bool(args.removed_client):
        parser.error("specify client or --removed-client, not both")
    client_dir = (REPO_ROOT / args.client).resolve() if args.client else None
    client_id = args.removed_client or client_dir.name
    if client_dir:
        manifest_path = client_dir / "mcp-manifest.yaml"
        if not manifest_path.exists():
            print(f"[reconcile-client] ERROR: manifest not found: {manifest_path}",
                  file=sys.stderr)
            raise SystemExit(1)

    try:
        explicit = all((args.profile, args.subscription,
                        args.resource_group, args.apim_name))
        env = {} if explicit else azd_env()
        normalized = {key.replace("_", "").lower(): value
                      for key, value in env.items()}
        profile = args.profile or normalized.get("gatewayprofile", "native-mcp")
        subscription = args.subscription or normalized.get("azuresubscriptionid")
        resource_group = args.resource_group or normalized.get("azureresourcegroup")
        apim_name = args.apim_name or normalized.get("apimname")
        if not (subscription and resource_group and apim_name):
            raise ReconcileError(
                "subscription, resource group, and apimName are required")

        desired = desired_state(client_dir, profile) if client_dir else DesiredState()
        client = AzRestClient(subscription, resource_group, apim_name)
        actual = discover_owned_apis(client, client_id)
        plan = build_plan(client, desired, actual)
    except (ReconcileError, KeyError, ValueError) as error:
        print(f"[reconcile-client] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[reconcile-client] {mode} {client_id} -> {apim_name} ({profile})")
    if plan.empty:
        print("  no orphans")
        return
    for line in format_plan(plan):
        print(f"  {line}")
    if not args.apply:
        print("[reconcile-client] no deletions applied; use --apply")
        return
    try:
        apply_plan(client, plan)
    except ReconcileError as error:
        print(f"[reconcile-client] ERROR during apply: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"[reconcile-client] removed {len(plan.orphan_tools)} tools and "
          f"{len(plan.orphan_apis)} orphan APIs")


if __name__ == "__main__":
    main()
