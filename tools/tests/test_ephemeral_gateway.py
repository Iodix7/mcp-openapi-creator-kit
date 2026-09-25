"""Synthetic exclusive-test cleanup checks; no Azure provider behavior is proved."""
import hashlib
import copy
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit
import uuid

import pytest

from mcp_openapi_creator_kit import ephemeral_gateway as cleanup_module
from mcp_openapi_creator_kit import runtime


provision = runtime.command("provision-gateway")
ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "12345678-1234-4321-8123-123456789abc"


def test_arm_export_representation_only():
    original = {
        "metadata": {"_generator": {"name": "bicep", "version": "0.44.1", "templateHash": "123"}},
        "parameters": {"name": {"type": "string", "defaultValue": "string"}},
        "outputs": {"name": {"type": "string", "value": "[parameters('name')]"}},
        "resources": [{"type": "Microsoft.ApiManagement/service", "properties": {"value": "string"}}],
    }
    exported = copy.deepcopy(original)
    del exported["metadata"]
    exported["parameters"]["name"]["type"] = "String"
    exported["outputs"]["name"]["type"] = "String"
    assert cleanup_module._export_matches(original, exported)
    assert original["parameters"]["name"]["type"] == "string"
    assert exported["parameters"]["name"]["type"] == "String"
    for section, key, value in [
        ("parameters", "defaultValue", "String"),
        ("parameters", "type", "secureString"),
        ("outputs", "value", "changed"),
    ]:
        changed = copy.deepcopy(exported)
        changed[section]["name"][key] = value
        assert not cleanup_module._export_matches(original, changed)
    changed = copy.deepcopy(exported)
    changed["resources"][0]["properties"]["value"] = "String"
    assert not cleanup_module._export_matches(original, changed)
    changed = copy.deepcopy(original)
    changed["metadata"]["custom"] = "must not disappear"
    assert not cleanup_module._export_matches(changed, exported)
    assert not cleanup_module._export_matches(original, {**exported, "resources": []})
    assert not cleanup_module._export_matches(original, {**exported, "parameters": None})


def _old_package(tmp_path):
    package = tmp_path / "original"
    contents = {
        "tools/provision-gateway.py": b"original provision code",
        "tools/retire-client.py": b"original transport code",
        "platform/gateway.bicep": b"original template",
        "package/assets.py": b"raise RuntimeError('must never execute old code')",
    }
    for name, data in contents.items():
        relative = ("_commands/" + name[6:] if name.startswith("tools/") else
                    name[8:] if name.startswith("package/") else "assets/" + name)
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    manifest = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}).encode()
    (package / "assets" / "manifest.json").write_bytes(manifest)
    return package, {"source": "installed-package", "version": "test",
                     "manifestSha256": hashlib.sha256(manifest).hexdigest(), "verifiedFiles": len(contents)}


def test_original_package_verified_as_data_not_executed(tmp_path):
    package, info = _old_package(tmp_path)
    assert cleanup_module.verify_creation_kit(package, info) == package
    with pytest.raises(ValueError, match="pinned hash"):
        cleanup_module.verify_creation_kit(package, {**info, "manifestSha256": "0" * 64})
    (package / "assets.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="bytes changed"):
        cleanup_module.verify_creation_kit(package, info)


def test_recovery_fingerprint_uses_original_verified_bytes(tmp_path, monkeypatch):
    from dataclasses import asdict
    package, info = _old_package(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(provision, "REPO_ROOT", root)
    context = provision.ProvisionContext(
        "operator@example.test", "22222222-2222-4222-8222-222222222222",
        "11111111-1111-4111-8111-111111111111", "group", "westeurope",
        "mcp-kit-e2e-" + uuid.UUID(RUN_ID).hex, "Test", "test@example.test", "policy-mcp-consumption")
    expected = provision.digest({
        "context": asdict(context), "kit": info, "workspace": str(root.resolve()),
        "files": [hashlib.sha256(value).hexdigest() for value in (
            b"original provision code", b"original transport code", b"original template")],
    })
    assert cleanup_module._fingerprint(provision, context, package, info) == expected
    assert cleanup_module._fingerprint(provision, context) != expected


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Azure:
    def __init__(self, context, report, template):
        self.context, self.report, self.template = context, report, template
        self.calls = []
        self.timeouts = []
        self.deleted = False
        self.pending = 0
        self.failure = None
        self.collection_result = None
        self.collection_pages = []
        self.after_delete = None
        self.account = {"user": context.account, "id": context.subscription, "tenantId": context.tenant}
        self.service = {
            "id": context.resource_id, "name": context.apim_name,
            "type": "Microsoft.ApiManagement/service", "location": context.location,
            "sku": {"name": "Consumption", "capacity": 0}, "identity": {"type": "None"},
            "tags": context.tags, "etag": '"changed-by-client-deployment"',
            "properties": {
                "createdAtUtc": "2026-09-21T10:00:00Z", "provisioningState": "Succeeded",
                "publisherName": context.publisher_name, "publisherEmail": context.publisher_email,
                "virtualNetworkType": "None", "publicNetworkAccess": "Enabled",
                "gatewayUrl": f"https://{context.apim_name}.azure-api.net",
            },
        }
        self.deployment = {
            "id": report["deploymentResourceId"],
            "properties": {
                "provisioningState": "Succeeded", "mode": "Incremental",
                "parameters": context.parameters()["parameters"],
                "outputResources": [{"id": context.resource_id}],
            },
        }

    @property
    def deletes(self):
        return [args for args in self.calls if "--method" in args
                and args[args.index("--method") + 1] == "DELETE"]

    def __call__(self, arguments, *, timeout=60):
        self.calls.append(arguments)
        self.timeouts.append(timeout)
        if arguments[1:3] == ["account", "show"]:
            return json.dumps(self.account)
        if arguments[1:3] == ["cloud", "show"]:
            return json.dumps({"name": "AzureCloud", "resourceManager": "https://management.azure.com/"})
        assert arguments[:2] == ["az", "rest"]
        assert arguments[arguments.index("--subscription") + 1] == self.context.subscription
        method = arguments[arguments.index("--method") + 1]
        uri = arguments[arguments.index("--uri") + 1]
        path = urlsplit(uri).path
        if path == self.report["deploymentResourceId"]:
            assert method == "GET"
            return json.dumps(self.deployment)
        if path == self.report["deploymentResourceId"] + "/exportTemplate":
            assert method == "POST"
            return json.dumps({"template": self.template})
        if path == self.context.resource_id:
            if method == "DELETE":
                assert "--headers" not in arguments
                assert arguments[arguments.index("--output") + 1] == "none"
                marker = Path(self.report["artifactDirectory"]) / "ephemeral-cleanup-attempt.json"
                assert marker.is_file(), "Intent must be durable before destructive submission"
                self.deleted = True
                if self.after_delete:
                    self.after_delete()
                return ""
            assert method == "GET"
            return json.dumps(self.service)
        assert method == "GET"
        assert path == self.context.group_id + "/providers/Microsoft.ApiManagement/service"
        if self.failure:
            raise self.failure
        if self.collection_pages:
            return json.dumps(self.collection_pages.pop(0))
        if self.collection_result is not None:
            return json.dumps(self.collection_result)
        if not self.deleted or self.pending > 0:
            self.pending = max(0, self.pending - 1)
            return json.dumps({"value": [self.service]})
        return '{"value": []}'


@pytest.fixture
def run(monkeypatch):
    root = ROOT / ".test-artifacts" / ("ephemeral-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    monkeypatch.setattr(provision, "REPO_ROOT", root)
    context = provision.ProvisionContext(
        account="operator@example.test",
        tenant="22222222-2222-2222-2222-222222222222",
        subscription="11111111-1111-1111-1111-111111111111",
        resource_group="existing-rg", location="westeurope",
        apim_name="mcp-kit-e2e-" + uuid.UUID(RUN_ID).hex,
        publisher_name="Test", publisher_email="test@example.test",
        profile="policy-mcp-consumption",
    )
    template = {"resources": [{
        "type": "Microsoft.ApiManagement/service", "apiVersion": provision.APIM_VERSION,
        "name": "[parameters('apimName')]",
    }]}
    fingerprint = provision.input_fingerprint(context)
    contents = {
        "gateway.json": provision.encode_json(template),
        "parameters.json": provision.encode_json(context.parameters()),
        "name-check.json": provision.encode_json({"name": context.apim_name}),
    }
    key = provision.digest({
        "inputs": fingerprint,
        "contents": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    })
    directory = root / ".mcp-kit" / "provision" / key
    directory.mkdir(parents=True)
    for name, data in contents.items():
        (directory / name).write_bytes(data)
    artifacts = provision.Artifacts(directory, contents)
    name = "mcp-kit-gateway-" + provision.digest({
        "inputs": fingerprint, "artifacts": artifacts.fingerprint,
    })[:24]
    creation = {
        "schemaVersion": 1, "status": "provisioned", "applied": True, "azureVerified": True,
        "provisioningState": "Succeeded", "context": context.report(),
        "resourceId": context.resource_id, "tier": "Consumption", "capacity": 0,
        "identityType": "None", "network": "public", "resourceGroupMode": "existing-only",
        "creationId": context.creation_id, "ownershipTags": context.tags, "azdUsed": False,
        "inputFingerprint": fingerprint, "artifactFingerprint": artifacts.fingerprint,
        "artifactDirectory": str(directory), "reviewToken": "a" * 64,
        "deploymentName": name,
        "deploymentResourceId": context.group_id + "/providers/Microsoft.Resources/deployments/" + name,
        "gatewayUrl": f"https://{context.apim_name}.azure-api.net",
    }
    azure = Azure(context, creation, template)
    creation["creationReceiptPath"] = str(provision.write_creation_receipt(
        context, artifacts, creation, azure.service))
    monkeypatch.setattr(provision, "run", azure)
    clock = Clock()
    monkeypatch.setattr(cleanup_module, "_clock", clock)
    monkeypatch.setattr(cleanup_module, "_sleep", clock.sleep)
    options = {"run_id": RUN_ID, "confirm_resource_id": context.resource_id,
               "exclusive_test": True, "timeout_seconds": 30}
    try:
        yield root, creation, options, azure, clock
    finally:
        shutil.rmtree(root)


def invoke(run, **overrides):
    """Expose safe error reports for detailed assertions; failures must raise."""
    root, creation, options, _, _ = run
    try:
        result = cleanup_module.cleanup(root, creation, **{**options, **overrides})
    except cleanup_module.CleanupError as error:
        assert isinstance(error, RuntimeError)
        assert "Preserve the original run evidence" in str(error)
        assert error.report["status"] == "blocked" and not error.report["serviceAbsent"]
        return error.report
    assert result["status"] == "deleted" and result["serviceAbsent"]
    return result


def test_success_polls_and_preserves_group_and_history(run):
    root, creation, _, azure, clock = run
    azure.pending = 3
    before = provision.REPO_ROOT, provision.run
    result = invoke(run)
    assert result["status"] == "deleted" and result["serviceAbsent"]
    assert result["deleteAttempted"] and not result["observationOnly"]
    assert result["resourceGroupPreserved"] and result["deploymentHistoryPreserved"]
    assert not result["purgeAttempted"]
    assert "48 hours" in result["notice"]
    assert result["softDelete"] == {
        "retentionHours": 48, "nameReservedDuringRetention": True,
        "retentionCompletionVerified": False, "purgeSupported": False,
    }
    assert len(azure.deletes) == 1 and clock.now == 10
    assert all(0 < timeout <= 30 for timeout in azure.timeouts)
    assert (provision.REPO_ROOT, provision.run) == before
    assert Path(creation["creationReceiptPath"]).is_file()
    assert root.is_dir()
    # The fake transport refuses any generic inventory, RG DELETE, purge or access endpoint.


@pytest.mark.parametrize("options", [
    {"exclusive_test": False}, {"exclusive_test": 1},
    {"confirm_resource_id": "wrong"},
    {"run_id": str(uuid.uuid4())}, {"run_id": uuid.UUID(RUN_ID).hex},
    {"timeout_seconds": 0}, {"timeout_seconds": True},
])
def test_approval_and_run_checks_precede_all_azure(run, options):
    result = invoke(run, **options)
    assert result["status"] == "blocked"
    assert not run[3].calls


@pytest.mark.parametrize("mutation", [
    lambda report: report.update(status="preview"),
    lambda report: report.update(applied=False),
    lambda report: report.pop("creationReceiptPath"),
    lambda report: report.pop("artifactDirectory"),
    lambda report: report["context"].update(apimName="existing-shared-apim"),
    lambda report: report["context"].update(profile="native-mcp"),
    lambda report: report["context"].update(resourceGroup="other-rg"),
    lambda report: report.update(inputFingerprint="0" * 64),
    lambda report: report.update(artifactFingerprint="0" * 64),
    lambda report: report.update(deploymentResourceId="wrong"),
    lambda report: report.update(capacity=False),
])
def test_uncertain_or_modified_report_never_adopted(run, mutation):
    mutation(run[1])
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


@pytest.mark.parametrize("field,value", [
    ("workspace", "wrong"), ("resourceId", "wrong"), ("createdAtUtc", ""),
    ("tags", {}), ("context", {}), ("tier", "BasicV2"),
    ("inputFingerprint", "0" * 64), ("authorizesDeletion", True),
    ("kind", "user-written-receipt"), ("etag", "*"),
])
def test_invalid_receipt_blocks_before_azure(run, field, value):
    path = Path(run[1]["creationReceiptPath"])
    receipt = json.loads(path.read_bytes())
    receipt[field] = value
    path.write_text(json.dumps(receipt), encoding="utf-8")
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


@pytest.mark.parametrize("name", ["gateway.json", "parameters.json", "name-check.json"])
def test_modified_artifacts_block_before_azure(run, name):
    path = Path(run[1]["artifactDirectory"]) / name
    path.write_bytes(path.read_bytes() + b" ")
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


def test_missing_receipt_blocks_before_azure(run):
    Path(run[1]["creationReceiptPath"]).unlink()
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


def test_hardlinked_receipt_blocks_before_azure(run):
    source = Path(run[1]["creationReceiptPath"])
    (source.parent / "receipt-link.json").hardlink_to(source)
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


@pytest.mark.parametrize("kind", ["is_symlink", "is_junction"])
@pytest.mark.parametrize("target", ["root", "directory", "receipt"])
def test_redirected_paths_block_before_azure(run, monkeypatch, kind, target):
    path = {
        "root": run[0], "directory": Path(run[1]["artifactDirectory"]),
        "receipt": Path(run[1]["creationReceiptPath"]),
    }[target]
    original = getattr(Path, kind)
    monkeypatch.setattr(Path, kind, lambda item: item == path or original(item))
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


@pytest.mark.parametrize("path_field", ["creationReceiptPath", "artifactDirectory"])
def test_foreign_paths_block_before_azure(run, path_field):
    run[1][path_field] = str(ROOT)
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


@pytest.mark.parametrize("mutation", [
    lambda service: service["tags"].update({"foreign": "new-use"}),
    lambda service: service["tags"].update({"mcp-kit-creation-id": "wrong"}),
    lambda service: service["properties"].update(createdAtUtc="2026-09-22T10:00:00Z"),
    lambda service: service["properties"].update(publicNetworkAccess="Disabled"),
    lambda service: service["sku"].update(capacity=1),
    lambda service: service.update(identity={"type": "SystemAssigned"}),
    lambda service: service["properties"].update(privateEndpointConnections=[{"id": "foreign"}]),
])
def test_live_incarnation_change_blocks_without_delete(run, mutation):
    mutation(run[3].service)
    result = invoke(run)
    assert result["status"] == "blocked" and not result["serviceAbsent"]
    assert not run[3].deletes


@pytest.mark.parametrize("mutation", [
    lambda azure: azure.deployment["properties"].update(provisioningState="Failed"),
    lambda azure: azure.deployment["properties"].update(parameters={}),
    lambda azure: azure.deployment["properties"].update(outputResources=[]),
    lambda azure: azure.deployment.update(id="wrong"),
    lambda azure: setattr(azure, "template", {}),
])
def test_live_original_deployment_required(run, mutation):
    mutation(run[3])
    assert invoke(run)["status"] == "blocked"
    assert not run[3].deletes


def test_wrong_active_account_blocks_before_resource_requests(run):
    run[3].account["user"] = "somebody-else"
    assert invoke(run)["status"] == "blocked"
    assert len(run[3].calls) == 1
    assert not run[3].deletes


@pytest.mark.parametrize("result", [
    {}, {"value": None}, {"value": {}}, {"value": [], "nextLink": 42},
    {"value": [], "nextLink": "https://foreign.invalid/"},
    {"value": [{"id": "/other/service"}]},
    {"error": {"message": "sensitive provider output"}, "value": []},
])
def test_malformed_or_incomplete_collection_is_not_absence(run, result):
    run[3].collection_result = result
    report = invoke(run)
    assert report["status"] == "blocked" and not report["serviceAbsent"]
    assert "sensitive provider output" not in json.dumps(report)
    assert not run[3].deletes


@pytest.mark.parametrize("error", [RuntimeError("Forbidden secret"), TimeoutError("timeout secret")])
def test_collection_failure_is_not_absence(run, error):
    run[3].failure = error
    result = invoke(run)
    assert result["status"] == "blocked" and not result["serviceAbsent"]
    assert "secret" not in json.dumps(result)
    assert not run[3].deletes


def test_all_collection_pages_are_required_before_absence(run):
    azure = run[3]
    next_link = (
        f"https://management.azure.com{azure.context.group_id}"
        f"/providers/Microsoft.ApiManagement/service?api-version={provision.APIM_VERSION}&$skiptoken=next"
    )
    azure.collection_pages = [
        {"value": [], "nextLink": next_link}, {"value": [azure.service]},
    ]
    result = invoke(run)
    assert result["serviceAbsent"] and len(azure.deletes) == 1
    assert any(next_link in call for call in azure.calls)


def test_duplicate_service_collection_entry_blocks_delete(run):
    azure = run[3]
    azure.collection_result = {"value": [azure.service, azure.service]}
    assert invoke(run)["status"] == "blocked"
    assert not azure.deletes


def test_bounded_poll_and_resume_never_repeats_delete(run):
    run[3].pending = 1000
    result = invoke(run, timeout_seconds=7)
    assert result["status"] == "blocked" and not result["serviceAbsent"]
    assert run[4].now == 7 and len(run[3].deletes) == 1
    assert Path(result["cleanupAttemptPath"]).is_file()
    run[3].pending = 0
    resumed = invoke(run)
    assert resumed["status"] == "deleted" and resumed["observationOnly"]
    assert len(run[3].deletes) == 1


def test_unknown_delete_result_preserves_intent_and_allows_observation_only(run):
    def fail():
        raise TimeoutError("raw destructive result is not safe to log")

    run[3].after_delete = fail
    result = invoke(run)
    assert result["status"] == "blocked" and result["deleteAttempted"]
    assert "raw destructive" not in json.dumps(result)
    assert len(run[3].deletes) == 1
    run[3].after_delete = None
    run[3].deleted = False
    result = invoke(run, timeout_seconds=1)
    assert result["status"] == "blocked" and result["observationOnly"]
    assert len(run[3].deletes) == 1
    run[3].deleted = True
    assert invoke(run)["serviceAbsent"]
    assert len(run[3].deletes) == 1


def test_post_delete_replacement_never_reported_complete(run):
    def replace():
        run[3].pending = 10
        run[3].service["properties"]["createdAtUtc"] = "2026-09-22T10:00:00Z"

    run[3].after_delete = replace
    result = invoke(run)
    assert result["status"] == "blocked" and not result["serviceAbsent"]
    assert len(run[3].deletes) == 1


def test_post_delete_forbidden_is_not_success(run):
    def forbid():
        run[3].failure = RuntimeError("Forbidden")

    run[3].after_delete = forbid
    result = invoke(run)
    assert result["status"] == "blocked" and not result["serviceAbsent"]
    assert len(run[3].deletes) == 1


def test_modified_intent_blocks_before_cloud(run):
    result = invoke(run)
    path = Path(result["cleanupAttemptPath"])
    marker = json.loads(path.read_bytes())
    marker["runId"] = str(uuid.uuid4())
    path.write_text(json.dumps(marker), encoding="utf-8")
    run[3].calls.clear()
    assert invoke(run)["status"] == "blocked"
    assert not run[3].calls


def test_receipt_change_during_cloud_verification_blocks_delete(run, monkeypatch):
    azure = run[3]

    def changed(arguments, *, timeout=60):
        result = azure(arguments, timeout=timeout)
        if ("--uri" in arguments and "--method" in arguments
                and arguments[arguments.index("--method") + 1] == "GET"
                and urlsplit(arguments[arguments.index("--uri") + 1]).path == azure.context.resource_id):
            path = Path(run[1]["creationReceiptPath"])
            receipt = json.loads(path.read_bytes())
            receipt["recordedAtUtc"] = "2026-09-22T10:00:00Z"
            path.write_text(json.dumps(receipt), encoding="utf-8")
        return result

    monkeypatch.setattr(provision, "run", changed)
    assert invoke(run)["status"] == "blocked"
    assert not azure.deletes


def test_intent_flush_failure_never_submits_or_retries_delete(run, monkeypatch):
    def fail(_):
        raise OSError("Disk flush failed")

    with monkeypatch.context() as patch:
        patch.setattr(cleanup_module.os, "fsync", fail)
        result = invoke(run)
    assert result["status"] == "blocked"
    assert not run[3].deletes
    assert Path(result["cleanupAttemptPath"]).is_file()
    resumed = invoke(run, timeout_seconds=1)
    assert resumed["observationOnly"] and resumed["status"] == "blocked"
    assert not run[3].deletes


def test_complete_absence_does_not_submit_delete(run):
    run[3].deleted = True
    result = invoke(run)
    assert result["serviceAbsent"] and not result["deleteAttempted"]
    assert not run[3].deletes


def test_per_call_timeout_includes_whole_operation_budget(run, monkeypatch):
    azure = run[3]

    def slow(arguments, *, timeout=60):
        assert timeout <= 3 - run[4].now
        value = azure(arguments, timeout=timeout)
        run[4].now += 1
        return value

    monkeypatch.setattr(provision, "run", slow)
    result = invoke(run, timeout_seconds=3)
    assert result["status"] == "blocked" and not result["serviceAbsent"]
    assert len(azure.calls) == 3 and not azure.deletes
