import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from types import SimpleNamespace

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from deployment import (Context, check_secrets, client_path, context_from_args,
                        normalized_environment, safe_path, summarize_what_if,
                        template_inventory)
from lifecycle import AzRestClient, ReconcileError
from local_python import local_python
from test_build_facade import CONTRACT, MANIFEST, bf

spec = importlib.util.spec_from_file_location("safe_deploy", TOOLS / "deploy-client.py")
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

SUB = str(uuid.UUID(int=0))
TENANT = str(uuid.UUID(int=1))


class DeploymentHarness:
    def __init__(self, root, monkeypatch):
        self.root = root
        self.client_dir = root / "clients" / "demo"
        self.client_dir.mkdir(parents=True)
        contract_dir = root / "apis" / "things"
        contract_dir.mkdir(parents=True)
        (contract_dir / "openapi.yaml").write_text(yaml.safe_dump(CONTRACT), encoding="utf-8")
        self.manifest = copy.deepcopy(MANIFEST)
        self.save_manifest()
        (root / "infra").mkdir()
        (root / "tools").mkdir()
        shutil.copyfile(TOOLS / "validate-deployment-profile.py", root / "tools" / "validate-deployment-profile.py")
        shutil.copytree(TOOLS.parent / "modules", root / "modules")
        monkeypatch.setattr(dc, "REPO_ROOT", root)
        monkeypatch.setattr(bf, "REPO_ROOT", root)
        monkeypatch.setattr(dc, "local_python", lambda _: sys.executable)
        monkeypatch.setattr(dc, "azd_env", lambda: pytest.fail("Explicit mode called azd"))
        monkeypatch.setattr(dc, "run", self.run)
        self.calls = []
        self.profile = "native-mcp"
        self.gateway = {
            "id": self.client.base,
            "sku": {"name": "BasicV2"},
            "identity": {"type": "SystemAssigned", "principalId": "fixture-principal"},
            "properties": {"provisioningState": "Succeeded", "publicNetworkAccess": "Enabled"},
        }
        self.inventory = {}
        self.account = {"id": SUB, "tenantId": TENANT, "user": "operator@example.test"}
        self.what_if_error = None
        self.vault = {
            "id": f"/subscriptions/{SUB}/resourceGroups/fixture/providers/Microsoft.KeyVault/vaults/fixture-vault",
            "properties": {
                "tenantId": TENANT, "accessPolicies": [
                    {"objectId": "fixture-principal", "permissions": {"secrets": ["get", "list"]}}],
            },
        }
        self.secret_metadata = [{"id": "https://fixture-vault.vault.azure.net/secrets/demo-key",
                                 "attributes": {"enabled": True}}]
        self.roles = []
        self.deployment_records = []
        self.deployment_details = {}

    @property
    def context(self):
        return Context(SUB, TENANT, "fixture", "fixture-apim", self.profile)

    @property
    def client(self):
        return AzRestClient(SUB, "fixture", "fixture-apim")

    def save_manifest(self):
        (self.client_dir / "mcp-manifest.yaml").write_text(
            yaml.safe_dump(self.manifest), encoding="utf-8")

    def arguments(self, *extra):
        return ["deploy-client.py", "clients/demo", "--subscription", SUB, "--tenant", TENANT,
                "--resource-group", "fixture", "--apim-name", "fixture-apim",
                "--profile", self.profile, "--confirm-subscription", SUB, *extra]

    def run(self, args, capture=False):
        self.calls.append(args)
        if any(str(value).endswith("build-facade.py") for value in args):
            bf.build_client(self.client_dir)
            return ""
        if any(str(value).endswith("build-policy-mcp.py") for value in args):
            from mcp_openapi_creator_kit.policy import build_client_plan, write_client_plan
            write_client_plan(self.client_dir, build_client_plan(self.root, self.client_dir))
            return ""
        if args[:3] == ["az", "account", "show"]:
            return json.dumps(self.account)
        if args[:3] == ["az", "keyvault", "show"]:
            return json.dumps(self.vault)
        if args[:4] == ["az", "keyvault", "secret", "list"]:
            return json.dumps(self.secret_metadata)
        if args[:4] == ["az", "role", "assignment", "list"]:
            return json.dumps(self.roles)
        if args[:4] == ["az", "deployment", "group", "list"]:
            return json.dumps(self.deployment_records)
        if args[:4] == ["az", "deployment", "group", "show"]:
            return json.dumps(self.deployment_details)
        if args[:2] == ["az", "rest"]:
            assert args[args.index("--subscription") + 1] == SUB
            if args[args.index("--method") + 1] == "DELETE":
                return ""
            path = args[args.index("--uri") + 1].split("?", 1)[0]
            suffix = path.removeprefix(self.client.base)
            return json.dumps(self.gateway if not suffix else {"value": self.inventory.get(suffix, [])})
        if args[:4] == ["az", "deployment", "group", "what-if"]:
            if self.what_if_error is not None:
                return json.dumps(self.what_if_error)
            relative = Path(args[args.index("--template-file") + 1]).relative_to(self.client_dir / "generated").as_posix()
            _, allowed, _ = next(item for item in template_inventory(
                self.client, self.manifest, self.profile, self.client_dir) if item[0] == relative)
            return json.dumps({"status": "Succeeded", "changes": [
                {"resourceId": rid, "changeType": "Create",
                 "before": {"secret": "must-not-print"}, "after": {"secret": "must-not-print"}}
                for rid in sorted(allowed)]})
        if args[:4] == ["az", "deployment", "group", "create"]:
            return ""
        pytest.fail(f"Unexpected command: {args}")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return DeploymentHarness(tmp_path, monkeypatch)


@pytest.mark.parametrize("profile", ["native-mcp", "rest-consumption", "policy-mcp-consumption"])
def test_first_client_explicit_preview_review_apply(harness, monkeypatch, capsys, profile):
    harness.profile = profile
    if profile != "native-mcp":
        harness.gateway["sku"]["name"] = "Consumption"
    monkeypatch.setenv("MCP_RECONCILE_APPLY", "true")
    monkeypatch.setattr(sys, "argv", harness.arguments())
    dc.main()
    output = capsys.readouterr().out
    token = output.split("Review token: ")[1].splitlines()[0]
    assert "azd auth not checked" in output
    assert "Reconciliation DRY-RUN" in output and "ARM what-if" in output
    assert "Microsoft.ApiManagement/service/apis " in output
    assert "Microsoft.Resources/deployments " in output
    assert "must-not-print" not in output
    assert not any("create" in args or "DELETE" in args for args in harness.calls)
    assert not any("azd" == args[0] for args in harness.calls)
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes", "--review-token", token))
    dc.main()
    applies = [args for args in harness.calls if args[:4] == ["az", "deployment", "group", "create"]]
    assert len(applies) == (2 if profile == "policy-mcp-consumption" else 1)
    for args in applies:
        assert args[args.index("--subscription") + 1] == SUB
        path = Path(args[args.index("--template-file") + 1])
        assert path.is_relative_to(harness.client_dir / "generated")
        assert "--mode" in args and "Incremental" in args
        assert "keyVaultName=" in args or profile == "policy-mcp-consumption"
    assert not any("keyvault" in args or "role" in args for args in harness.calls)
    assert all("sample" not in " ".join(args) and "infra/main" not in " ".join(args) for args in harness.calls)


@pytest.mark.parametrize("field", ["id", "tenantId"])
def test_context_mismatch_stops_before_resource_reads(harness, monkeypatch, field):
    harness.account[field] = str(uuid.UUID(int=9))
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes"))
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("rest" in args or "deployment" in args for args in harness.calls)


def test_yes_never_skips_plan_without_matching_token(harness, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes"))
    with pytest.raises(SystemExit):
        dc.main()
    assert "ARM what-if" in capsys.readouterr().out
    assert not any("create" in args or "DELETE" in args for args in harness.calls)


def test_changed_manifest_invalidates_review(harness, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", harness.arguments())
    dc.main()
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    harness.manifest["displayName"] = "Changed"
    harness.save_manifest()
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes", "--review-token", token))
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("create" in args for args in harness.calls)


def test_operator_account_change_invalidates_review(harness, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", harness.arguments())
    dc.main()
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    harness.account["user"] = "another@example.test"
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes", "--review-token", token))
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("create" in args or "DELETE" in args for args in harness.calls)


def test_apply_rechecks_operator_before_final_inventory(harness, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", harness.arguments())
    dc.main()
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    reads = 0
    def run(args, capture=False):
        nonlocal reads
        if args[:3] == ["az", "account", "show"]:
            reads += 1
            if reads == 2:
                harness.account["user"] = "changed-mid-review@example.test"
        return harness.run(args, capture=capture)
    monkeypatch.setattr(dc, "run", run)
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes", "--review-token", token))
    with pytest.raises(SystemExit):
        dc.main()
    assert reads == 2
    assert not any("create" in args or "DELETE" in args for args in harness.calls)


def test_consumption_selection_on_basicv2_blocks_before_what_if(harness, monkeypatch):
    harness.profile = "policy-mcp-consumption"
    monkeypatch.setattr(sys, "argv", harness.arguments())
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("what-if" in args or "create" in args for args in harness.calls)


def test_wrong_gateway_response_blocks_preview(harness, monkeypatch):
    harness.gateway["id"] += "-different"
    monkeypatch.setattr(sys, "argv", harness.arguments())
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("what-if" in args for args in harness.calls)


@pytest.mark.parametrize("suffix,item", [
    ("/apis", {"name": "demo-things", "properties": {"type": "http"}}),
    ("/products", {"name": "demo-product", "properties": {}}),
    ("/subscriptions", {"name": "demo-pilot", "properties": {"scope": "other"}}),
    ("/tags", {"name": "demo", "properties": {"displayName": "demo"}}),
])
def test_unowned_occupancy_blocks_before_what_if(harness, monkeypatch, suffix, item):
    harness.inventory[suffix] = [item]
    monkeypatch.setattr(sys, "argv", harness.arguments())
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("what-if" in args for args in harness.calls)


@pytest.mark.parametrize("tool", [
    {"name": "get-thing"},
    {"name": "different-id", "properties": {"displayName": "get-thing"}},
])
def test_global_native_tool_collision(harness, monkeypatch, tool):
    harness.inventory["/apis"] = [{"name": "other-mcp", "properties": {"type": "mcp"}}]
    harness.inventory["/apis/other-mcp/tools"] = [tool]
    monkeypatch.setattr(sys, "argv", harness.arguments())
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("what-if" in args for args in harness.calls)


def test_unsafe_diagnostics_never_modified(harness, monkeypatch):
    harness.inventory["/diagnostics"] = [
        {"name": "existing", "properties": {"frontend": {"response": {"body": {"bytes": 1}}}}}]
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes"))
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("create" in args or "DELETE" in args or "PUT" in args for args in harness.calls)


@pytest.mark.parametrize("result", [
    {}, {"status": "Failed", "error": {"message": "must-not-print"}},
    {"status": "Succeeded"}, {"status": "Succeeded", "changes": []},
    {"status": "Succeeded", "changes": [{"resourceId": "/unexpected", "changeType": "Create"}]},
])
def test_failed_or_incomplete_what_if_is_closed(harness, monkeypatch, capsys, result):
    harness.what_if_error = result
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes"))
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("create" in args or "DELETE" in args for args in harness.calls)
    assert "must-not-print" not in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["Unsupported", "Ignore", "Delete", "Unknown"])
def test_unsupported_selected_what_if_resource(kind):
    with pytest.raises(ReconcileError):
        summarize_what_if({"status": "Succeeded", "changes": [
            {"resourceId": "/selected", "changeType": kind}]}, {"/selected"}, {"/selected"})


def test_secrets_absent_for_mock_and_required_for_external(harness):
    assert check_secrets(harness.context, harness.manifest, harness.gateway,
                         lambda *_: pytest.fail("Mock must not query vault")) == {}
    harness.manifest["apis"][0]["backend"]["outboundAuth"] = {"secretRef": "demo-key"}
    with pytest.raises(ReconcileError, match="key-vault-name"):
        check_secrets(harness.context, harness.manifest, harness.gateway, harness.run)
    context = Context(SUB, TENANT, "fixture", "fixture-apim", "native-mcp", "fixture-vault")
    check_secrets(context, harness.manifest, harness.gateway, harness.run)
    assert not any("show" in args and "secret" in args or "listSecrets" in " ".join(args)
                   for args in harness.calls)
    harness.vault["properties"]["accessPolicies"] = []
    with pytest.raises(ReconcileError, match="permission"):
        check_secrets(context, harness.manifest, harness.gateway, harness.run)


def test_external_first_client_checks_vault_without_provisioning_it(harness, monkeypatch, capsys):
    harness.manifest["apis"][0]["backend"] = {
        "mode": "external", "url": "https://backend.example.invalid",
        "outboundAuth": {"type": "apiKey", "secretRef": "demo-key"},
    }
    harness.save_manifest()
    arguments = harness.arguments("--key-vault-name", "fixture-vault")
    monkeypatch.setattr(sys, "argv", arguments)
    dc.main()
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    monkeypatch.setattr(sys, "argv", [*arguments, "--yes", "--review-token", token])
    dc.main()
    assert any(args[:4] == ["az", "keyvault", "secret", "list"] for args in harness.calls)
    creates = [args for args in harness.calls if "create" in args]
    assert len(creates) == 1
    assert "keyVaultName=fixture-vault" in creates[0]
    generated = (harness.client_dir / "generated" / "client.bicep").read_text()
    assert "clientId: 'demo'" in generated
    assert "modules/kv-named-values.bicep" in generated
    assert "@minLength(3)" in generated
    assert "platform" not in generated and "roleAssignments" not in generated


def test_named_value_collision_requires_tag_not_just_prefix(harness, monkeypatch):
    harness.manifest["apis"][0]["backend"] = {
        "mode": "external", "url": "https://backend.example.invalid",
        "outboundAuth": {"type": "apiKey", "secretRef": "demo-key"},
    }
    harness.save_manifest()
    harness.inventory["/namedValues"] = [{"name": "demo-key", "properties": {"tags": ["other"]}}]
    monkeypatch.setattr(sys, "argv", harness.arguments("--key-vault-name", "fixture-vault"))
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("what-if" in args or "create" in args for args in harness.calls)


def test_reconciliation_deletes_only_after_review_then_deploys(harness, monkeypatch, capsys):
    harness.inventory["/apis"] = [
        {"name": "demo-old-mcp", "properties": {"type": "mcp"}},
        {"name": "other-api", "properties": {"type": "http"}},
    ]
    harness.inventory["/apis/demo-old-mcp/tags"] = [{"name": "demo"}]
    harness.inventory["/apis/demo-old-mcp/tools"] = [{"name": "old-tool"}]
    monkeypatch.setenv("MCP_RECONCILE_APPLY", "true")
    monkeypatch.setattr(sys, "argv", harness.arguments())
    dc.main()
    output = capsys.readouterr().out
    token = output.split("Review token: ")[1].splitlines()[0]
    assert "DELETE tool demo-old-mcp/old-tool" in output
    assert not any("DELETE" in args for args in harness.calls)
    monkeypatch.setattr(sys, "argv", harness.arguments("--yes", "--review-token", token))
    dc.main()
    deletes = [index for index, args in enumerate(harness.calls) if "DELETE" in args]
    create = next(index for index, args in enumerate(harness.calls) if "create" in args)
    assert len(deletes) == 2 and max(deletes) < create
    assert all("demo-old-mcp" in " ".join(harness.calls[index]) for index in deletes)


def test_context_confirmation_mismatch_blocks_resource_reads(harness, monkeypatch):
    arguments = harness.arguments()
    arguments[-1] = "wrong"
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("rest" in args for args in harness.calls)


def test_unowned_deployment_record_not_overwritten(harness, monkeypatch):
    harness.deployment_records = [{"name": "product-demo"}]
    harness.deployment_details = {"apim": "other-apim", "client": "demo"}
    monkeypatch.setattr(sys, "argv", harness.arguments())
    with pytest.raises(SystemExit):
        dc.main()
    assert not any("what-if" in args or "create" in args for args in harness.calls)


def test_role_permission_requires_direct_unconditional_supported_grant(harness):
    harness.manifest["apis"][0]["backend"]["outboundAuth"] = {"secretRef": "demo-key"}
    harness.vault["properties"]["enableRbacAuthorization"] = True
    context = Context(SUB, TENANT, "fixture", "fixture-apim", "native-mcp", "fixture-vault")
    with pytest.raises(ReconcileError, match="permission"):
        check_secrets(context, harness.manifest, harness.gateway, harness.run)
    harness.roles = [{
        "principalId": "fixture-principal", "scope": harness.vault["id"],
        "roleDefinitionId": "4633458b-17de-408a-b874-0445c86b69e6",
    }]
    check_secrets(context, harness.manifest, harness.gateway, harness.run)
    harness.roles[0]["condition"] = "conditional grant"
    with pytest.raises(ReconcileError, match="permission"):
        check_secrets(context, harness.manifest, harness.gateway, harness.run)


def test_missing_secret_metadata_fails(harness):
    harness.manifest["apis"][0]["backend"]["outboundAuth"] = {"secretRef": "demo-key"}
    harness.secret_metadata = []
    with pytest.raises(ReconcileError, match="missing/disabled"):
        check_secrets(Context(SUB, TENANT, "fixture", "fixture-apim", "native-mcp", "fixture-vault"),
                      harness.manifest, harness.gateway, harness.run)


def test_context_partial_and_legacy_output_validation():
    args = argparse.Namespace(subscription=SUB, tenant=None, resource_group=None,
                              apim_name=None, profile=None, key_vault_name=None)
    with pytest.raises(ReconcileError, match="Explicit context requires"):
        context_from_args(args, lambda: pytest.fail("No azd fallback"))
    args.subscription = None
    env = {"AZURE_SUBSCRIPTION_ID": SUB, "AZURE_TENANT_ID": TENANT,
           "AZURE_RESOURCE_GROUP": "fixture", "apimName": "fixture-apim",
           "GATEWAY_PROFILE": "native-mcp", "AZURE_ENV_NAME": "fixture"}
    assert context_from_args(args, lambda: env).key_vault == ""
    with pytest.raises(ReconcileError, match="Ambiguous"):
        normalized_environment({"APIM_NAME": "one", "apimName": "two"})


def test_complete_legacy_azd_context_still_previews(harness, monkeypatch, capsys):
    monkeypatch.setattr(dc, "azd_env", lambda: {
        "AZURE_SUBSCRIPTION_ID": SUB, "AZURE_TENANT_ID": TENANT,
        "AZURE_RESOURCE_GROUP": "fixture", "apimName": "fixture-apim",
        "GATEWAY_PROFILE": "native-mcp", "AZURE_ENV_NAME": "fixture",
    })
    monkeypatch.setattr(sys, "argv", ["deploy-client.py", "clients/demo", "--confirm-subscription", SUB])
    dc.main()
    output = capsys.readouterr().out
    assert "fixture (outputs only; azd auth not used/checked)" in output
    assert "ARM what-if" in output
    assert not any("create" in args for args in harness.calls)


@pytest.mark.parametrize("raw", ["../outside", "clients/demo/../demo", "clients/demo/generated", "clients/--bad"])
def test_unsafe_selected_paths_rejected(harness, raw):
    with pytest.raises(ReconcileError):
        client_path(harness.root, raw)


def test_arm_pagination_cannot_escape_subscription_or_repeat():
    client = AzRestClient(SUB, "fixture", "fixture-apim", runner=lambda args: json.dumps({
        "value": [], "nextLink": "https://evil.invalid/" }))
    with pytest.raises(ReconcileError):
        client.list_apis()
    with pytest.raises(ReconcileError):
        client.request("GET", client.base + "/../other")
    with pytest.raises(ReconcileError):
        client.request("GET", "//evil.invalid" + client.base + "/apis")


def test_local_python_fails_without_local_environment(tmp_path):
    with pytest.raises(RuntimeError, match="Missing repository .venv"):
        local_python(tmp_path)


def test_local_python_old_version_is_actionable(tmp_path, monkeypatch):
    import local_python as isolation
    monkeypatch.setattr(isolation, "sys", SimpleNamespace(version_info=(3, 11)))
    with pytest.raises(RuntimeError, match="Python >=3.12"):
        isolation.local_python(tmp_path)


def test_symlink_and_junction_guard_even_inside_root(tmp_path, monkeypatch):
    file = tmp_path / "input"
    file.write_text("safe", encoding="utf-8")
    monkeypatch.setattr(Path, "is_junction", lambda path: path == file)
    with pytest.raises(ReconcileError, match="junction"):
        safe_path(tmp_path, file)


def test_hardlinked_generated_output_is_rejected(tmp_path):
    original = tmp_path / "original"
    original.write_text("untouched", encoding="utf-8")
    linked = tmp_path / "linked"
    os.link(original, linked)
    with pytest.raises(ReconcileError, match="Hard-linked"):
        safe_path(tmp_path, linked)
    assert original.read_text() == "untouched"
