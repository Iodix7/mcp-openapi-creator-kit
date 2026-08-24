#!/usr/bin/env python3
"""Validate platform-wide constraints before an Azure deployment."""
import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = {"native-mcp", "rest-consumption", "policy-mcp-consumption"}
NETWORK_PROFILES = {"public", "hybrid", "isolated"}


def fail(message: str):
    print(f"[validate-profile] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def az_json(args: list[str]) -> dict:
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("command 'az' not found")
    process = subprocess.run(
        [executable, *args, "--output", "json"], cwd=REPO_ROOT,
        capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "Azure CLI command failed")
    return json.loads(process.stdout)


def validate_azure_resources(profile: str, environment: dict[str, str],
                             runner=az_json) -> list[str]:
    violations = []
    network = environment.get("NETWORK_PROFILE", "public")
    subnet_id = (environment.get("VNET_INTEGRATION_SUBNET_ID")
                 if network == "hybrid"
                 else environment.get("VNET_INJECTION_SUBNET_ID"))
    if profile == "native-mcp" and subnet_id and not environment.get(
            "EXISTING_APIM_NAME"):
        try:
            subnet = runner(["resource", "show", "--ids", subnet_id])
            properties = subnet.get("properties") or {}
            delegations = {
                (item.get("properties") or {}).get("serviceName")
                for item in properties.get("delegations") or []}
            expected = ("Microsoft.Web/serverFarms" if network == "hybrid"
                        else "Microsoft.Web/hostingEnvironments")
            if expected not in delegations:
                violations.append(
                    f"{network} subnet must be delegated to {expected}")
            if network == "isolated":
                prefixes = properties.get("addressPrefixes") or [
                    properties.get("addressPrefix")]
                prefixes = [value for value in prefixes if value]
                if not prefixes or any(
                        ipaddress.ip_network(value).prefixlen > 27
                        for value in prefixes):
                    violations.append("isolated subnet must be /27 or larger")
                if not properties.get("networkSecurityGroup"):
                    violations.append(
                        "isolated subnet requires a network security group")
        except (RuntimeError, ValueError, KeyError) as error:
            violations.append(f"cannot validate {network} subnet: {error}")

    subscription = environment.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = environment.get("AZURE_RESOURCE_GROUP", "")
    telemetry = environment.get("TELEMETRY_MODE", "new")
    if telemetry == "existing" and environment.get("EXISTING_APPINSIGHTS_NAME"):
        try:
            runner([
                "resource", "show", "--subscription", subscription,
                "--resource-group", environment.get(
                    "EXISTING_APPINSIGHTS_RG", "") or resource_group,
                "--resource-type", "Microsoft.Insights/components",
                "--name", environment["EXISTING_APPINSIGHTS_NAME"],
            ])
        except RuntimeError as error:
            violations.append(f"cannot validate existing Application Insights: {error}")

    existing_apim = environment.get("EXISTING_APIM_NAME", "")
    if existing_apim:
        try:
            apim = runner([
                "resource", "show", "--subscription", subscription,
                "--resource-group", resource_group,
                "--resource-type", "Microsoft.ApiManagement/service",
                "--name", existing_apim,
            ])
            identity_type = ((apim.get("identity") or {}).get("type") or "")
            if profile == "native-mcp" and "SystemAssigned" not in identity_type:
                violations.append(
                    "existing APIM requires a system-assigned managed identity")
        except RuntimeError as error:
            violations.append(f"cannot validate existing APIM: {error}")
    return violations


def validate(profile: str, manifest_paths: list[Path], report_only: bool = False,
             environment: dict[str, str] | None = None):
    if profile not in PROFILES:
        fail(f"GATEWAY_PROFILE '{profile}' invalid; use native-mcp | "
             "rest-consumption | policy-mcp-consumption")

    violations = []
    environment = environment or {}
    platform_network = environment.get("NETWORK_PROFILE", "public")
    existing_apim = environment.get("EXISTING_APIM_NAME", "")
    telemetry = environment.get("TELEMETRY_MODE", "new")
    if platform_network not in NETWORK_PROFILES:
        violations.append(
            f"NETWORK_PROFILE={platform_network}; use public | hybrid | isolated")
    if profile != "native-mcp" and platform_network != "public":
        violations.append(
            f"NETWORK_PROFILE={platform_network}; Consumption requires public")
    if profile == "native-mcp" and not existing_apim:
        if (platform_network == "hybrid" and
                not environment.get("VNET_INTEGRATION_SUBNET_ID")):
            violations.append(
                "NETWORK_PROFILE=hybrid requires VNET_INTEGRATION_SUBNET_ID")
        if (platform_network == "isolated" and
                not environment.get("VNET_INJECTION_SUBNET_ID")):
            violations.append(
                "NETWORK_PROFILE=isolated requires VNET_INJECTION_SUBNET_ID")
    if telemetry not in {"new", "existing", "none"}:
        violations.append(
            f"TELEMETRY_MODE={telemetry}; use new | existing | none")
    if telemetry == "existing" and not environment.get("EXISTING_APPINSIGHTS_NAME"):
        violations.append(
            "TELEMETRY_MODE=existing requires EXISTING_APPINSIGHTS_NAME")

    for path in manifest_paths:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        client = manifest.get("client", path.parent.name)
        manifest_network = manifest.get("networkProfile", "public")
        if profile != "native-mcp" and manifest_network != "public":
            violations.append(
                f"{client}: networkProfile={manifest_network}; "
                "Consumption requires public")
        if (profile == "native-mcp" and manifest_network != "public" and
                manifest_network != platform_network and not existing_apim):
            violations.append(
                f"{client}: networkProfile={manifest_network} requires "
                f"NETWORK_PROFILE={manifest_network}, not {platform_network}")
        for api in manifest.get("apis") or []:
            backend_mode = (api.get("backend") or {}).get("mode")
            if backend_mode != "mock":
                violations.append(
                    f"{client}/{api.get('name', '?')}: backend.mode={backend_mode}; "
                    f"{profile} supports mock backend only")

    if violations:
        message = f"{profile} is not deployable:\n  - " + "\n  - ".join(violations)
        if report_only:
            print(f"[validate-profile] REPORT: {message}")
            return violations
        fail(message)
    if profile == "native-mcp":
        print("[validate-profile] native-mcp: valid profile")
        return []
    print(f"[validate-profile] {profile}: {len(manifest_paths)} manifest "
          "valid (REST mock, public network, zero backend compute)")
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="client directory or manifest path")
    parser.add_argument("--profile", default=os.environ.get("GATEWAY_PROFILE",
                                                            "native-mcp"))
    parser.add_argument("--report-only", action="store_true",
                        help="print incompatibilities without failing CI")
    parser.add_argument("--check-azure-resources", action="store_true",
                        help="verify referenced Azure resources with Azure CLI")
    args = parser.parse_args()

    if args.paths:
        manifest_paths = []
        for raw in args.paths:
            path = (REPO_ROOT / raw).resolve()
            manifest_paths.append(path / "mcp-manifest.yaml" if path.is_dir() else path)
    else:
        manifest_paths = sorted((REPO_ROOT / "clients").glob("*/mcp-manifest.yaml"))
    missing = [str(path) for path in manifest_paths if not path.exists()]
    if missing:
        fail(f"manifests not found: {missing}")
    environment = dict(os.environ)
    validate(args.profile, manifest_paths, report_only=args.report_only,
             environment=environment)
    if args.check_azure_resources:
        violations = validate_azure_resources(args.profile, environment)
        if violations:
            fail("Azure resource preflight failed:\n  - " +
                 "\n  - ".join(violations))
        print("[validate-profile] Azure resource preflight passed")


if __name__ == "__main__":
    main()