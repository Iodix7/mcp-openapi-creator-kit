"""Whole-service cleanup solely for explicitly exclusive, UUID-named E2E APIMs.

This is not general gateway retirement. Exclusivity throughout the run is an
operator assumption, not an atomic child-graph or conditional-DELETE guarantee.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import asdict
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from . import runtime
from .data_paths import safe_data_path, workspace_root


_LOCK = threading.RLock()
_clock = time.monotonic
_sleep = time.sleep
_POLL_SECONDS = 5
_MAX_FILE_BYTES = 16 * 1024 * 1024
_NOTICE = (
    "Only active-service absence is verified. APIM soft-delete retains the service "
    "for 48 hours and reserves its name; no purge, permanent-destruction or "
    "zero-cost claim. Resource group and deployment history are preserved."
)


class _Blocked(ValueError):
    pass


class CleanupError(RuntimeError):
    """Actionable cleanup failure with safe attempt state, never raw Azure output."""

    def __init__(self, report: dict):
        self.report = report
        super().__init__(
            report["reason"] + " Preserve the original run evidence and any cleanup attempt marker. "
            "Never fabricate replacement ownership evidence or automatically retry DELETE; "
            "an explicit cleanup with an existing attempt marker is observation-only."
        )


def _require(condition, message):
    if not condition:
        raise _Blocked(message)


def _read(root: Path, path: Path) -> bytes:
    path = safe_data_path(root, path)
    _require(path.is_file() and path.stat().st_size <= _MAX_FILE_BYTES,
             "Missing, invalid or oversized original provisioning artifact.")
    return path.read_bytes()


def _object(data: bytes) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate JSON fields in provenance.")
            result[key] = value
        return result

    result = json.loads(data, object_pairs_hook=unique_pairs)
    _require(isinstance(result, dict), "Provenance must contain a JSON object.")
    return result


def _timestamp(value):
    _require(isinstance(value, str) and bool(value), "Missing creation timestamp.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "Creation timestamp must include its timezone.")
    return parsed


def verify_creation_kit(package: Path, info: dict) -> Path:
    """Verify an old installation as data only; never import or execute its code."""
    package = Path(package).expanduser().absolute()
    _require(not any(p.is_symlink() or p.is_junction() for p in (package, *package.parents)),
             "Original package ancestry contains a symlink/junction.")
    package = workspace_root(package)
    _require(isinstance(info, dict) and info.get("source") == "installed-package",
             "Recovery requires original installed-package provenance.")
    data = _read(package, package / "assets" / "manifest.json")
    _require(hashlib.sha256(data).hexdigest() == info.get("manifestSha256"),
             "Original installed manifest does not match the run's pinned hash.")
    manifest = _object(data)
    _require(len(manifest) == info.get("verifiedFiles") and all(
        key in manifest for key in ("tools/provision-gateway.py", "tools/retire-client.py",
                                    "platform/gateway.bicep", "package/assets.py")),
        "Original package manifest is incomplete.")
    for relative, expected in manifest.items():
        _require(isinstance(relative, str) and "\\" not in relative
                 and not relative.startswith("/") and all(p not in {"", ".", ".."} for p in relative.split("/"))
                 and isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected),
                 "Invalid original package manifest entry.")
        if relative.startswith("tools/"):
            path = package / "_commands" / relative.removeprefix("tools/")
        elif relative.startswith("package/"):
            path = package / relative.removeprefix("package/")
        else:
            path = package / "assets" / relative
        _require(hashlib.sha256(_read(package, path)).hexdigest() == expected,
                 "Original installed package bytes changed; recovery blocked.")
    return package


def _fingerprint(provision, context, creation_kit=None, creation_kit_info=None):
    if creation_kit is None:
        return provision.input_fingerprint(context)
    package = verify_creation_kit(creation_kit, creation_kit_info)
    return provision.digest({
        "context": asdict(context), "kit": creation_kit_info,
        "workspace": str(provision.REPO_ROOT.resolve()),
        "files": [hashlib.sha256(_read(package, package / path)).hexdigest() for path in
                  ("_commands/provision-gateway.py", "_commands/retire-client.py", "assets/platform/gateway.bicep")],
    })


def _local(root, creation, run_id, confirm_resource_id, exclusive_test, provision,
           creation_kit=None, creation_kit_info=None):
    _require(exclusive_test is True,
             "Exclusive temporary test-service deletion authorization is required.")
    _require(isinstance(run_id, str) and str(uuid.UUID(run_id)) == run_id.lower(),
             "A complete hyphenated run UUID is required.")
    _require(isinstance(creation, dict) and creation.get("status") == "provisioned"
             and creation.get("applied") is True and creation.get("azureVerified") is True
             and creation.get("provisioningState") == "Succeeded",
             "Creation is uncertain or unverified; cleanup cannot adopt or retry it.")
    raw_context = creation.get("context")
    _require(isinstance(raw_context, dict), "Original creation context is missing.")
    mapping = {
        "account": "account", "tenant": "tenant", "subscription": "subscription",
        "resource_group": "resourceGroup", "location": "location",
        "apim_name": "apimName", "publisher_name": "publisherName",
        "publisher_email": "publisherEmail", "profile": "profile",
    }
    context = provision.ProvisionContext(**{
        key: raw_context[value] for key, value in mapping.items()
    })
    context.validate()
    _require(raw_context == context.report(), "Creation context is not the exact original context.")
    _require(context.apim_name == "mcp-kit-e2e-" + uuid.UUID(run_id).hex
             and context.profile == "policy-mcp-consumption"
             and context.tier == "Consumption" and context.capacity == 0
             and context.identity == "None",
             "Only this run's dedicated Consumption mock-test service is eligible.")
    _require(confirm_resource_id == context.resource_id
             and creation.get("resourceId") == context.resource_id,
             "Exact full service resource-ID confirmation is required.")
    for key, expected in {
        "schemaVersion": 1, "tier": "Consumption", "capacity": 0,
        "identityType": "None", "network": "public", "resourceGroupMode": "existing-only",
        "creationId": context.creation_id, "ownershipTags": context.tags, "azdUsed": False,
    }.items():
        _require(type(creation.get(key)) is type(expected) and creation[key] == expected,
                 "Creation report scope/provenance differs.")
    for key in ("inputFingerprint", "artifactFingerprint", "reviewToken"):
        _require(isinstance(creation.get(key), str)
                 and re.fullmatch("[0-9a-f]{64}", creation[key]),
                 "Original provisioning fingerprints are missing or malformed.")
    _require(creation["inputFingerprint"] == _fingerprint(provision, context, creation_kit, creation_kit_info),
             "Installed provisioning inputs changed; automatic cleanup is blocked.")
    for key in ("artifactDirectory", "creationReceiptPath"):
        _require(isinstance(creation.get(key), str) and bool(creation[key]),
                 "Original receipt/artifact path is missing.")
    directory = safe_data_path(root, Path(creation["artifactDirectory"]))
    receipt_path = safe_data_path(root, Path(creation["creationReceiptPath"]))
    _require(receipt_path == directory / "creation-receipt.json",
             "Cleanup requires the in-place original creation receipt.")
    contents = {
        name: _read(root, directory / name)
        for name in ("gateway.json", "parameters.json", "name-check.json")
    }
    artifact = provision.Artifacts(directory, contents)
    _require(artifact.fingerprint == creation["artifactFingerprint"],
             "Original provisioning artifact fingerprint changed.")
    key = provision.digest({
        "inputs": creation["inputFingerprint"],
        "contents": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    })
    _require(directory == root / ".mcp-kit" / "provision" / key,
             "Provisioning artifacts do not belong to this run workspace.")
    _require(_object(contents["parameters.json"]) == context.parameters()
             and _object(contents["name-check.json"]) == {"name": context.apim_name},
             "Original provisioning parameters differ from the test scope.")
    template = _object(contents["gateway.json"])
    resources = template.get("resources")
    _require(isinstance(resources, list) and len(resources) == 1
             and isinstance(resources[0], dict)
             and resources[0].get("type") == "Microsoft.ApiManagement/service"
             and resources[0].get("apiVersion") == provision.APIM_VERSION
             and resources[0].get("name") == "[parameters('apimName')]"
             and not resources[0].get("resources") and not resources[0].get("copy"),
             "Original template is not the single-service creation artifact.")
    deployment_name = "mcp-kit-gateway-" + provision.digest({
        "inputs": creation["inputFingerprint"], "artifacts": artifact.fingerprint,
    })[:24]
    deployment_id = context.group_id + "/providers/Microsoft.Resources/deployments/" + deployment_name
    _require(creation.get("deploymentName") == deployment_name
             and creation.get("deploymentResourceId") == deployment_id,
             "Original creation deployment identity differs.")
    receipt_bytes = _read(root, receipt_path)
    receipt = _object(receipt_bytes)
    for key, expected in {
        "schemaVersion": 1, "kind": "verified-gateway-create",
        "workspace": str(root), "context": context.report(), "resourceId": context.resource_id,
        "creationId": context.creation_id, "tags": context.tags, "tier": "Consumption",
        "capacity": 0, "identityType": "None", "principalId": None, "identityTenant": None,
        "deploymentResourceId": deployment_id, "inputFingerprint": creation["inputFingerprint"],
        "artifactFingerprint": artifact.fingerprint, "reviewToken": creation["reviewToken"],
        "provisioningState": "Succeeded", "authorizesDeletion": False,
        "gatewayUrl": f"https://{context.apim_name}.azure-api.net",
    }.items():
        _require(key in receipt and type(receipt[key]) is type(expected) and receipt[key] == expected,
                 "Original creation receipt does not match the verified run provenance.")
    _require(creation.get("gatewayUrl") == receipt["gatewayUrl"],
             "Creation report gateway URL differs.")
    _timestamp(receipt.get("createdAtUtc"))
    _timestamp(receipt.get("recordedAtUtc"))
    etag = receipt.get("etag")
    _require(isinstance(etag, str) and bool(etag.strip()) and etag != "*"
             and all(ord(c) >= 32 and ord(c) != 127 for c in etag),
             "Original creation ETag is missing or invalid.")
    marker_path = safe_data_path(root, directory / "ephemeral-cleanup-attempt.json")
    marker = {
        "schemaVersion": 1, "kind": "exclusive-test-service-delete-attempt",
        "runId": str(uuid.UUID(run_id)), "workspace": str(root),
        "resourceId": context.resource_id, "receiptSha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "inputFingerprint": creation["inputFingerprint"], "artifactFingerprint": artifact.fingerprint,
        "exclusiveTest": True,
    }
    if marker_path.exists():
        _require(_object(_read(root, marker_path)) == marker,
                 "Cleanup attempt marker is invalid; no deletion retry is permitted.")
    return context, receipt, artifact, template, marker_path, marker


def _export_matches(template, exported):
    if not isinstance(exported, dict):
        return False
    expected = copy.deepcopy(template)
    actual = copy.deepcopy(exported)
    # ARM deployment export omits only root compiler metadata and title-cases
    # the primitive types of root parameter/output declarations.
    if "metadata" not in actual and "metadata" in expected:
        metadata = expected["metadata"]
        if (isinstance(metadata, dict) and set(metadata) == {"_generator"}
                and isinstance(metadata["_generator"], dict)
                and set(metadata["_generator"]) == {"name", "version", "templateHash"}
                and metadata["_generator"]["name"] == "bicep"):
            del expected["metadata"]
    for section in ("parameters", "outputs"):
        if not isinstance(expected.get(section, {}), dict) or not isinstance(actual.get(section, {}), dict):
            return False
        for name, declaration in expected.get(section, {}).items():
            other = actual.get(section, {}).get(name)
            if (isinstance(declaration, dict) and isinstance(other, dict)
                    and declaration.get("type") == "string" and other.get("type") == "String"):
                other["type"] = "string"
    return actual == expected


def _original_deployment(provision, context, receipt, template):
    deployment_id = receipt["deploymentResourceId"]
    result = provision.rest(context, "GET", f"{deployment_id}?api-version={provision.ARM_VERSION}")
    properties = result.get("properties")
    _require(result.get("id") == deployment_id and isinstance(properties, dict)
             and properties.get("provisioningState") == "Succeeded"
             and properties.get("mode") == "Incremental",
             "Original successful incremental creation deployment is not verifiable.")
    parameters = properties.get("parameters")
    expected = context.parameters()["parameters"]
    _require(isinstance(parameters, dict) and set(parameters) == set(expected)
             and all(isinstance(parameters[key], dict)
                     and parameters[key].get("value") == expected[key]["value"]
                     for key in expected),
             "Live original deployment parameters differ from the creation artifacts.")
    resources = properties.get("outputResources")
    _require(isinstance(resources, list) and len(resources) == 1
             and isinstance(resources[0], dict)
             and resources[0].get("id") == context.resource_id,
             "Original deployment does not prove exactly the created service.")
    exported = provision.rest(
        context, "POST", f"{deployment_id}/exportTemplate?api-version={provision.ARM_VERSION}")
    _require(_export_matches(template, exported.get("template")),
             "Live original deployment template differs from the preserved creation artifact.")


def _incarnation(provision, context, receipt, gateway, *, final=False):
    provision.desired_gateway(context, gateway, final=final)
    _require(gateway.get("id") == receipt["resourceId"]
             and gateway.get("tags") == receipt["tags"]
             and gateway.get("name") == context.apim_name
             and gateway["properties"].get("createdAtUtc") == receipt["createdAtUtc"]
             and not gateway["properties"].get("privateEndpointConnections")
             and not (gateway.get("identity") or {}).get("principalId")
             and not (gateway.get("identity") or {}).get("tenantId"),
             "Service incarnation or exact creation tags changed; cleanup is blocked.")
    # Client deployment can change the service ETag. No If-Match contract is assumed.


def cleanup(root: Path, creation: dict, *, run_id: str, confirm_resource_id: str,
            exclusive_test: bool, timeout_seconds: int = 900,
            creation_kit: Path | None = None, creation_kit_info: dict | None = None) -> dict:
    """Delete once, then verify absence; reruns with an attempt are observation-only.

    Failures raise ``CleanupError`` with bounded, non-provider errors and a safe
    ``report`` containing attempt state. Success is ``status="deleted"`` with
    ``serviceAbsent=True`` and does not mean permanent deletion.
    The creation report must come from this run's successful trusted provisioner,
    not an imported report. Local receipts are audit records, not signatures.
    """
    report = {
        "schemaVersion": 1, "status": "blocked", "serviceAbsent": False,
        "deleteAttempted": False, "observationOnly": False,
        "resourceGroupPreserved": True, "deploymentHistoryPreserved": True,
        "purgeAttempted": False, "notice": _NOTICE,
        "softDelete": {"retentionHours": 48, "nameReservedDuringRetention": True,
                       "retentionCompletionVerified": False, "purgeSupported": False},
    }
    phase = "local provenance"
    with _LOCK:
        provision = runtime.command("provision-gateway")
        previous_root, previous_run = provision.REPO_ROOT, provision.run
        try:
            _require(type(timeout_seconds) is int and 0 < timeout_seconds <= 3600,
                     "Cleanup timeout must be an integer from 1 through 3600 seconds.")
            deadline = _clock() + timeout_seconds

            def remaining():
                value = deadline - _clock()
                _require(value > 0, "Cleanup deadline exceeded; absence is unverified. No automatic DELETE retry.")
                return value

            supplied_root = Path(root).expanduser().absolute()
            _require(not any(part.is_symlink() or part.is_junction()
                             for part in (supplied_root, *supplied_root.parents)),
                     "Run workspace ancestry must not contain symlinks or junctions.")
            root = workspace_root(supplied_root)
            provision.REPO_ROOT = root
            context, receipt, artifacts, template, marker_path, marker = _local(
                root, creation, run_id, confirm_resource_id, exclusive_test, provision,
                creation_kit, creation_kit_info)
            report.update(resourceId=context.resource_id, cleanupAttemptPath=str(marker_path),
                          observationOnly=marker_path.exists(), deleteAttempted=marker_path.exists())

            def bounded_run(arguments, *, timeout=60):
                result = previous_run(arguments, timeout=min(timeout, remaining()))
                remaining()
                return result

            provision.run = bounded_run
            phase = "Azure context verification"
            provision.verify_context(context)
            phase = "original Azure deployment verification"
            _original_deployment(provision, context, receipt, template)
            collection_path = context.group_id + "/providers/Microsoft.ApiManagement/service"

            def observe():
                remaining()
                services = provision.collection(context, collection_path, provision.APIM_VERSION)
                remaining()
                matches = [item for item in services
                           if item["id"].casefold() == context.resource_id.casefold()]
                if matches:
                    _incarnation(provision, context, receipt, matches[0])
                return bool(matches)

            phase = "complete service collection verification"
            present = observe()
            if present and not report["observationOnly"]:
                phase = "final service incarnation verification"
                gateway = provision.rest(
                    context, "GET", f"{context.resource_id}?api-version={provision.APIM_VERSION}")
                _incarnation(provision, context, receipt, gateway, final=True)
                provision.verify_context(context)
                _require(_fingerprint(provision, context, creation_kit, creation_kit_info) == creation["inputFingerprint"],
                         "Installed inputs changed before deletion.")
                # Recheck all local bytes, including the receipt, immediately before the intent.
                refreshed = _local(root, creation, run_id, confirm_resource_id, exclusive_test, provision,
                                   creation_kit, creation_kit_info)
                _require(refreshed[5] == marker,
                         "Original receipt changed during verification; cleanup is blocked.")
                remaining()
                phase = "durable delete intent"
                try:
                    with marker_path.open("xb") as stream:
                        stream.write(provision.encode_json(marker))
                        stream.flush()
                        os.fsync(stream.fileno())
                    if os.name != "nt":
                        directory_fd = os.open(marker_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                except FileExistsError:
                    raise _Blocked("Another cleanup attempt exists; resume observation-only, never retry DELETE.") from None
                report["deleteAttempted"] = True
                phase = "service DELETE submission (outcome may be unknown)"
                # DELETE can return an empty body. Acceptance is not completion.
                provision.run([
                    "az", "rest", "--method", "DELETE", "--subscription", context.subscription,
                    "--uri", f"{context.resource_id}?api-version={provision.APIM_VERSION}",
                    "--output", "none",
                ])
                phase = "post-delete complete service collection verification"
                present = observe()
            while present:
                _sleep(min(_POLL_SECONDS, remaining()))
                phase = "post-delete complete service collection verification"
                present = observe()
            report.update(status="deleted", serviceAbsent=True)
        except _Blocked as error:
            report["reason"] = str(error)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError):
            report["reason"] = (
                f"Cleanup blocked during {phase}; missing, changed, inaccessible or malformed evidence. "
                "Raw output suppressed; absence is unverified and DELETE is never automatically retried."
            )
        finally:
            provision.REPO_ROOT, provision.run = previous_root, previous_run
    if not report["serviceAbsent"]:
        raise CleanupError(report) from None
    return report
