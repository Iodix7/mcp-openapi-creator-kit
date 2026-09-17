#!/usr/bin/env python3
"""Create only a new public APIM in an explicitly selected existing resource group."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import uuid

from mcp_openapi_creator_kit.assets import kit_root, verify_assets
from mcp_openapi_creator_kit.data_paths import safe_data_path
from mcp_openapi_creator_kit.gateway import verify_active_account
from mcp_openapi_creator_kit.workflow import GatewayTarget

if __package__:
    from .deployment import Context, PROFILES
    from .lifecycle import ReconcileError
    from .local_python import local_python
else:
    from deployment import Context, PROFILES
    from lifecycle import ReconcileError
    from local_python import local_python


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET = "platform/gateway.bicep"
APIM_VERSION = "2024-05-01"
ARM_VERSION = "2022-09-01"
MAX_PAGES = 100
WAIT_SECONDS = 3600
POLL_SECONDS = 15


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def transport():
    # One implementation of Windows batch quoting, locale decoding, bounded
    # process execution and PID-tree cleanup; never load a customer script.
    return importlib.import_module("mcp_openapi_creator_kit._commands.retire-client")


def run(arguments: list[str], *, timeout: int = 60) -> str:
    adapter = transport()
    previous = adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS
    try:
        adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS = REPO_ROOT, timeout
        return adapter.run(arguments, capture=True)
    except UnicodeError as error:
        raise ReconcileError("Azure CLI output could not be decoded; raw output suppressed") from error
    finally:
        adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS = previous


def json_result(arguments: list[str], *, timeout: int = 60) -> dict:
    try:
        result = json.loads(run(arguments, timeout=timeout))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ReconcileError("Incomplete or undecodable Azure JSON; raw output suppressed") from error
    if not isinstance(result, dict) or result.get("error") is not None:
        raise ReconcileError("Azure returned an incomplete/error response; no empty-inventory fallback")
    return result


def verify_context(context):
    verify_active_account(context.target, run)
    cloud = json_result(["az", "cloud", "show", "--query",
                         "{name:name,resourceManager:endpoints.resourceManager}", "--output", "json"])
    if cloud.get("name") != "AzureCloud" or cloud.get("resourceManager") != "https://management.azure.com/":
        raise ReconcileError("Standalone provisioning supports Azure public cloud only; no cloud-context fallback")


@dataclass(frozen=True)
class ProvisionContext:
    account: str
    tenant: str
    subscription: str
    resource_group: str
    location: str
    apim_name: str
    publisher_name: str
    publisher_email: str
    profile: str

    def validate(self):
        Context(self.subscription, self.tenant, self.resource_group,
                self.apim_name, self.profile).validate()
        for value in (self.subscription, self.tenant):
            if str(uuid.UUID(value)) != value.casefold():
                raise ReconcileError("Tenant/subscription must use complete hyphenated UUIDs")
        for value in asdict(self).values():
            if (not isinstance(value, str) or not value.strip() or value != value.strip()
                    or any(ord(character) < 32 or ord(character) == 127 for character in value)):
                raise ReconcileError("Every provisioning field must be explicit nonblank text without control characters")
        if not re.fullmatch(r"[a-z][a-z0-9]{1,39}", self.location):
            raise ReconcileError("--location must be an explicit canonical Azure region, for example westeurope")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,48}[A-Za-z0-9]|[A-Za-z]", self.apim_name):
            raise ReconcileError("APIM name must start with a letter and end with a letter/digit")
        if len(self.publisher_name) > 100 or len(self.publisher_email) > 254:
            raise ReconcileError("Publisher name/email is too long")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.publisher_email):
            raise ReconcileError("--publisher-email must be a publisher email address, not a secret")

    @property
    def target(self):
        return GatewayTarget(account=self.account, tenant=self.tenant,
                             subscription=self.subscription, resource_group=self.resource_group,
                             apim_name=self.apim_name)

    @property
    def resource_id(self):
        return self.target.resource_id

    @property
    def group_id(self):
        return f"/subscriptions/{self.subscription}/resourceGroups/{self.resource_group}"

    @property
    def tier(self):
        return "BasicV2" if self.profile == "native-mcp" else "Consumption"

    @property
    def capacity(self):
        return 1 if self.profile == "native-mcp" else 0

    @property
    def identity(self):
        return "SystemAssigned" if self.profile == "native-mcp" else "None"

    @property
    def creation_id(self):
        return digest({"purpose": "gateway-create-v1", "context": asdict(self),
                       "workspace": str(REPO_ROOT.resolve())})

    @property
    def tags(self):
        return {"mcp-kit-owner": "mcp-openapi-creator-kit", "mcp-kit-creation-id": self.creation_id}

    def parameters(self):
        return {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0",
            "parameters": {key: {"value": value} for key, value in {
                "apimName": self.apim_name, "location": self.location,
                "publisherName": self.publisher_name, "publisherEmail": self.publisher_email,
                "gatewayProfile": self.profile,
                "creationId": self.creation_id,
            }.items()},
        }

    def report(self):
        return {
            "account": self.account, "tenant": self.tenant, "subscription": self.subscription,
            "resourceGroup": self.resource_group, "location": self.location, "apimName": self.apim_name,
            "publisherName": self.publisher_name, "publisherEmail": self.publisher_email,
            "profile": self.profile, "azdEnvironment": "not used",
        }


def input_fingerprint(context: ProvisionContext) -> str:
    info = verify_assets()
    paths = [Path(__file__), Path(transport().__file__), kit_root() / ASSET]
    return digest({"context": asdict(context), "kit": info, "workspace": str(REPO_ROOT.resolve()),
                   "files": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]})


@dataclass(frozen=True)
class Artifacts:
    directory: Path
    contents: dict[str, bytes]

    @property
    def fingerprint(self):
        return digest({name: hashlib.sha256(value).hexdigest() for name, value in self.contents.items()})

    def verify(self):
        for name, expected in self.contents.items():
            path = safe_data_path(REPO_ROOT, self.directory / name)
            if not path.is_file() or path.read_bytes() != expected:
                raise ReconcileError("Provisioning artifacts changed; preview again")


def encode_json(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def prepare_artifacts(context: ProvisionContext, fingerprint: str) -> Artifacts:
    template = json_result(["az", "bicep", "build", "--file", str(kit_root() / ASSET), "--stdout"],
                           timeout=300)
    resources = template.get("resources")
    if (not isinstance(resources, list) or len(resources) != 1
            or not isinstance(resources[0], dict)
            or resources[0].get("type") != "Microsoft.ApiManagement/service"
            or resources[0].get("resources") or resources[0].get("copy")
            or resources[0].get("apiVersion") != APIM_VERSION):
        raise ReconcileError("Trusted gateway template must declare exactly one APIM service")
    contents = {
        "gateway.json": encode_json(template),
        "parameters.json": encode_json(context.parameters()),
        "name-check.json": encode_json({"name": context.apim_name}),
    }
    key = digest({"inputs": fingerprint, "contents": {
        name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}})
    directory = safe_data_path(REPO_ROOT, REPO_ROOT / ".mcp-kit" / "provision" / key)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in contents.items():
        path = safe_data_path(REPO_ROOT, directory / name)
        if path.exists() and path.read_bytes() != value:
            raise ReconcileError("Conflicting provisioning artifacts; preserve them and use a clean workspace")
        if not path.exists():
            path.write_bytes(value)
    artifacts = Artifacts(directory, contents)
    artifacts.verify()
    return artifacts


def rest(context: ProvisionContext, method: str, uri: str, *, body: Path | None = None) -> dict:
    arguments = ["az", "rest", "--method", method, "--subscription", context.subscription,
                 "--uri", uri, "--output", "json"]
    if body is not None:
        arguments.extend(["--body", f"@{body}", "--headers", "Content-Type=application/json"])
    return json_result(arguments)


def collection(context: ProvisionContext, path: str, version: str) -> list[dict]:
    uri = f"{path}?api-version={version}"
    seen = set()
    values = {}
    for _ in range(MAX_PAGES):
        parsed = urlsplit(uri)
        if ((parsed.scheme or parsed.netloc) and
                (parsed.scheme != "https" or parsed.netloc.casefold() != "management.azure.com")):
            raise ReconcileError("ARM pagination escaped the management endpoint")
        if parsed.path.casefold() != path.casefold() or uri in seen or parsed.fragment:
            raise ReconcileError("ARM pagination escaped its exact collection or contains a cycle")
        seen.add(uri)
        result = rest(context, "GET", uri)
        if not isinstance(result.get("value"), list):
            raise ReconcileError("Incomplete ARM collection; never assume an empty inventory")
        for item in result["value"]:
            resource_id = item.get("id") if isinstance(item, dict) else None
            if (not isinstance(resource_id, str) or not resource_id.casefold().startswith(path.casefold() + "/")
                    or "/" in resource_id[len(path) + 1:] or resource_id.casefold() in values):
                raise ReconcileError("Incomplete, duplicate or out-of-scope ARM collection entry")
            values[resource_id.casefold()] = item
        next_link = result.get("nextLink")
        if next_link is None or next_link == "":
            return [values[key] for key in sorted(values)]
        if not isinstance(next_link, str):
            raise ReconcileError("Invalid ARM pagination link")
        uri = next_link
    raise ReconcileError("ARM collection exceeded the bounded page limit")


def inspect_absence(context: ProvisionContext, artifacts: Artifacts, deployment_name: str) -> dict:
    verify_context(context)
    group = rest(context, "GET", f"{context.group_id}?api-version={ARM_VERSION}")
    if (str(group.get("id", "")).casefold() != context.group_id.casefold()
            or not isinstance(group.get("location"), str)
            or not isinstance(group.get("properties"), dict)
            or group["properties"].get("provisioningState") != "Succeeded"):
        raise ReconcileError("Explicit resource group must already exist and be Succeeded; it is never created or updated")
    services = collection(context, f"{context.group_id}/providers/Microsoft.ApiManagement/service", APIM_VERSION)
    if any(item["id"].casefold() == context.resource_id.casefold() for item in services):
        raise ReconcileError("APIM already exists: provisioning never adopts, recovers, replaces or updates it; use deploy")
    deployments = collection(context, f"{context.group_id}/providers/Microsoft.Resources/deployments", ARM_VERSION)
    deployment_id = f"{context.group_id}/providers/Microsoft.Resources/deployments/{deployment_name}"
    if any(item["id"].casefold() == deployment_id.casefold() for item in deployments):
        raise ReconcileError("Provisioning deployment history already exists; never overwrite or resume it automatically")
    available = rest(
        context, "POST",
        f"/subscriptions/{context.subscription}/providers/Microsoft.ApiManagement/checkNameAvailability?api-version={APIM_VERSION}",
        body=artifacts.directory / "name-check.json")
    if available.get("nameAvailable") is not True:
        raise ReconcileError("APIM name availability was not positively confirmed; choose a new name after review")
    return {"resourceGroup": group, "services": services, "deployments": deployments,
            "nameAvailable": True, "operatorAccount": context.account.casefold()}


def desired_gateway(context: ProvisionContext, gateway: dict, *, final: bool = False):
    if (not isinstance(gateway, dict) or not isinstance(gateway.get("sku"), dict)
            or not isinstance(gateway.get("identity") or {}, dict)
            or not isinstance(gateway.get("properties"), dict)
            or type(gateway["sku"].get("capacity")) is not int):
        raise ReconcileError("Gateway resource expansion is incomplete")
    if (str(gateway.get("id", "")).casefold() != context.resource_id.casefold()
            or str(gateway.get("type", "")).casefold() != "microsoft.apimanagement/service"
            or gateway.get("sku", {}).get("name") != context.tier
            or gateway.get("sku", {}).get("capacity") != context.capacity
            or str(gateway.get("location", "")).replace(" ", "").casefold() != context.location
            or (gateway.get("identity") or {}).get("type", "None") != context.identity):
        raise ReconcileError("Gateway identity, resource ID, region or tier differs from the exact creation plan")
    tags = gateway.get("tags")
    if not isinstance(tags, dict) or any(tags.get(name) != value for name, value in context.tags.items()):
        raise ReconcileError("Gateway kit ownership tags differ from the exact creation plan")
    properties = gateway.get("properties") or {}
    if (properties.get("virtualNetworkType") != "None"
            or properties.get("publicNetworkAccess") != "Enabled"
            or properties.get("virtualNetworkConfiguration")
            or properties.get("additionalLocations")
            or (gateway.get("identity") or {}).get("userAssignedIdentities")
            or properties.get("publisherName") != context.publisher_name
            or properties.get("publisherEmail") != context.publisher_email):
        raise ReconcileError("Gateway network/publisher configuration differs from the public creation plan")
    if final:
        if properties.get("provisioningState") != "Succeeded":
            raise ReconcileError("Gateway provisioning state is not Succeeded")
        if context.identity == "SystemAssigned":
            identity = gateway["identity"]
            try:
                if not uuid.UUID(identity.get("principalId", "")).int:
                    raise ValueError("Empty principal")
            except (ValueError, TypeError, AttributeError) as error:
                raise ReconcileError("Native gateway system-assigned principalId is missing/invalid") from error
            if str(identity.get("tenantId", "")).casefold() != context.tenant.casefold():
                raise ReconcileError("Native gateway managed identity tenant differs from the approved tenant")
        if properties.get("gatewayUrl", "").casefold() != f"https://{context.apim_name}.azure-api.net".casefold():
            raise ReconcileError("Gateway URL does not identify the new public APIM")


def summarize_what_if(context: ProvisionContext, result: dict) -> list[dict]:
    if result.get("status") != "Succeeded" or result.get("error") is not None or not isinstance(result.get("changes"), list):
        raise ReconcileError("ARM what-if failed or omitted resource expansion")
    summary = []
    seen = set()
    created = False
    for change in result["changes"]:
        if not isinstance(change, dict):
            raise ReconcileError("Malformed ARM what-if change")
        resource_id = change.get("resourceId")
        change_type = change.get("changeType")
        if (not isinstance(resource_id, str) or resource_id.casefold() in seen
                or not resource_id.casefold().startswith(context.group_id.casefold() + "/providers/")
                or any(part in {".", ".."} for part in resource_id.split("/"))
                or any(ord(character) < 32 or ord(character) == 127 for character in resource_id)
                or any(character in resource_id for character in ("?", "#", "\\"))
                or "%" in resource_id):
            raise ReconcileError("ARM what-if contains duplicate, missing or out-of-scope resource IDs")
        seen.add(resource_id.casefold())
        is_target = resource_id.casefold() == context.resource_id.casefold()
        if is_target:
            if change_type != "Create" or change.get("before"):
                raise ReconcileError("Creation-only plan requires APIM Create; Modify/NoChange/Deploy/Delete are refused")
            desired_gateway(context, change.get("after"))
            created = True
        elif change_type != "Ignore":
            raise ReconcileError("ARM what-if proposes an unrelated resource or identity change; creation blocked")
        # Incremental what-if may list existing RG resources as Ignore. They are
        # displayed and bound to review, but never become deployable resources.
        segments = re.split(r"/providers/", resource_id, flags=re.IGNORECASE)[-1].split("/")
        if len(segments) < 3 or len(segments) % 2 != 1 or any(not segment for segment in segments):
            raise ReconcileError("ARM what-if resource type/identity is malformed")
        summary.append({"resourceId": resource_id, "changeType": change_type,
                        "resourceType": "/".join([segments[0], *segments[1::2]])})
    if not created:
        raise ReconcileError("ARM what-if did not explicitly expand creation of the new APIM")
    return sorted(summary, key=lambda item: item["resourceId"].casefold())


def deployment_command(context: ProvisionContext, artifacts: Artifacts, name: str, operation: str):
    arguments = [
        "az", "deployment", "group", operation, "--subscription", context.subscription,
        "--resource-group", context.resource_group, "--name", name, "--mode", "Incremental",
        "--template-file", str(artifacts.directory / "gateway.json"),
        "--parameters", f"@{artifacts.directory / 'parameters.json'}",
    ]
    if operation == "what-if":
        return [*arguments, "--no-pretty-print", "--result-format", "FullResourcePayloads", "--output", "json"]
    return [*arguments, "--no-wait", "--output", "none"]


def wait_for_gateway(context: ProvisionContext, deployment_name: str) -> dict:
    deadline = time.monotonic() + WAIT_SECONDS
    uri = f"{context.group_id}/providers/Microsoft.Resources/deployments/{deployment_name}?api-version={ARM_VERSION}"
    while time.monotonic() < deadline:
        deployment = rest(context, "GET", uri)
        expected_id = uri.split("?")[0]
        if str(deployment.get("id", "")).casefold() != expected_id.casefold():
            raise ReconcileError("Deployment verification returned the wrong resource identity")
        if not isinstance(deployment.get("properties"), dict):
            raise ReconcileError("Deployment verification returned incomplete properties")
        state = deployment["properties"].get("provisioningState")
        if state == "Succeeded":
            gateway = rest(context, "GET", f"{context.resource_id}?api-version={APIM_VERSION}")
            desired_gateway(context, gateway, final=True)
            verify_context(context)
            return gateway
        if state not in {"Accepted", "Running", "Creating", "Updating"}:
            raise ReconcileError("Provisioning deployment failed, was cancelled or returned an unknown state; inspect privately")
        print(f"[provision-gateway] Waiting for {context.resource_id}; deployment state {state}",
              file=sys.stderr, flush=True)
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
    raise ReconcileError("Provisioning verification timed out; Azure may still be creating APIM. Do not automatically retry")


def receipt_path(artifacts: Artifacts) -> Path:
    path = safe_data_path(REPO_ROOT, artifacts.directory / "creation-receipt.json")
    if path.exists():
        raise ReconcileError("A creation receipt already exists; never overwrite, adopt or automatically recreate the gateway")
    return path


def write_creation_receipt(context: ProvisionContext, artifacts: Artifacts, report: dict, gateway: dict) -> Path:
    """Issue a local audit record only after verified Azure creation, never from pasted facts."""
    path = receipt_path(artifacts)
    etag = gateway.get("etag")
    created_at = gateway["properties"].get("createdAtUtc")
    if (not isinstance(etag, str) or not etag.strip() or etag == "*"
            or any(ord(character) < 32 or ord(character) == 127 for character in etag)):
        raise ReconcileError("Gateway exists but its creation ETag is missing/invalid; no ownership receipt issued. Do not retry creation")
    try:
        if not isinstance(created_at, str):
            raise ValueError("Missing creation timestamp")
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Creation timestamp has no timezone")
    except ValueError as error:
        raise ReconcileError("Gateway exists but createdAtUtc is missing/invalid; no ownership receipt issued. Do not retry creation") from error
    identity = gateway.get("identity") or {}
    receipt = {
        "schemaVersion": 1, "kind": "verified-gateway-create",
        "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
        "workspace": str(REPO_ROOT.resolve()), "context": context.report(),
        "resourceId": context.resource_id, "creationId": context.creation_id,
        "tags": context.tags, "etag": etag, "createdAtUtc": created_at,
        "tier": context.tier, "capacity": context.capacity,
        "identityType": context.identity, "principalId": identity.get("principalId"),
        "identityTenant": identity.get("tenantId"),
        "gatewayUrl": gateway["properties"]["gatewayUrl"],
        "deploymentResourceId": report["deploymentResourceId"],
        "inputFingerprint": report["inputFingerprint"],
        "artifactFingerprint": artifacts.fingerprint, "reviewToken": report["reviewToken"],
        "provisioningState": "Succeeded", "authorizesDeletion": False,
    }
    with path.open("xb") as stream:
        stream.write(encode_json(receipt))
        stream.flush()
        os.fsync(stream.fileno())
    return path


def provision(context: ProvisionContext, *, dry_run: bool = False, confirmation: str | None = None,
              apply: bool = False, review_token: str | None = None) -> dict:
    context.validate()
    if dry_run and (apply or review_token):
        raise ReconcileError("--dry-run cannot apply or authorize a review token")
    if review_token and not apply:
        raise ReconcileError("--review-token is only valid with --yes")
    local_python(REPO_ROOT)
    fingerprint = input_fingerprint(context)
    report = {
        "schemaVersion": 1, "status": "dry-run", "context": context.report(),
        "resourceId": context.resource_id, "tier": context.tier, "capacity": context.capacity,
        "identityType": context.identity, "network": "public", "resourceGroupMode": "existing-only",
        "inputFingerprint": fingerprint, "azureVerified": False, "applied": False,
        "azdUsed": False, "creationId": context.creation_id, "ownershipTags": context.tags,
    }
    print("[provision-gateway] Exact creation-only scope (resource group remains unchanged)")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if dry_run:
        return report
    if confirmation is None:
        if not sys.stdin.isatty():
            raise ReconcileError("Preview requires --confirm-subscription or an interactive context confirmation")
        confirmation = input("Approve this exact context for inspection/what-if by retyping the subscription ID: ").strip()
    if confirmation.casefold() != context.subscription.casefold():
        raise ReconcileError("Context confirmation does not match the explicit subscription")
    verify_context(context)
    artifacts = prepare_artifacts(context, fingerprint)
    name = "mcp-kit-gateway-" + digest({"inputs": fingerprint, "artifacts": artifacts.fingerprint})[:24]
    state = inspect_absence(context, artifacts, name)
    artifacts.verify()
    changes = summarize_what_if(context, json_result(
        deployment_command(context, artifacts, name, "what-if"), timeout=300))
    if input_fingerprint(context) != fingerprint:
        raise ReconcileError("Installed inputs changed during what-if; preview again")
    artifacts.verify()
    token = digest({"purpose": "provision-new-gateway-v1", "inputs": fingerprint,
                    "artifacts": artifacts.fingerprint, "state": state,
                    "deploymentName": name, "changes": changes})
    report.update(status="preview", azureVerified=True, deploymentName=name,
                  deploymentResourceId=f"{context.group_id}/providers/Microsoft.Resources/deployments/{name}",
                  artifactDirectory=str(artifacts.directory), artifactFingerprint=artifacts.fingerprint,
                  reviewToken=token, changes=changes)
    print("[provision-gateway] ARM what-if: exact IDs/change types only; raw payloads suppressed")
    for change in changes:
        print(f"  {change['changeType']} {change['resourceId']}")
    print(f"[provision-gateway] Review token: {token}")
    print("Review this scope before --yes --review-token. Simulation and tokens are not human approval "
          "or a transaction; concurrent writes and Azure Policy/provider effects remain possible.")
    if not apply:
        return report
    if not review_token or review_token != token:
        raise ReconcileError("Missing/stale review token; no apply. Review the refreshed preview")
    if input_fingerprint(context) != fingerprint:
        raise ReconcileError("Installed inputs changed before apply")
    artifacts.verify()
    refreshed = inspect_absence(context, artifacts, name)
    if digest(refreshed) != digest(state):
        raise ReconcileError("Azure inventory/context changed before apply; preview again")
    if input_fingerprint(context) != fingerprint:
        raise ReconcileError("Installed inputs changed during the final inventory check")
    artifacts.verify()
    verify_context(context)
    receipt_path(artifacts)
    run(deployment_command(context, artifacts, name, "create"))
    gateway = wait_for_gateway(context, name)
    try:
        created_receipt = write_creation_receipt(context, artifacts, report, gateway)
    except OSError as error:
        raise ReconcileError("Gateway exists but the durable create receipt could not be saved. Preserve deployment history; do not retry or delete automatically") from error
    identity = gateway.get("identity") or {}
    report.update(status="provisioned", applied=True, provisioningState="Succeeded",
                  gatewayUrl=gateway["properties"]["gatewayUrl"],
                  principalId=identity.get("principalId"), identityTenant=identity.get("tenantId"),
                  creationReceiptPath=str(created_receipt))
    print("[provision-gateway] Verified new APIM. Run fresh gateway inspection, then selective client deploy.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("account", "tenant", "subscription", "resource-group", "location",
                 "apim-name", "publisher-name", "publisher-email"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--dry-run", action="store_true", help="strictly offline scope preview; no Azure, writes or review token")
    parser.add_argument("--confirm-subscription", help="approve the displayed explicit context for inspection/what-if")
    parser.add_argument("--yes", action="store_true", help="apply only after refreshing what-if and validating --review-token")
    parser.add_argument("--review-token", help="exact creation-only token from the preceding preview; required with --yes")
    args = parser.parse_args()
    try:
        context = ProvisionContext(**{field: getattr(args, field) for field in ProvisionContext.__dataclass_fields__})
        result = provision(context, dry_run=args.dry_run, confirmation=args.confirm_subscription,
                           apply=args.yes, review_token=args.review_token)
        print("[provision-gateway] Result")
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return result
    except (ReconcileError, RuntimeError, ValueError, TypeError, KeyError, OSError) as error:
        print(f"[provision-gateway] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
