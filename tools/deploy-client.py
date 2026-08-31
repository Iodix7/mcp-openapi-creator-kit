#!/usr/bin/env python3
"""
deploy-client.py - deploy ONE client to an already provisioned APIM.

Usage:
  python tools/deploy-client.py clients/<clientId>
  python tools/deploy-client.py clients/<clientId> --yes

This is the day-to-day command for a single client change (new API,
mock->external switch, manifest update): zero blast radius for other clients
and much faster than `azd up` (which reconciles platform + ALL clients).

What it does:
    1. rebuilds and validates client artifacts (build-facade.py)
    2. reads apimName / keyVaultName / resource group from current azd env
         outputs (azd env get-values), so it requires at least one completed
         `azd up`
    3. runs a targeted ARM deployment for clients/<id>/generated/client.bicep

Prerequisite for new secretRef values: the secret must already exist in Key
Vault (az keyvault secret set --vault-name <kv> --name <secretRef> --value ...).
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def die(msg: str):
    print(f"[deploy-client] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(args: list, capture: bool = False) -> str:
    # No shell: pass args as-is. On Windows, shutil.which also resolves .cmd
    # shims for az/azd, which may not be found without resolution.
    exe = shutil.which(args[0])
    if exe is None:
        die(f"command '{args[0]}' not found in PATH")
    proc = subprocess.run([exe, *args[1:]], cwd=REPO_ROOT,
                          capture_output=capture, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() if capture else ""
        die(f"command failed: {' '.join(args)}" + (f"\n{detail}" if detail else ""))
    return proc.stdout if capture else ""


def azd_env() -> dict:
    out = run(["azd", "env", "get-values"], capture=True)
    values = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip().strip('"')
    return values


def confirm_context(env: dict, expected_subscription: str | None):
    norm = {k.replace("_", "").lower(): v for k, v in env.items()}
    subscription = norm.get("azuresubscriptionid")
    account = json.loads(run([
        "az", "account", "show", "--query",
        "{name:name,user:user.name,tenantId:tenantId,id:id}", "-o", "json",
    ], capture=True))
    if str(account.get("id", "")).casefold() != str(subscription).casefold():
        die("Azure CLI subscription does not match AZURE_SUBSCRIPTION_ID in "
            "the current azd environment")
    details = {
        "Account": account.get("user") or account.get("name") or "(unknown)",
        "Tenant": account.get("tenantId") or norm.get("azuretenantid") or "(unknown)",
        "Subscription": subscription,
        "azd environment": norm.get("azureenvname") or "(unknown)",
        "Resource group": norm.get("azureresourcegroup"),
        "APIM": norm.get("apimname"),
        "GATEWAY_PROFILE": norm.get("gatewayprofile", "native-mcp"),
    }
    print("[deploy-client] Azure deployment context")
    for label, value in details.items():
        print(f"  {label}: {value}")
    confirmation = expected_subscription
    if confirmation is None:
        confirmation = input(
            "[deploy-client] Retype the subscription ID to continue: ").strip()
    if str(confirmation).casefold() != str(subscription).casefold():
        die("deployment confirmation does not match AZURE_SUBSCRIPTION_ID")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client", help="directory clients/<clientId>")
    parser.add_argument(
        "--confirm-subscription",
        help="non-interactive safety confirmation; must equal AZURE_SUBSCRIPTION_ID",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="apply the reviewed reconciliation plan and deploy",
    )
    args = parser.parse_args()
    client_dir = REPO_ROOT / args.client
    client_id = client_dir.name
    if not (client_dir / "mcp-manifest.yaml").exists():
        die(f"{args.client}: mcp-manifest.yaml not found")

    # 1. Build + client validations (also regenerates the index).
    run([sys.executable, "tools/build-facade.py", args.client])

    # 2. Context from azd environment (outputs from the latest azd up).
    # Case/underscore-insensitive lookup: azd stores original names, but this
    # script should not depend on that detail.
    env = azd_env()
    norm = {k.replace("_", "").lower(): v for k, v in env.items()}
    apim = norm.get("apimname")
    kv = norm.get("keyvaultname")
    rg = norm.get("azureresourcegroup")
    sub = norm.get("azuresubscriptionid")
    gateway_profile = norm.get("gatewayprofile", "native-mcp")
    if not (apim and rg and sub) or (gateway_profile == "native-mcp" and not kv):
        die("apimName/AZURE_RESOURCE_GROUP/AZURE_SUBSCRIPTION_ID (e keyVaultName "
            "with native-mcp) "
            "not found in azd environment: run a full `azd up` first "
            "(it provisions platform and persists outputs)")
    confirm_context(env, args.confirm_subscription)

    run([sys.executable, "tools/validate-deployment-profile.py",
         "--profile", gateway_profile, args.client])
    enable_native_mcp = str(gateway_profile == "native-mcp").lower()
    if gateway_profile == "policy-mcp-consumption":
        run([sys.executable, "tools/build-policy-mcp.py", args.client])

    reconcile = [
        sys.executable, "tools/reconcile-client.py", args.client,
        "--profile", gateway_profile,
        "--subscription", sub,
        "--resource-group", rg,
        "--apim-name", apim,
    ]
    plan = run(reconcile, capture=True)
    print(plan, end="" if plan.endswith("\n") else "\n")
    if not args.yes:
        print("[deploy-client] preview completed; review every planned DELETE, "
              "then rerun with --yes to apply and deploy")
        return

    # Reconcile BEFORE OpenAPI import: an orphan native MCP tool can reference
    # a removed operation and break deployment.
    run([*reconcile, "--apply"])

    # 3. Targeted deployment for this client only. Subscription is pinned from
    # azd env: CLI default may change, this deployment must not.
    print(f"[deploy-client] {client_id} -> APIM '{apim}' (rg {rg})")
    run(["az", "deployment", "group", "create", "--subscription", sub,
         "--resource-group", rg,
         "--name", f"client-{client_id}",
         "--template-file", f"clients/{client_id}/generated/client.bicep",
         "--parameters", f"apimName={apim}", f"keyVaultName={kv or ''}",
         f"enableNativeMcp={enable_native_mcp}"])
    if gateway_profile == "policy-mcp-consumption":
        run(["az", "deployment", "group", "create", "--subscription", sub,
             "--resource-group", rg,
             "--name", f"policy-mcp-client-{client_id}",
             "--template-file",
             f"clients/{client_id}/generated/policy-mcp/client.bicep",
             "--parameters", f"apimName={apim}"])
    print(f"[deploy-client] {client_id}: deployment completed")


if __name__ == "__main__":
    main()
