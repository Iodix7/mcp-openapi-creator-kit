#!/usr/bin/env python3
"""Reconcile every client before a full azd provisioning run."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLY_ENV = "MCP_RECONCILE_APPLY"
REMOVED_CLIENTS_FILE = REPO_ROOT / "clients" / "removed-clients.yaml"


def env_requests_apply() -> bool:
    return os.environ.get(APPLY_ENV, "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def removed_client_ids(active_ids: set[str]) -> list[str]:
    if not REMOVED_CLIENTS_FILE.exists():
        return []
    data = yaml.safe_load(REMOVED_CLIENTS_FILE.read_text(encoding="utf-8")) or {}
    clients = data.get("clients", []) if isinstance(data, dict) else None
    if not isinstance(clients, list) or not all(
            isinstance(client, str) and client for client in clients):
        raise RuntimeError(f"{REMOVED_CLIENTS_FILE}: clients must be a list of IDs")
    overlap = active_ids.intersection(clients)
    if overlap:
        raise RuntimeError(
            "clients cannot be both active and removed: " + ", ".join(sorted(overlap)))
    return sorted(set(clients))


def run(args: list[str], capture=False) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        raise RuntimeError(f"command '{args[0]}' not found")
    process = subprocess.run([executable, *args[1:]], cwd=REPO_ROOT,
                             capture_output=capture, text=True)
    if process.returncode != 0:
        detail = (process.stderr or "").strip() if capture else ""
        raise RuntimeError(f"command failed: {' '.join(args)}"
                           + (f"\n{detail}" if detail else ""))
    return process.stdout if capture else ""


def azd_env() -> dict[str, str]:
    values = {}
    for line in run(["azd", "env", "get-values"], capture=True).splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-if-env", action="store_true",
                        help=f"apply only if {APPLY_ENV}=true")
    parser.add_argument("--skip-if-unprovisioned", action="store_true")
    args = parser.parse_args()
    apply = args.apply or (args.apply_if_env and env_requests_apply())

    try:
        env = azd_env()
        normalized = {key.replace("_", "").lower(): value
                      for key, value in env.items()}
        apim = normalized.get("apimname")
        subscription = normalized.get("azuresubscriptionid")
        resource_group = normalized.get("azureresourcegroup")
        profile = normalized.get("gatewayprofile", "native-mcp")
        if not apim:
            if args.skip_if_unprovisioned:
                print("[reconcile-all] environment not provisioned yet: skip")
                return
            raise RuntimeError("apimName missing in azd environment")
        if not (subscription and resource_group):
            raise RuntimeError("subscription or resource group missing in azd environment")

        clients = sorted(path.parent for path in
                         (REPO_ROOT / "clients").glob("*/mcp-manifest.yaml"))
        active_ids = {client.name for client in clients}
        removed_ids = removed_client_ids(active_ids)
        print(f"[reconcile-all] {len(clients)} client attivi, "
              f"{len(removed_ids)} removed on {apim} ({profile})")
        for client_dir in clients:
            command = [sys.executable, "tools/reconcile-client.py",
                       str(client_dir.relative_to(REPO_ROOT)),
                       "--profile", profile,
                       "--subscription", subscription,
                       "--resource-group", resource_group,
                       "--apim-name", apim]
            if apply:
                command.append("--apply")
            run(command)
        for client_id in removed_ids:
            command = [sys.executable, "tools/reconcile-client.py",
                       "--removed-client", client_id,
                       "--profile", profile,
                       "--subscription", subscription,
                       "--resource-group", resource_group,
                       "--apim-name", apim]
            if apply:
                command.append("--apply")
            run(command)
    except RuntimeError as error:
        print(f"[reconcile-all] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
