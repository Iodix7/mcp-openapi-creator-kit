"""Standalone resource-group creation: strict fake Azure, no cloud access."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

provision = importlib.import_module("mcp_openapi_creator_kit._commands.provision-resource-group")
REAL_RUN = provision.run
ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def context():
    return provision.ProvisionContext("operator@example.test", TENANT, SUBSCRIPTION,
                                      "new-customer-rg", "westeurope")


def group(context):
    return {"id": context.resource_id, "name": context.resource_group,
            "type": provision.GROUP_TYPE, "location": context.location, "tags": context.tags,
            "properties": {"provisioningState": "Succeeded"}}


class Azure:
    def __init__(self, context):
        self.context = context
        self.calls = []
        self.creates = []
        self.account = {"user": context.account, "tenantId": context.tenant, "id": context.subscription}
        self.cloud = {"name": "AzureCloud", "resourceManager": "https://management.azure.com/"}
        self.compiled = provision.expected_template()
        self.groups = {"value": []}
        self.histories = {"value": []}
        self.pages = {}
        self.what_if = {"status": "Succeeded", "changes": [{
            "resourceId": context.resource_id, "changeType": "Create", "after": group(context)}]}
        self.actual = group(context)
        self.deployment_states = ["Succeeded"]
        self.after_what_if = None
        self.on_create = None
        self.before_groups = None
        self.group_reads = 0

    def __call__(self, args, *, timeout=60):
        self.calls.append(args[:])
        context = self.context
        assert args[0] == "az"
        if args[1:3] == ["account", "show"]:
            return json.dumps(self.account)
        if args[1:3] == ["cloud", "show"]:
            return json.dumps(self.cloud)
        if args[1:3] == ["bicep", "build"]:
            assert Path(args[args.index("--file") + 1]) == provision.kit_root() / provision.ASSET
            assert "--stdout" in args
            return json.dumps(self.compiled)
        assert args[args.index("--subscription") + 1] == context.subscription
        if args[1:3] == ["deployment", "sub"]:
            assert args[args.index("--location") + 1] == context.location
            assert "--resource-group" not in args
            template = Path(args[args.index("--template-file") + 1])
            assert json.loads(template.read_bytes()) == self.compiled
            params = args[args.index("--parameters") + 1]
            assert params.startswith("@")
            assert json.loads(Path(params[1:]).read_bytes()) == context.parameters()
            if args[3] == "what-if":
                assert args[args.index("--result-format") + 1] == "FullResourcePayloads"
                if self.after_what_if:
                    self.after_what_if()
                return json.dumps(self.what_if)
            assert args[3] == "create" and "--no-wait" in args
            self.creates.append(args)
            if self.on_create:
                self.on_create()
            return ""
        assert args[1] == "rest"
        assert args[args.index("--method") + 1] == "GET"
        uri = args[args.index("--uri") + 1]
        path = uri.split("?")[0]
        if uri in self.pages:
            return json.dumps(self.pages[uri])
        if path == context.scope + "/resourceGroups":
            self.group_reads += 1
            if self.before_groups:
                self.before_groups()
            return json.dumps(self.groups)
        if path == context.scope + "/providers/Microsoft.Resources/deployments":
            return json.dumps(self.histories)
        if path.startswith(context.scope + "/providers/Microsoft.Resources/deployments/"):
            state = self.deployment_states[0]
            if len(self.deployment_states) > 1:
                self.deployment_states.pop(0)
            return json.dumps({"id": path, "properties": {"provisioningState": state}})
        if path == context.resource_id:
            return json.dumps(self.actual)
        raise AssertionError(f"Unexpected Azure URI: {uri}")


@pytest.fixture
def azure(context, tmp_path, monkeypatch):
    assert Path(provision.__file__).resolve() == ROOT / "tools" / "provision-resource-group.py"
    monkeypatch.setattr(provision, "REPO_ROOT", tmp_path)
    azure = Azure(context)
    monkeypatch.setattr(provision, "run", azure)
    return azure


def preview(context):
    return provision.provision(context, confirmation=context.subscription)


def apply(context, token):
    return provision.provision(context, confirmation=context.subscription, apply=True, review_token=token)


def test_dry_run_is_offline_and_write_free(context, azure, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run attempted subprocess or write")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    result = provision.provision(context, dry_run=True)
    assert result["status"] == "dry-run" and not result["azureVerified"] and not result["applied"]
    assert not result["authorizesDeletion"] and "reviewToken" not in result
    assert result["context"]["azdEnvironment"] == "not used"
    assert azure.calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", [
    ("account", ""), ("account", " operator@example.test"), ("account", "bad\naccount"),
    ("tenant", "bad"), ("subscription", SUBSCRIPTION.replace("-", "")),
    ("resource_group", "../bad"), ("resource_group", "bad\\path"), ("resource_group", "bad."),
    ("resource_group", "a?x"), ("location", "West Europe"), ("location", ""),
])
def test_invalid_context_is_offline(context, azure, field, value):
    with pytest.raises((RuntimeError, ValueError)):
        provision.provision(replace(context, **{field: value}), dry_run=True)
    assert not azure.calls


@pytest.mark.parametrize("options", [
    {"confirmation": "wrong"}, {"dry_run": True, "apply": True},
    {"dry_run": True, "review_token": "x"}, {"review_token": "x"},
])
def test_invalid_confirmation_is_offline(context, azure, options):
    with pytest.raises(provision.ReconcileError):
        provision.provision(context, **options)
    assert not azure.calls


@pytest.mark.parametrize("field", ["user", "tenantId", "id"])
def test_wrong_active_account_never_reads_resources(context, azure, field):
    azure.account[field] = "other"
    with pytest.raises(ValueError, match="Active Azure CLI"):
        preview(context)
    assert all(call[1:3] == ["account", "show"] for call in azure.calls)


def test_public_cloud_required(context, azure):
    azure.cloud["name"] = "AzureUSGovernment"
    with pytest.raises(provision.ReconcileError, match="public cloud"):
        preview(context)
    assert not any(call[1] == "rest" for call in azure.calls)


@pytest.mark.parametrize("inventory", [
    {}, {"value": None}, {"value": {}}, {"error": {"code": "Forbidden"}},
    {"value": [None]}, {"value": [{}]}, {"value": [{"id": "/subscriptions/other/resourceGroups/a"}]},
    {"value": [{"id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/../a"}]},
    {"value": [{"id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/a%2fb"}]},
    {"value": [{"id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/a?x"}]},
    {"value": [], "nextLink": 1},
])
def test_malformed_inventory_fails_closed(context, azure, inventory):
    azure.groups = inventory
    with pytest.raises((provision.ReconcileError, ValueError)):
        preview(context)
    assert not azure.creates


@pytest.mark.parametrize("link", [
    "https://evil.test/subscriptions/x/resourceGroups?api-version=2022-09-01",
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups?api-version=2022-09-01",
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups?api-version=bad&$skiptoken=one",
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups?api-version=2022-09-01&api-version=2022-09-01",
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/other?api-version=2022-09-01",
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups?api-version=2022-09-01&$skiptoken=one#fragment",
])
def test_pagination_routes_are_exact(context, azure, link):
    azure.groups["nextLink"] = link
    with pytest.raises(provision.ReconcileError, match="pagination"):
        preview(context)
    assert not azure.creates


def test_complete_pagination_detects_existing_group_case_insensitively(context, azure):
    link = f"https://management.azure.com{context.scope}/resourceGroups?api-version=2022-09-01&$skiptoken=next"
    azure.groups["nextLink"] = link
    azure.pages[link] = {"value": [{"id": context.resource_id.upper()}]}
    with pytest.raises(provision.ReconcileError, match="already exists"):
        preview(context)
    assert not azure.creates


def test_inventory_duplicates_and_page_limit(context, azure, monkeypatch):
    azure.groups["value"] = [{"id": context.scope + "/resourceGroups/a"}] * 2
    with pytest.raises(provision.ReconcileError, match="duplicate"):
        preview(context)
    azure.groups = {"value": [], "nextLink": f"{context.scope}/resourceGroups?api-version=2022-09-01&$skiptoken=next"}
    monkeypatch.setattr(provision, "MAX_PAGES", 1)
    with pytest.raises(provision.ReconcileError, match="page limit"):
        preview(context)


@pytest.mark.parametrize("mutation", ["resources", "variables", "condition", "location", "parameters", "schema"])
def test_compiled_template_is_exact(context, azure, mutation):
    if mutation == "resources":
        azure.compiled["resources"].append({"type": "Microsoft.ApiManagement/service"})
    elif mutation in {"condition", "location"}:
        azure.compiled["resources"][0][mutation] = "unapproved"
    elif mutation == "schema":
        azure.compiled["$schema"] = "resource-group-scope"
    else:
        azure.compiled[mutation] = {}
    with pytest.raises(provision.ReconcileError, match="Compiled template"):
        preview(context)
    assert not azure.creates


@pytest.mark.parametrize("change_type", ["Modify", "Delete", "NoChange", "Ignore", "Deploy", "Unsupported", None])
def test_no_changes_to_existing_groups_or_unexpanded_plan(context, azure, change_type):
    azure.what_if["changes"][0]["changeType"] = change_type
    with pytest.raises(provision.ReconcileError, match="Create"):
        preview(context)
    assert not azure.creates


@pytest.mark.parametrize("mutation", ["extra", "empty", "id", "tags", "location", "before"])
def test_what_if_requires_exact_group(context, azure, mutation):
    change = azure.what_if["changes"][0]
    if mutation == "extra":
        azure.what_if["changes"].append(deepcopy(change))
    elif mutation == "empty":
        azure.what_if["changes"] = []
    elif mutation == "before":
        change["before"] = {}
    else:
        change["after"][mutation] = "different"
    with pytest.raises(provision.ReconcileError):
        preview(context)
    assert not azure.creates


def test_preview_apply_and_durable_non_deletion_receipt(context, azure, monkeypatch):
    preview_result = preview(context)
    assert preview_result["status"] == "preview" and not azure.creates
    assert preview_result["deploymentResourceId"].startswith(context.scope + "/providers/")
    azure.deployment_states = ["Running", "Succeeded"]
    monkeypatch.setattr(provision.time, "sleep", lambda _: None)
    result = apply(context, preview_result["reviewToken"])
    assert result["status"] == "provisioned" and result["applied"]
    assert len(azure.creates) == 1
    receipt = json.loads(Path(result["creationReceiptPath"]).read_bytes())
    assert receipt["kind"] == "verified-resource-group-create"
    assert receipt["resourceId"] == context.resource_id
    assert receipt["tags"] == context.tags and receipt["provisioningState"] == "Succeeded"
    assert receipt["authorizesDeletion"] is False
    assert provision.attempt_path(context).is_file()
    with pytest.raises(provision.ReconcileError, match="attempt"):
        apply(context, preview_result["reviewToken"])
    assert len(azure.creates) == 1


@pytest.mark.parametrize("token", [None, "wrong"])
def test_apply_requires_reviewed_token(context, azure, token):
    with pytest.raises(provision.ReconcileError, match="review token"):
        apply(context, token)
    assert not azure.creates and not provision.attempt_path(context).exists()


def test_changed_inventory_invalidates_token(context, azure):
    result = preview(context)
    azure.groups["value"] = [{"id": context.scope + "/resourceGroups/another"}]
    with pytest.raises(provision.ReconcileError, match="review token"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_final_inventory_change_blocks_apply(context, azure):
    result = preview(context)
    def drift():
        if azure.group_reads == 3:
            azure.groups["value"] = [{"id": context.scope + "/resourceGroups/another"}]
    azure.before_groups = drift
    with pytest.raises(provision.ReconcileError, match="inventory/context changed"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_existing_subscription_history_is_never_overwritten(context, azure):
    result = preview(context)
    azure.histories["value"] = [{"id": result["deploymentResourceId"]}]
    with pytest.raises(provision.ReconcileError, match="history already exists"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_artifact_tampering_blocks(context, azure):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "parameters.json"
    azure.after_what_if = lambda: path.write_text("tampered", encoding="utf-8")
    with pytest.raises(provision.ReconcileError, match="artifacts changed"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_installed_fingerprint_drift_blocks(context, azure, monkeypatch):
    original = provision.input_fingerprint
    azure.after_what_if = lambda: monkeypatch.setattr(provision, "input_fingerprint", lambda _: "changed")
    with pytest.raises(provision.ReconcileError, match="inputs changed"):
        preview(context)
    assert not azure.creates
    monkeypatch.setattr(provision, "input_fingerprint", original)


def test_conflicting_receipt_prevents_cloud_write(context, azure):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "creation-receipt.json"
    path.write_text("preserve", encoding="utf-8")
    with pytest.raises(provision.ReconcileError, match="receipt already exists"):
        apply(context, result["reviewToken"])
    assert path.read_text() == "preserve" and not azure.creates


@pytest.mark.parametrize("mutation", ["id", "location", "tags", "state", "failure", "timeout", "receipt"])
def test_partial_unknown_outcome_never_retries_or_deletes(context, azure, monkeypatch, mutation):
    result = preview(context)
    if mutation == "state":
        azure.actual["properties"]["provisioningState"] = "Failed"
    elif mutation == "failure":
        def fail():
            raise provision.ReconcileError("transport failed")
        azure.on_create = fail
    elif mutation == "timeout":
        monkeypatch.setattr(provision, "WAIT_SECONDS", 0)
    elif mutation == "receipt":
        original = provision.write_exclusive
        def fail_receipt(path, value):
            if path.name == "creation-receipt.json":
                raise OSError("disk full")
            return original(path, value)
        monkeypatch.setattr(provision, "write_exclusive", fail_receipt)
    else:
        azure.actual[mutation] = "wrong"
    with pytest.raises(provision.ReconcileError, match="partial/unknown"):
        apply(context, result["reviewToken"])
    assert len(azure.creates) == 1
    assert provision.attempt_path(context).exists()
    assert not (Path(result["artifactDirectory"]) / "creation-receipt.json").exists()
    with pytest.raises(provision.ReconcileError, match="attempt"):
        preview(context)
    assert len(azure.creates) == 1


def test_hardlinked_artifact_rejected(context, azure, tmp_path):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "parameters.json"
    (tmp_path / "hardlink").hardlink_to(path)
    with pytest.raises(ValueError, match="Hard-linked"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_inventory_permission_failure_is_not_absence(context, azure):
    def forbidden():
        raise provision.ReconcileError("ARM Forbidden")
    azure.before_groups = forbidden
    with pytest.raises(provision.ReconcileError, match="Forbidden"):
        preview(context)
    assert not azure.creates


def test_cli_dry_run_contract(context, azure, monkeypatch, capsys):
    args = ["provision-group"]
    for name in ("account", "tenant", "subscription", "resource_group", "location"):
        args.extend(["--" + name.replace("_", "-"), getattr(context, name)])
    monkeypatch.setattr(sys, "argv", [*args, "--dry-run"])
    provision.main()
    assert '"status": "dry-run"' in capsys.readouterr().out
    assert not azure.calls


def test_final_context_drift_prevents_apply(context, azure):
    result = preview(context)
    def drift():
        if azure.group_reads == 3:
            azure.account["tenantId"] = "other"
    azure.before_groups = drift
    with pytest.raises(ValueError, match="Active Azure CLI"):
        apply(context, result["reviewToken"])
    assert not azure.creates


def test_receipt_race_preserves_other_receipt(context, azure):
    result = preview(context)
    path = Path(result["artifactDirectory"]) / "creation-receipt.json"
    azure.on_create = lambda: path.write_text("other receipt", encoding="utf-8")
    with pytest.raises(provision.ReconcileError, match="partial/unknown"):
        apply(context, result["reviewToken"])
    assert path.read_text() == "other receipt"
    assert len(azure.creates) == 1


def test_transport_binds_privately_and_reuses_safe_runner(context, azure, monkeypatch, tmp_path):
    adapter = provision.gateway.transport()
    previous = adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS
    calls = []
    class Process:
        returncode = 0
        stdout = None
        stderr = None

        def communicate(self, timeout):
            calls.append(timeout)
            return b'{"value":[]}', b""
    def launch(*args, **kwargs):
        assert kwargs["cwd"] == tmp_path
        calls.append(kwargs)
        return Process()
    monkeypatch.setattr(adapter.shutil, "which", lambda _: "az.exe")
    monkeypatch.setattr(adapter.subprocess, "Popen", launch)
    assert REAL_RUN(["az", "account", "show"], timeout=123) == '{"value":[]}'
    assert calls[-1] == 123
    assert (adapter.REPO_ROOT, adapter.CLI_TIMEOUT_SECONDS) == previous
