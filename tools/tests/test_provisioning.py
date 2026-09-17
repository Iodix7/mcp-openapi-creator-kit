"""Creation-only provisioning contracts; every Azure interaction is simulated."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


provision = importlib.import_module("mcp_openapi_creator_kit._commands.provision-gateway")
ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
PRINCIPAL = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def context():
    return provision.ProvisionContext(
        account="operator@example.test", tenant=TENANT, subscription=SUBSCRIPTION,
        resource_group="customer-existing-rg", location="westeurope", apim_name="new-customer-gateway",
        publisher_name='Example "Organization" & Partners (IT) 100% —',
        publisher_email="publisher@example.test", profile="native-mcp")


def gateway(context):
    return {
        "id": context.resource_id, "type": "Microsoft.ApiManagement/service",
        "name": context.apim_name, "location": context.location,
        "tags": context.tags, "etag": '"creation-etag-123"',
        "sku": {"name": context.tier, "capacity": context.capacity},
        "identity": {"type": context.identity, **({
            "principalId": PRINCIPAL, "tenantId": TENANT,
        } if context.identity == "SystemAssigned" else {})},
        "properties": {
            "publisherName": context.publisher_name, "publisherEmail": context.publisher_email,
            "virtualNetworkType": "None", "publicNetworkAccess": "Enabled",
            "provisioningState": "Succeeded",
            "createdAtUtc": "2026-09-16T14:00:00Z",
            "gatewayUrl": f"https://{context.apim_name}.azure-api.net",
        },
    }


class Azure:
    def __init__(self, context):
        self.context = context
        self.calls = []
        self.creates = []
        self.services = []
        self.histories = []
        self.available = {"nameAvailable": True}
        self.account = {"user": context.account, "id": context.subscription, "tenantId": context.tenant}
        self.cloud = {"name": "AzureCloud", "resourceManager": "https://management.azure.com/"}
        self.group = {
            "id": context.group_id, "name": context.resource_group, "location": "northeurope",
            "properties": {"provisioningState": "Succeeded"}, "tags": {"keep": "unchanged"},
        }
        self.compiled = {"resources": [{
            "type": "Microsoft.ApiManagement/service", "apiVersion": provision.APIM_VERSION,
            "name": "[parameters('apimName')]",
        }]}
        self.what_if = {"status": "Succeeded", "changes": [{
            "resourceId": context.resource_id, "changeType": "Create",
            "after": gateway(context),
        }]}
        self.actual = gateway(context)
        self.deployment_states = ["Succeeded"]
        self.on_what_if = None
        self.on_name_check = None
        self.services_error = None

    def __call__(self, arguments, *, timeout=60):
        self.calls.append(arguments[:])
        context = self.context
        assert arguments[0] == "az"
        if arguments[1:3] == ["account", "show"]:
            return json.dumps(self.account)
        if arguments[1:3] == ["cloud", "show"]:
            return json.dumps(self.cloud)
        if arguments[1:3] == ["bicep", "build"]:
            assert Path(arguments[arguments.index("--file") + 1]) == provision.kit_root() / provision.ASSET
            assert "--stdout" in arguments
            return json.dumps(self.compiled)
        assert arguments[arguments.index("--subscription") + 1] == context.subscription
        if arguments[1:3] == ["deployment", "group"]:
            assert arguments[arguments.index("--mode") + 1] == "Incremental"
            parameters = arguments[arguments.index("--parameters") + 1]
            assert parameters.startswith("@")
            assert json.loads(Path(parameters[1:]).read_bytes()) == context.parameters()
            assert context.publisher_name not in arguments
            if arguments[3] == "what-if":
                assert arguments[arguments.index("--result-format") + 1] == "FullResourcePayloads"
                if self.on_what_if:
                    self.on_what_if()
                return json.dumps(self.what_if)
            assert arguments[3] == "create" and "--no-wait" in arguments
            self.creates.append(arguments)
            return ""
        assert arguments[1] == "rest"
        method = arguments[arguments.index("--method") + 1]
        uri = arguments[arguments.index("--uri") + 1]
        path = uri.split("?")[0]
        if method == "POST":
            assert path == f"/subscriptions/{context.subscription}/providers/Microsoft.ApiManagement/checkNameAvailability"
            body = Path(arguments[arguments.index("--body") + 1][1:])
            assert json.loads(body.read_bytes()) == {"name": context.apim_name}
            if self.on_name_check:
                self.on_name_check()
            return json.dumps(self.available)
        assert method == "GET"
        if path == context.group_id:
            return json.dumps(self.group)
        if path == context.group_id + "/providers/Microsoft.ApiManagement/service":
            if self.services_error:
                raise self.services_error
            return json.dumps({"value": self.services})
        if path == context.group_id + "/providers/Microsoft.Resources/deployments":
            return json.dumps({"value": self.histories})
        if "/providers/Microsoft.Resources/deployments/" in path:
            state = self.deployment_states[0]
            if len(self.deployment_states) > 1:
                self.deployment_states.pop(0)
            return json.dumps({"id": path, "properties": {"provisioningState": state}})
        if path == context.resource_id:
            return json.dumps(self.actual)
        raise AssertionError(f"Unexpected URI: {uri}")


@pytest.fixture
def azure(context, tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "REPO_ROOT", tmp_path)
    service = Azure(context)
    monkeypatch.setattr(provision, "run", service)
    return service


def preview(context):
    return provision.provision(context, confirmation=context.subscription)


def apply(context, token):
    return provision.provision(context, confirmation=context.subscription, apply=True, review_token=token)


def test_dry_run_zero_azure_zero_writes_and_no_token(context, azure, tmp_path):
    result = provision.provision(context, dry_run=True)
    assert result["status"] == "dry-run"
    assert not result["azureVerified"] and not result["applied"] and not result["azdUsed"]
    assert "reviewToken" not in result
    assert result["context"]["publisherName"] == context.publisher_name
    assert result["identityType"] == "SystemAssigned"
    assert azure.calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", [
    ("account", ""), ("account", " operator@example.test"), ("tenant", "not-a-uuid"),
    ("subscription", ""), ("tenant", TENANT.replace("-", "")),
    ("resource_group", "bad/group"), ("location", "West Europe"),
    ("location", ""), ("apim_name", "bad-"), ("publisher_name", ""),
    ("publisher_email", "not-an-email"), ("profile", "standardv2"),
    ("publisher_name", "unsafe\ntext"),
])
def test_explicit_context_required_before_cloud(context, azure, field, value):
    with pytest.raises((ValueError, RuntimeError)):
        provision.provision(replace(context, **{field: value}), dry_run=True)
    assert azure.calls == []


@pytest.mark.parametrize("options", [
    {"dry_run": True, "apply": True}, {"dry_run": True, "review_token": "token"},
    {"review_token": "token"},
])
def test_invalid_approval_combinations(context, azure, options):
    with pytest.raises(provision.ReconcileError):
        provision.provision(context, **options)
    assert azure.calls == []


def test_wrong_confirmation_precedes_all_azure_calls(context, azure):
    with pytest.raises(provision.ReconcileError, match="confirmation"):
        provision.provision(context, confirmation="wrong")
    assert azure.calls == []


@pytest.mark.parametrize("field", ["user", "id", "tenantId"])
def test_active_context_mismatch_blocks_before_resource_reads(context, azure, field):
    azure.account[field] = "other"
    with pytest.raises(ValueError, match="Active Azure CLI"):
        preview(context)
    assert all(call[1:3] == ["account", "show"] for call in azure.calls)
    assert not azure.creates


def test_nonpublic_cloud_blocked(context, azure):
    azure.cloud["name"] = "AzureUSGovernment"
    with pytest.raises(provision.ReconcileError, match="public cloud"):
        preview(context)
    assert not any(call[1] == "rest" for call in azure.calls)


@pytest.mark.parametrize("profile", sorted(provision.PROFILES))
def test_preview_and_apply_verified_exact_profile(context, azure, profile):
    context = replace(context, profile=profile)
    azure.context = context
    azure.what_if["changes"][0]["after"] = gateway(context)
    azure.actual = gateway(context)
    before = preview(context)
    assert before["status"] == "preview" and before["azureVerified"] and not before["applied"]
    assert before["tier"] == context.tier and before["identityType"] == context.identity
    assert len(before["reviewToken"]) == 64 and len(azure.creates) == 0
    after = apply(context, before["reviewToken"])
    assert after["status"] == "provisioned" and after["applied"]
    assert after["provisioningState"] == "Succeeded"
    assert after["gatewayUrl"] == gateway(context)["properties"]["gatewayUrl"]
    assert after["principalId"] == (PRINCIPAL if profile == "native-mcp" else None)
    receipt = json.loads(Path(after["creationReceiptPath"]).read_bytes())
    assert receipt["kind"] == "verified-gateway-create" and not receipt["authorizesDeletion"]
    assert receipt["resourceId"] == context.resource_id and receipt["tags"] == context.tags
    assert receipt["createdAtUtc"] == azure.actual["properties"]["createdAtUtc"]
    assert receipt["etag"] == azure.actual["etag"]
    assert len(azure.creates) == 1
    methods = [call[call.index("--method") + 1] for call in azure.calls if "--method" in call]
    assert set(methods) == {"GET", "POST"}
    assert sum(call[1:4] == ["deployment", "group", "what-if"] for call in azure.calls) == 2
    assert all("azd" not in call for call in azure.calls)


def test_missing_and_stale_tokens_never_create(context, azure):
    for token in (None, "unreviewed"):
        with pytest.raises(provision.ReconcileError, match="review token"):
            apply(context, token)
    assert azure.creates == []


@pytest.mark.parametrize("existing", ["service", "history"])
def test_never_adopts_existing_service_or_history(context, azure, existing):
    if existing == "service":
        azure.services = [gateway(context)]
    else:
        result = preview(context)
        azure.histories = [{"id": result["deploymentResourceId"]}]
    with pytest.raises(provision.ReconcileError, match="already exists"):
        preview(context)
    assert azure.creates == []


@pytest.mark.parametrize("available", [{"nameAvailable": False}, {}, {"nameAvailable": "true"}])
def test_global_name_availability_must_be_positive(context, azure, available):
    azure.available = available
    with pytest.raises(provision.ReconcileError, match="availability"):
        preview(context)
    assert azure.creates == []


def test_permission_failure_is_never_empty_inventory(context, azure):
    azure.services_error = provision.ReconcileError("Forbidden")
    with pytest.raises(provision.ReconcileError, match="Forbidden"):
        preview(context)
    assert not azure.creates
    assert not any("what-if" in call for call in azure.calls)


@pytest.mark.parametrize("group", [
    {}, {"id": "/wrong"}, {"properties": []}, {"properties": {"provisioningState": "Failed"}},
])
def test_resource_group_is_existing_only_and_must_be_verifiable(context, azure, group):
    azure.group.update(group)
    if not group:
        azure.group = {}
    with pytest.raises(provision.ReconcileError, match="resource group"):
        preview(context)
    assert not azure.creates


@pytest.mark.parametrize("change_type", ["Modify", "NoChange", "Deploy", "Delete", "Ignore", "Unknown"])
def test_only_expanded_create_permitted(context, azure, change_type):
    azure.what_if["changes"][0]["changeType"] = change_type
    with pytest.raises(provision.ReconcileError, match="Create"):
        preview(context)
    assert not azure.creates


@pytest.mark.parametrize("mutation", ["missing", "before", "duplicate", "wrong-scope", "failed", "identity", "rbac"])
def test_what_if_expansion_and_scope_fail_closed(context, azure, mutation):
    first = azure.what_if["changes"][0]
    if mutation == "missing":
        first.pop("after")
    elif mutation == "before":
        first["before"] = gateway(context)
    elif mutation == "duplicate":
        azure.what_if["changes"].append(deepcopy(first))
    elif mutation == "wrong-scope":
        first["resourceId"] = context.resource_id.replace(context.resource_group, "another-rg")
    elif mutation == "failed":
        azure.what_if["status"] = "Failed"
    else:
        kind = "Microsoft.ManagedIdentity/userAssignedIdentities" if mutation == "identity" else "Microsoft.Authorization/roleAssignments"
        azure.what_if["changes"].append({
            "resourceId": context.group_id + "/providers/" + kind + "/unwanted", "changeType": "Create",
        })
    with pytest.raises(provision.ReconcileError):
        preview(context)
    assert not azure.creates


def test_unrelated_ignore_is_displayed_and_bound_to_token(context, azure, capsys):
    item = {"resourceId": context.group_id + "/providers/Microsoft.Storage/storageAccounts/keep",
            "changeType": "Ignore"}
    azure.what_if["changes"].append(item)
    result = preview(context)
    assert "Ignore " + item["resourceId"] in capsys.readouterr().out
    azure.what_if["changes"].pop()
    with pytest.raises(provision.ReconcileError, match="review token"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_inventory_changes_during_final_check_block_apply(context, azure):
    result = preview(context)
    checks = 0

    def change_inventory():
        nonlocal checks
        checks += 1
        if checks == 1:
            azure.group["tags"]["concurrent-change"] = "stop"

    azure.on_name_check = change_inventory
    with pytest.raises(provision.ReconcileError, match="inventory/context changed"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_target_appearing_after_what_if_blocks_apply(context, azure):
    result = preview(context)
    azure.on_what_if = lambda: azure.services.append(gateway(context))
    with pytest.raises(provision.ReconcileError, match="APIM already exists"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_artifact_tampering_after_what_if_blocks_apply(context, azure):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "gateway.json"
    azure.on_what_if = lambda: path.write_text('{"malicious":true}', encoding="utf-8")
    with pytest.raises(provision.ReconcileError, match="artifacts changed"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_account_changed_after_what_if_blocks_apply(context, azure):
    result = preview(context)
    azure.on_what_if = lambda: azure.account.update(user="different@example.test")
    with pytest.raises(ValueError, match="Active Azure CLI"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_input_changes_after_what_if_block_apply(context, azure, monkeypatch):
    original = provision.input_fingerprint
    result = preview(context)
    azure.on_what_if = lambda: monkeypatch.setattr(provision, "input_fingerprint", lambda _: "changed")
    with pytest.raises(provision.ReconcileError, match="inputs changed"):
        apply(context, result["reviewToken"])
    assert not azure.creates
    monkeypatch.setattr(provision, "input_fingerprint", original)


@pytest.mark.parametrize("field,value", [
    ("id", "/wrong/resource"), ("type", "Microsoft.Storage/storageAccounts"),
    ("location", "eastus"), ("sku", {"name": "StandardV2", "capacity": 1}),
    ("sku", {"name": "BasicV2", "capacity": True}),
    ("sku", None), ("identity", {"type": "UserAssigned"}), ("properties", []),
])
def test_verification_does_not_claim_success_on_mismatch(context, azure, field, value):
    token = preview(context)["reviewToken"]
    azure.actual[field] = value
    with pytest.raises(provision.ReconcileError):
        apply(context, token)
    assert len(azure.creates) == 1


@pytest.mark.parametrize("identity", [
    {"type": "SystemAssigned"},
    {"type": "SystemAssigned", "principalId": PRINCIPAL, "tenantId": SUBSCRIPTION},
    {"type": "SystemAssigned", "principalId": "malformed", "tenantId": TENANT},
    {"type": "SystemAssigned", "principalId": "00000000-0000-0000-0000-000000000000", "tenantId": TENANT},
    {"type": "SystemAssigned", "principalId": PRINCIPAL, "tenantId": TENANT,
     "userAssignedIdentities": {"/unapproved": {}}},
])
def test_native_principal_verified(context, azure, identity):
    token = preview(context)["reviewToken"]
    azure.actual["identity"] = identity
    with pytest.raises(provision.ReconcileError):
        apply(context, token)


@pytest.mark.parametrize("properties", [
    {"provisioningState": "Failed"}, {"virtualNetworkType": "Internal"},
    {"gatewayUrl": "https://unrelated.example.test"}, {"publisherEmail": "wrong@example.test"},
    {"virtualNetworkConfiguration": {"subnetResourceId": "/unapproved"}},
    {"additionalLocations": [{"location": "eastus"}]},
])
def test_final_gateway_properties_verified(context, azure, properties):
    token = preview(context)["reviewToken"]
    azure.actual["properties"].update(properties)
    with pytest.raises(provision.ReconcileError):
        apply(context, token)


def test_async_deployment_waits_and_failed_state_stops(context, azure, monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda _: None)
    azure.deployment_states = ["Accepted", "Running", "Failed"]
    token = preview(context)["reviewToken"]
    with pytest.raises(provision.ReconcileError, match="failed"):
        apply(context, token)
    assert len(azure.creates) == 1


def test_bounded_wait_does_not_retry_creation(context, azure, monkeypatch):
    monkeypatch.setattr(provision, "WAIT_SECONDS", 0)
    token = preview(context)["reviewToken"]
    with pytest.raises(provision.ReconcileError, match="timed out"):
        apply(context, token)
    assert len(azure.creates) == 1


@pytest.mark.parametrize("response", [
    {}, {"value": None}, {"value": [None]}, {"value": [{"id": "/wrong"}]},
    {"value": [], "nextLink": "https://evil.example.test/steal"},
    {"value": [], "nextLink": 42},
])
def test_incomplete_and_escaped_collections_block(context, response, monkeypatch):
    monkeypatch.setattr(provision, "rest", lambda *a, **kw: deepcopy(response))
    with pytest.raises(provision.ReconcileError):
        provision.collection(context, context.group_id + "/providers/Microsoft.ApiManagement/service", provision.APIM_VERSION)


def test_collection_pagination_checks_all_pages(context, monkeypatch):
    path = context.group_id + "/providers/Microsoft.ApiManagement/service"
    responses = iter([
        {"value": [], "nextLink": "https://management.azure.com" + path + "?next=2"},
        {"value": [gateway(context)]},
    ])
    monkeypatch.setattr(provision, "rest", lambda *a, **kw: next(responses))
    assert provision.collection(context, path, provision.APIM_VERSION) == [gateway(context)]


def test_collection_cycle_blocked(context, monkeypatch):
    path = context.group_id + "/providers/Microsoft.ApiManagement/service"
    monkeypatch.setattr(provision, "rest", lambda *a, **kw: {
        "value": [], "nextLink": path + "?api-version=" + provision.APIM_VERSION})
    with pytest.raises(provision.ReconcileError, match="cycle"):
        provision.collection(context, path, provision.APIM_VERSION)


@pytest.mark.parametrize("response", ["", "null", "[]", "not JSON", '{"error":{"code":"Forbidden"}}', '{"error":{}}'])
def test_json_errors_never_become_absence(response, monkeypatch):
    monkeypatch.setattr(provision, "run", lambda *a, **kw: response)
    with pytest.raises(provision.ReconcileError):
        provision.json_result(["az", "rest"])


@pytest.mark.parametrize("tail", ["unsafe\nlog", "unsafe?query", "unsafe%2fchild"])
def test_what_if_resource_ids_cannot_smuggle_output(context, azure, tail):
    azure.what_if["changes"].append({
        "resourceId": context.group_id + "/providers/Microsoft.Storage/storageAccounts/" + tail,
        "changeType": "Ignore",
    })
    with pytest.raises(provision.ReconcileError):
        preview(context)
    assert not azure.creates


def test_hardlinked_artifact_refused(context, azure, tmp_path):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "gateway.json"
    os.link(path, tmp_path / "linked.json")
    with pytest.raises(ValueError, match="Hard-linked"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_no_customer_platform_or_code_loaded(context, azure, tmp_path):
    (tmp_path / "platform").mkdir()
    (tmp_path / "platform" / "gateway.bicep").write_text("MALICIOUS")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "provision-gateway.py").write_text("raise RuntimeError('MALICIOUS')")
    result = preview(context)
    assert "MALICIOUS" not in (Path(result["artifactDirectory"]) / "gateway.json").read_text()
    assert not (tmp_path / "infra").exists()


def test_reuses_existing_windows_transport_and_restores_configuration(context, monkeypatch, tmp_path):
    adapter = provision.transport()
    previous = adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS
    monkeypatch.setattr(provision, "REPO_ROOT", tmp_path)

    def capture(arguments, capture=False):
        assert adapter.REPO_ROOT == tmp_path and adapter.CLI_TIMEOUT_SECONDS == 300
        assert capture
        return '{"safe":true}'

    monkeypatch.setattr(adapter, "run", capture)
    assert provision.run(["az", "bicep", "build"], timeout=300) == '{"safe":true}'
    assert (adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS) == previous


@pytest.mark.skipif(sys.platform != "win32", reason="Windows isolated Azure CLI locale")
def test_cp1252_transport_in_utf8_host(monkeypatch, tmp_path):
    adapter = provision.transport()
    monkeypatch.setattr(provision, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(adapter.locale, "getencoding", lambda: "cp1252")
    monkeypatch.setattr(adapter.shutil, "which", lambda _: sys.executable)
    result = provision.run(["az", "-I", "-c", r"import sys; sys.stdout.buffer.write(b'{\"name\":\"Demo \x97 APIM\"}')"])
    assert json.loads(result)["name"] == "Demo — APIM"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows isolated Azure CLI locale")
def test_invalid_cp1252_fails_without_raw_output(monkeypatch, tmp_path, capsys):
    adapter = provision.transport()
    monkeypatch.setattr(provision, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(adapter.locale, "getencoding", lambda: "cp1252")
    monkeypatch.setattr(adapter.shutil, "which", lambda _: sys.executable)
    with pytest.raises(provision.ReconcileError, match="could not be decoded"):
        provision.run(["az", "-I", "-c", r"import sys; sys.stdout.buffer.write(b'NEVER-ECHO\x81')"])
    captured = capsys.readouterr()
    assert "NEVER-ECHO" not in captured.out + captured.err


def test_package_layout_executes_from_data_only_workspace(context, tmp_path):
    """A wheel-shaped installation must not resolve tools/platform from cwd."""
    installed = tmp_path / "installed"
    package = installed / "mcp_openapi_creator_kit"
    source_package = ROOT / "src" / "mcp_openapi_creator_kit"
    shutil.copytree(source_package, package, ignore=shutil.ignore_patterns("__pycache__", "assets"))
    commands = package / "_commands"
    commands.mkdir(exist_ok=True)
    for name in ("provision-gateway", "deployment", "lifecycle", "local_python", "retire-client"):
        shutil.copyfile(ROOT / "tools" / (name + ".py"), commands / (name + ".py"))
    assets = package / "assets"
    (assets / "platform").mkdir(parents=True)
    template = assets / "platform" / "gateway.bicep"
    shutil.copyfile(ROOT / "platform" / "gateway.bicep", template)
    (assets / "manifest.json").write_text(json.dumps({
        "platform/gateway.bicep": hashlib.sha256(template.read_bytes()).hexdigest(),
    }))
    workspace = tmp_path / "customer data (not an installation)"
    workspace.mkdir()
    flags = [part for field, value in vars(context).items() for part in ("--" + field.replace("_", "-"), value)]
    script = (
        "import importlib,json,pathlib,sys;"
        f"sys.path.insert(0,{str(installed)!r});"
        "m=importlib.import_module('mcp_openapi_creator_kit._commands.provision-gateway');"
        f"assert pathlib.Path(m.__file__).is_relative_to(pathlib.Path({str(installed)!r}));"
        f"m.REPO_ROOT=pathlib.Path({str(workspace)!r});"
        f"sys.argv=['provision',*{flags!r},'--dry-run'];"
        "r=m.main();assert r['status']=='dry-run' and not r['applied']"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=workspace,
                            capture_output=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(workspace.iterdir()) == []


def test_cli_integration_dispatches_explicit_provision(context, tmp_path, monkeypatch, capsys):
    from mcp_openapi_creator_kit import cli, runtime
    monkeypatch.setitem(runtime.COMMANDS, "provision", "provision-gateway")
    flags = [part for field, value in vars(context).items() for part in ("--" + field.replace("_", "-"), value)]
    assert cli.main(["--workspace", str(tmp_path), "provision", *flags, "--dry-run"]) == 0
    assert '"resourceGroupMode": "existing-only"' in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("extra,expected", [
    ([], "Preview requires"),
    (["--confirm-subscription", "wrong"], "confirmation does not match"),
    (["--confirm-subscription", SUBSCRIPTION, "--yes", "--review-token", "stale"], "review token"),
])
def test_runtime_dispatch_failures_raise_nonzero_not_ignored_returns(context, azure, tmp_path, monkeypatch, capsys, extra, expected):
    from mcp_openapi_creator_kit import cli, runtime
    monkeypatch.setitem(runtime.COMMANDS, "provision", "provision-gateway")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    flags = [part for field, value in vars(context).items() for part in ("--" + field.replace("_", "-"), value)]
    previous_root, previous_argv = provision.REPO_ROOT, sys.argv
    with pytest.raises(SystemExit) as error:
        cli.main(["--workspace", str(tmp_path), "provision", *flags, *extra])
    assert error.value.code == 1
    assert expected in capsys.readouterr().err
    assert provision.REPO_ROOT == previous_root and sys.argv is previous_argv
    assert not azure.creates
    if "--yes" not in extra:
        assert azure.calls == []


def test_preview_does_not_issue_create_receipt(context, azure):
    result = preview(context)
    assert "creationReceiptPath" not in result
    assert not (Path(result["artifactDirectory"]) / "creation-receipt.json").exists()


@pytest.mark.parametrize("tags", [None, {}, {"mcp-kit-owner": "other"}, {
    "mcp-kit-owner": "mcp-openapi-creator-kit", "mcp-kit-creation-id": "wrong",
}])
def test_ownership_tags_required_in_creation_plan(context, azure, tags):
    azure.what_if["changes"][0]["after"]["tags"] = tags
    with pytest.raises(provision.ReconcileError, match="ownership tags"):
        preview(context)
    assert not azure.creates


def test_foreign_gateway_cannot_receive_create_receipt(context, azure):
    token = preview(context)["reviewToken"]
    azure.actual["tags"]["mcp-kit-creation-id"] = "someone-else"
    with pytest.raises(provision.ReconcileError, match="ownership tags"):
        apply(context, token)
    assert len(azure.creates) == 1


@pytest.mark.parametrize("etag", [None, "", "*", "bad\netag"])
def test_receipt_requires_actual_creation_etag(context, azure, etag):
    result = preview(context)
    azure.actual["etag"] = etag
    with pytest.raises(provision.ReconcileError, match="ETag"):
        apply(context, result["reviewToken"])
    assert not (Path(result["artifactDirectory"]) / "creation-receipt.json").exists()
    assert len(azure.creates) == 1


@pytest.mark.parametrize("created_at", [None, "not-time", "2026-09-16T14:00:00"])
def test_receipt_requires_timezone_qualified_service_creation_time(context, azure, created_at):
    result = preview(context)
    azure.actual["properties"]["createdAtUtc"] = created_at
    with pytest.raises(provision.ReconcileError, match="createdAtUtc"):
        apply(context, result["reviewToken"])
    assert not (Path(result["artifactDirectory"]) / "creation-receipt.json").exists()


def test_receipt_path_conflict_blocks_before_create(context, azure):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "creation-receipt.json"
    path.write_text("user-owned-conflict")
    with pytest.raises(provision.ReconcileError, match="receipt already exists"):
        apply(context, result["reviewToken"])
    assert not azure.creates and path.read_text() == "user-owned-conflict"


def test_failed_receipt_persistence_does_not_claim_success(context, azure, monkeypatch):
    token = preview(context)["reviewToken"]

    def unavailable(*args):
        raise OSError("disk full")

    monkeypatch.setattr(provision, "write_creation_receipt", unavailable)
    with pytest.raises(provision.ReconcileError, match="Gateway exists"):
        apply(context, token)
    assert len(azure.creates) == 1
