#!/usr/bin/env python3
"""Create only one new resource group, as a separately reviewed subscription deployment."""
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
from types import FunctionType
from urllib.parse import parse_qsl, urlsplit
import uuid

from mcp_openapi_creator_kit.assets import kit_root, verify_assets
from mcp_openapi_creator_kit.data_paths import safe_data_path, workspace_root
from mcp_openapi_creator_kit.gateway import verify_active_account

gateway = importlib.import_module("mcp_openapi_creator_kit._commands.provision-gateway")
ReconcileError = gateway.ReconcileError
digest = gateway.digest
encode_json = gateway.encode_json
REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET = "platform/resource-group.bicep"
ARM_VERSION = "2022-09-01"
MAX_PAGES = 100
WAIT_SECONDS = 600
POLL_SECONDS = 5
GROUP_TYPE = "Microsoft.Resources/resourceGroups"


def run(arguments: list[str], *, timeout: int = 60) -> str:
    # Bind a private globals dictionary: reuse the trusted transport without
    # mutating the gateway/retirement command's workspace or timeout.
    adapter = gateway.transport()
    bindings = {**adapter.run.__globals__, "REPO_ROOT": REPO_ROOT,
                "CLI_TIMEOUT_SECONDS": timeout}
    bound = FunctionType(adapter.run.__code__, bindings, adapter.run.__name__,
                         adapter.run.__defaults__, adapter.run.__closure__)
    try:
        return bound(arguments, capture=True)
    except UnicodeError as error:
        raise ReconcileError("Azure CLI output could not be decoded; raw output suppressed") from error


def json_result(arguments: list[str], *, timeout: int = 60) -> dict:
    try:
        result = json.loads(run(arguments, timeout=timeout))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ReconcileError("Incomplete or undecodable Azure JSON; raw output suppressed") from error
    if not isinstance(result, dict) or result.get("error") is not None:
        raise ReconcileError("Incomplete/error Azure response; never assume absence")
    return result


def verify_context(context):
    try:
        verify_active_account(context, run)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ReconcileError("Invalid active account JSON; raw output suppressed") from error
    cloud = json_result(["az", "cloud", "show", "--query",
                         "{name:name,resourceManager:endpoints.resourceManager}", "--output", "json"])
    if cloud != {"name": "AzureCloud", "resourceManager": "https://management.azure.com/"}:
        raise ReconcileError("Resource group provisioning supports Azure public cloud only; no context switch")


@dataclass(frozen=True)
class ProvisionContext:
    account: str
    tenant: str
    subscription: str
    resource_group: str
    location: str

    def validate(self):
        for value in asdict(self).values():
            if (not isinstance(value, str) or not value.strip() or value != value.strip()
                    or any(ord(character) < 32 or ord(character) == 127 for character in value)):
                raise ReconcileError("Every context field must be explicit text without control characters")
        for value in (self.tenant, self.subscription):
            if str(uuid.UUID(value)) != value.casefold():
                raise ReconcileError("Tenant/subscription must be complete hyphenated UUIDs")
        if not valid_name(self.resource_group) or self.resource_group.endswith("."):
            raise ReconcileError("Resource group name must use 1-90 letters/digits, underscores, parentheses, hyphens or dots; no trailing dot")
        if not re.fullmatch(r"[a-z][a-z0-9]{1,39}", self.location):
            raise ReconcileError("--location must be a canonical Azure region such as westeurope")

    @property
    def scope(self):
        return f"/subscriptions/{self.subscription}"

    @property
    def resource_id(self):
        return f"{self.scope}/resourceGroups/{self.resource_group}"

    @property
    def creation_id(self):
        return digest({"purpose": "resource-group-create-v1", "context": asdict(self),
                       "workspace": str(REPO_ROOT.resolve())})

    @property
    def tags(self):
        return {"mcp-kit-owner": "mcp-openapi-creator-kit", "mcp-kit-creation-id": self.creation_id}

    def report(self):
        return {"account": self.account, "tenant": self.tenant, "subscription": self.subscription,
                "resourceGroup": self.resource_group, "location": self.location,
                "azdEnvironment": "not used", "profile": "not applicable (resource-group-only)"}

    def parameters(self):
        return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                "contentVersion": "1.0.0.0",
                "parameters": {key: {"value": value} for key, value in {
                    "resourceGroupName": self.resource_group, "location": self.location,
                    "creationId": self.creation_id}.items()}}


def valid_name(name):
    return isinstance(name, str) and re.fullmatch(r"[\w().-]{1,90}", name) and name not in {".", ".."}


def input_fingerprint(context):
    paths = [Path(__file__), Path(gateway.__file__), Path(gateway.transport().__file__), kit_root() / ASSET]
    return digest({"context": asdict(context), "workspace": str(REPO_ROOT.resolve()),
                   "kit": verify_assets(),
                   "files": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]})


def expected_template():
    return {
        "$schema": "https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {key: {"type": "string"} for key in ("resourceGroupName", "location", "creationId")},
        "resources": [{"type": GROUP_TYPE, "apiVersion": ARM_VERSION,
                       "name": "[parameters('resourceGroupName')]", "location": "[parameters('location')]",
                       "tags": {"mcp-kit-owner": "mcp-openapi-creator-kit",
                                "mcp-kit-creation-id": "[parameters('creationId')]"}}],
    }


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
                raise ReconcileError("Resource group artifacts changed; preview again")


def prepare_artifacts(context, fingerprint):
    template = json_result(["az", "bicep", "build", "--file", str(kit_root() / ASSET), "--stdout"], timeout=300)
    # Ignore compiler metadata only; reject variables, outputs, conditions,
    # nested deployments, expressions or defaults outside the exact template.
    if {key: value for key, value in template.items() if key != "metadata"} != expected_template():
        raise ReconcileError("Compiled template differs from the exact one-resource-group creation template")
    contents = {"resource-group.json": encode_json(template), "parameters.json": encode_json(context.parameters())}
    key = digest({"inputs": fingerprint, "contents": {name: hashlib.sha256(value).hexdigest()
                                                    for name, value in contents.items()}})
    directory = safe_data_path(REPO_ROOT, REPO_ROOT / ".mcp-kit" / "provision-group" / key)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in contents.items():
        path = safe_data_path(REPO_ROOT, directory / name)
        if path.exists():
            if not path.is_file() or path.read_bytes() != value:
                raise ReconcileError("Conflicting resource group artifacts; preserve them")
        else:
            with path.open("xb") as stream:
                stream.write(value)
    result = Artifacts(directory, contents)
    result.verify()
    return result


def rest(context, uri):
    return json_result(["az", "rest", "--method", "GET", "--subscription", context.subscription,
                        "--uri", uri, "--output", "json"])


def collection(context, path):
    uri = f"{path}?api-version={ARM_VERSION}"
    seen = set()
    values = {}
    for _ in range(MAX_PAGES):
        if not isinstance(uri, str) or any(ord(c) < 33 or ord(c) == 127 for c in uri):
            raise ReconcileError("Malformed ARM collection route")
        parsed = urlsplit(uri)
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as error:
            raise ReconcileError("Malformed ARM collection query") from error
        keys = [key for key, _ in query]
        if (parsed.path.casefold() != path.casefold() or parsed.fragment
                or parsed.scheme not in {"", "https"}
                or (parsed.netloc and parsed.netloc.casefold() != "management.azure.com")
                or bool(parsed.scheme) != bool(parsed.netloc)
                or "\\" in uri or uri in seen
                or len(keys) != len(set(keys))
                or set(keys) - {"api-version", "$skiptoken"}
                or dict(query).get("api-version") != ARM_VERSION
                or any(not value for _, value in query)):
            raise ReconcileError("ARM pagination escaped its exact collection, version or bounded route")
        seen.add(uri)
        result = rest(context, uri)
        if not isinstance(result.get("value"), list):
            raise ReconcileError("Incomplete ARM inventory; never assume absence")
        for item in result["value"]:
            resource_id = item.get("id") if isinstance(item, dict) else None
            if (not isinstance(resource_id, str) or not resource_id.casefold().startswith(path.casefold() + "/")
                    or not valid_name(resource_id[len(path) + 1:])
                    or resource_id.casefold() in values):
                raise ReconcileError("Malformed, duplicate or out-of-scope inventory identity")
            values[resource_id.casefold()] = item
        link = result.get("nextLink")
        if link is None or link == "":
            return [values[key] for key in sorted(values)]
        uri = link
    raise ReconcileError("ARM inventory exceeded the bounded page limit")


def deployment_id(context, name):
    return f"{context.scope}/providers/Microsoft.Resources/deployments/{name}"


def inspect_absence(context, name):
    verify_context(context)
    groups = collection(context, context.scope + "/resourceGroups")
    if any(group["id"].casefold() == context.resource_id.casefold() for group in groups):
        raise ReconcileError("Resource group already exists; never adopt, modify or replace an existing group")
    deployments = collection(context, context.scope + "/providers/Microsoft.Resources/deployments")
    if any(item["id"].casefold() == deployment_id(context, name).casefold() for item in deployments):
        raise ReconcileError("Subscription deployment history already exists; never overwrite or resume")
    return {"resourceGroups": groups, "deployments": deployments,
            "operatorAccount": context.account.casefold()}


def desired_group(context, group, *, final=False):
    if (not isinstance(group, dict)
            or str(group.get("id", "")).casefold() != context.resource_id.casefold()
            or str(group.get("type", "")).casefold() != GROUP_TYPE.casefold()
            or str(group.get("name", "")).casefold() != context.resource_group.casefold()
            or group.get("location") != context.location or group.get("tags") != context.tags):
        raise ReconcileError("Resource group identity, location or tags differ from the exact creation plan")
    if final and (not isinstance(group.get("properties"), dict)
                  or group["properties"].get("provisioningState") != "Succeeded"):
        raise ReconcileError("Resource group provisioning state is not Succeeded")


def summarize_what_if(context, result):
    changes = result.get("changes")
    if (result.get("status") != "Succeeded" or result.get("error") is not None
            or not isinstance(changes, list) or len(changes) != 1):
        raise ReconcileError("What-if must expand exactly one resource group Create and no other change")
    change = changes[0]
    if (not isinstance(change, dict) or change.get("changeType") != "Create"
            or str(change.get("resourceId", "")).casefold() != context.resource_id.casefold()
            or change.get("before") is not None
            or change.get("unsupportedReason") or change.get("error")):
        raise ReconcileError("What-if is not an exact new resource group Create; unknown/Modify/Delete are refused")
    desired_group(context, change.get("after"))
    return [{"resourceId": context.resource_id, "changeType": "Create", "resourceType": GROUP_TYPE}]


def deployment_command(context, artifacts, name, operation):
    args = ["az", "deployment", "sub", operation, "--subscription", context.subscription,
            "--location", context.location, "--name", name,
            "--template-file", str(artifacts.directory / "resource-group.json"),
            "--parameters", f"@{artifacts.directory / 'parameters.json'}"]
    if operation == "what-if":
        return [*args, "--no-pretty-print", "--result-format", "FullResourcePayloads", "--output", "json"]
    return [*args, "--no-wait", "--output", "none"]


def wait_for_group(context, name):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        result = rest(context, f"{deployment_id(context, name)}?api-version={ARM_VERSION}")
        if (str(result.get("id", "")).casefold() != deployment_id(context, name).casefold()
                or not isinstance(result.get("properties"), dict)):
            raise ReconcileError("Incomplete or wrong subscription deployment identity")
        state = result["properties"].get("provisioningState")
        if state == "Succeeded":
            group = rest(context, f"{context.resource_id}?api-version={ARM_VERSION}")
            desired_group(context, group, final=True)
            verify_context(context)
            return group
        if state not in {"Accepted", "Running", "Creating", "Updating"}:
            raise ReconcileError("Subscription deployment failed, cancelled or returned an unknown state")
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
    raise ReconcileError("Subscription deployment verification timed out")


def attempt_path(context):
    key = digest({"resourceId": context.resource_id.casefold()})
    return safe_data_path(REPO_ROOT, REPO_ROOT / ".mcp-kit" / "provision-group" / "attempts" / (key + ".json"))


def receipt_path(artifacts):
    path = safe_data_path(REPO_ROOT, artifacts.directory / "creation-receipt.json")
    if path.exists():
        raise ReconcileError("Creation receipt already exists; never overwrite or retry")
    return path


def write_exclusive(path, value):
    with path.open("xb") as stream:
        stream.write(encode_json(value))
        stream.flush()
        os.fsync(stream.fileno())


def provision(context, *, dry_run=False, confirmation=None, apply=False, review_token=None):
    context.validate()
    if (dry_run and (apply or review_token)) or (review_token and not apply):
        raise ReconcileError("--dry-run cannot apply/authorize tokens; --review-token requires --yes")
    gateway.local_python(REPO_ROOT)
    workspace_root(REPO_ROOT)
    fingerprint = input_fingerprint(context)
    report = {"schemaVersion": 1, "status": "dry-run", "context": context.report(),
              "resourceId": context.resource_id, "resourceGroupMode": "new-only",
              "inputFingerprint": fingerprint, "azureVerified": False, "applied": False,
              "azdUsed": False, "creationId": context.creation_id, "ownershipTags": context.tags,
              "authorizesDeletion": False}
    print("[provision-group] Exact creation-only scope: one resource group, no APIM or other resources")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if dry_run:
        return report
    if confirmation is None:
        if not sys.stdin.isatty():
            raise ReconcileError("Preview requires --confirm-subscription or interactive context confirmation")
        confirmation = input("Approve this exact context for inspection/what-if by retyping the subscription ID: ").strip()
    if confirmation.casefold() != context.subscription.casefold():
        raise ReconcileError("Context confirmation does not match the explicit subscription")
    if attempt_path(context).exists():
        raise ReconcileError("A resource group creation attempt is already recorded. Outcome may be partial/unknown; inspect privately. No automatic retry, rollback or deletion.")
    verify_context(context)
    artifacts = prepare_artifacts(context, fingerprint)
    name = "mcp-kit-group-" + digest({"inputs": fingerprint, "artifacts": artifacts.fingerprint})[:24]
    state = inspect_absence(context, name)
    artifacts.verify()
    raw_what_if = json_result(deployment_command(context, artifacts, name, "what-if"), timeout=300)
    changes = summarize_what_if(context, raw_what_if)
    if input_fingerprint(context) != fingerprint:
        raise ReconcileError("Installed inputs changed during what-if")
    artifacts.verify()
    token = digest({"purpose": "provision-new-resource-group-v1", "inputs": fingerprint,
                    "artifacts": artifacts.fingerprint, "state": state, "deploymentName": name,
                    "whatIf": raw_what_if})
    report.update(status="preview", azureVerified=True, deploymentName=name,
                  deploymentResourceId=deployment_id(context, name),
                  artifactDirectory=str(artifacts.directory), artifactFingerprint=artifacts.fingerprint,
                  reviewToken=token, changes=changes)
    print(f"[provision-group] Create {context.resource_id}\n[provision-group] Review token: {token}")
    print("Review before --yes --review-token. Simulation/tokens are not human approval or a transaction; "
          "concurrent writes and Azure Policy effects remain possible. No automatic rollback or deletion.")
    if not apply:
        return report
    if not review_token or review_token != token:
        raise ReconcileError("Missing/stale review token; no apply. Review the refreshed preview")
    refreshed = inspect_absence(context, name)
    if digest(refreshed) != digest(state):
        raise ReconcileError("Azure inventory/context changed before apply; preview again")
    if input_fingerprint(context) != fingerprint:
        raise ReconcileError("Installed inputs changed before apply")
    artifacts.verify()
    verify_context(context)
    destination = receipt_path(artifacts)
    attempt = attempt_path(context)
    attempt.parent.mkdir(parents=True, exist_ok=True)
    write_exclusive(attempt, {**report, "kind": "resource-group-create-attempt",
                             "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
                             "outcome": "unknown-until-verified-receipt"})
    try:
        artifacts.verify()
        run(deployment_command(context, artifacts, name, "create"))
        group = wait_for_group(context, name)
        artifacts.verify()
        receipt = {**report, "status": "provisioned", "applied": True,
                   "kind": "verified-resource-group-create", "workspace": str(REPO_ROOT.resolve()),
                   "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
                   "provisioningState": "Succeeded", "tags": group["tags"],
                   "authorizesDeletion": False}
        write_exclusive(receipt_path(artifacts), receipt)
    except (OSError, ValueError, RuntimeError) as error:
        raise ReconcileError("Resource group creation outcome is partial/unknown or its durable receipt failed. "
                             "Preserve the attempt and subscription deployment history; inspect privately. "
                             "No automatic retry, rollback or deletion.") from error
    report.update(status="provisioned", applied=True, provisioningState="Succeeded",
                  creationReceiptPath=str(destination))
    print("[provision-group] Verified new resource group. Separately review provision before creating APIM.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("account", "tenant", "subscription", "resource-group", "location"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--dry-run", action="store_true", help="strictly offline, no writes/network/subprocess or token")
    parser.add_argument("--confirm-subscription", help="confirm the exact context for inspection/what-if")
    parser.add_argument("--yes", action="store_true", help="apply only with a matching separately reviewed token")
    parser.add_argument("--review-token", help="exact token from the preceding preview; required with --yes")
    args = parser.parse_args()
    try:
        context = ProvisionContext(args.account, args.tenant, args.subscription, args.resource_group, args.location)
        report = provision(context, dry_run=args.dry_run, confirmation=args.confirm_subscription,
                           apply=args.yes, review_token=args.review_token)
        print(json.dumps(report, indent=2, ensure_ascii=True))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"[provision-group] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
