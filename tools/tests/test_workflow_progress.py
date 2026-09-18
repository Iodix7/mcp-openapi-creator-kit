"""Local completion history is stale-aware and cannot become cloud authority."""
from datetime import datetime, timedelta, timezone
import json
import os
import shutil

import pytest
import yaml

from mcp_openapi_creator_kit import cli, gateway, progress
from mcp_openapi_creator_kit.runtime import command
from mcp_openapi_creator_kit.workspace import WorkspaceReader
from offline_scenarios import PREVIEW_ARGUMENTS, SyntheticAzure
from test_workflow import target


def spec(root, client="fixture"):
    from mcp_openapi_creator_kit.scenario import inventory, reference_markdown
    path = root / "docs" / client / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Mock demonstration\nOperator review remains required.\n" +
                    reference_markdown(inventory(root, client)), encoding="utf-8")
    return path


def prepare(root, client="fixture", profile="native-mcp"):
    spec(root, client)
    cli.main(["--workspace", str(root), "prepare", f"clients/{client}", "--profile", profile])


def reader(root, monkeypatch, client="fixture"):
    azure = SyntheticAzure(root)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    workspace = WorkspaceReader(root)
    observation = workspace.gateway_evidence.inspect(target())
    workspace.workflow_status(client, requires_mcp=True, gateway_mode="existing", evidence_id=observation.evidence_id)
    return workspace


def step(status, name):
    return next(item.model_dump(mode="json", by_alias=True) for item in status.steps if item.id == name)


def test_files_or_plain_build_are_not_completed_preparation(mcp_workspace, monkeypatch):
    workspace = reader(mcp_workspace, monkeypatch)
    before = workspace.workflow_status("fixture")
    assert before.completion == "local-incomplete" and before.status == "ready-for-preparation"
    assert "deploy-preview" not in before.allowed_actions
    spec(mcp_workspace)
    cli.main(["--workspace", str(mcp_workspace), "build", "clients/fixture"])
    status = workspace.workflow_status("fixture")
    assert step(status, "build")["status"] == "recorded-current"
    assert step(status, "prepare")["status"] == "missing"
    assert "prepare" in status.next_command and status.completion == "local-incomplete"
    assert not (mcp_workspace / "infra").exists()


@pytest.mark.parametrize("profile", ["native-mcp", "rest-consumption", "policy-mcp-consumption"])
def test_prepare_runs_profile_specific_build_without_azure(mcp_workspace, monkeypatch, profile):
    monkeypatch.setattr(gateway, "run_azure", lambda _: pytest.fail("Offline prepare accessed Azure"))
    prepare(mcp_workspace, profile=profile)
    inputs = progress.input_digest(mcp_workspace, "fixture")
    assert progress.read_step(mcp_workspace, "fixture", "prepare", inputs, profile=profile)["status"] == "recorded-current"
    policy = mcp_workspace / "clients" / "fixture" / "generated" / "policy-mcp" / "client.bicep"
    assert policy.exists() == (profile == "policy-mcp-consumption")
    status = WorkspaceReader(mcp_workspace).workflow_status("fixture", requires_mcp=True, gateway_mode="existing")
    assert status.evidence is None and status.profile is None and status.approval_status == "not-granted"


@pytest.mark.parametrize("profile", ["native-mcp", "rest-consumption", "policy-mcp-consumption"])
def test_prepare_rejects_numeric_exclusive_bound_offline(mcp_workspace, monkeypatch, profile, capsys):
    monkeypatch.setattr(gateway, "run_azure", lambda _: pytest.fail("Invalid contract reached Azure"))
    contract_path = mcp_workspace / "apis" / "customer-care" / "openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text("utf-8"))
    contract.setdefault("components", {}).setdefault("schemas", {})["Quantity"] = {
        "type": "number", "exclusiveMinimum": 0,
    }
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        prepare(mcp_workspace, profile=profile)
    assert "exclusiveMinimum" in capsys.readouterr().err
    assert not (mcp_workspace / "clients" / "fixture" / "generated").exists()
    inputs = progress.input_digest(mcp_workspace, "fixture")
    assert progress.read_step(mcp_workspace, "fixture", "prepare", inputs,
                              profile=profile)["status"] != "recorded-current"


@pytest.mark.parametrize("mutation", ["spec", "manifest", "contract", "generated", "kit", "profile"])
def test_changes_invalidate_recorded_preparation(mcp_workspace, monkeypatch, mutation):
    prepare(mcp_workspace)
    workspace = reader(mcp_workspace, monkeypatch)
    assert workspace.workflow_status("fixture").completion == "preview-required"
    if mutation == "profile":
        inputs = progress.input_digest(mcp_workspace, "fixture")
        assert progress.read_step(mcp_workspace, "fixture", "prepare", inputs, profile="rest-consumption")["status"] == "stale"
        return
    if mutation == "kit":
        original = progress.verify_assets
        monkeypatch.setattr(progress, "verify_assets", lambda: {**original(), "version": "different"})
    else:
        paths = {
            "spec": mcp_workspace / "docs" / "fixture" / "spec.md",
            "manifest": mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml",
            "contract": mcp_workspace / "apis" / "customer-care" / "openapi.yaml",
            "generated": mcp_workspace / "clients" / "fixture" / "generated" / "client.bicep",
        }
        path = paths[mutation]
        path.write_text(path.read_text("utf-8") + "\n# edited\n", encoding="utf-8")
    status = workspace.workflow_status("fixture")
    assert status.completion == "local-incomplete" and step(status, "prepare")["status"] == "stale"
    assert status.approval_status == "not-granted"


def test_failed_rerun_does_not_leave_current_success(mcp_workspace, monkeypatch):
    prepare(mcp_workspace)
    def fail(*_args):
        raise SystemExit(1)
    monkeypatch.setattr(cli, "invoke", fail)
    with pytest.raises(SystemExit):
        cli.main(["--workspace", str(mcp_workspace), "prepare", "clients/fixture", "--profile", "native-mcp"])
    inputs = progress.input_digest(mcp_workspace, "fixture")
    assert progress.read_step(mcp_workspace, "fixture", "prepare", inputs)["status"] == "incomplete"


def test_receipts_are_workspace_bound_and_malformed_fails_closed(mcp_workspace, tmp_path_factory):
    prepare(mcp_workspace)
    other = tmp_path_factory.mktemp("another-workspace")
    shutil.copytree(mcp_workspace, other, dirs_exist_ok=True)
    inputs = progress.input_digest(other, "fixture")
    assert progress.read_step(other, "fixture", "prepare", inputs)["status"] == "stale"
    path = progress.receipt_path(other, "fixture", "prepare")
    for text in ("[]", "{broken", '{"version":1,"client":"other","step":"prepare"}'):
        path.write_text(text, encoding="utf-8")
        assert progress.read_step(other, "fixture", "prepare", inputs)["status"] == "invalid"
    path.write_text(" " * (progress.MAX_RECEIPT_BYTES + 1), encoding="utf-8")
    assert progress.read_step(other, "fixture", "prepare", inputs)["status"] == "invalid"


def test_receipt_path_hardlink_rejected_before_generated_output_changes(mcp_workspace):
    prepare(mcp_workspace)
    path = progress.receipt_path(mcp_workspace, "fixture", "prepare")
    os.link(path, mcp_workspace / "protected-history")
    generated = mcp_workspace / "clients" / "fixture" / "generated" / "client.bicep"
    before = generated.read_bytes()
    with pytest.raises(SystemExit):
        prepare(mcp_workspace)
    assert generated.read_bytes() == before


def test_matching_preview_surfaces_plan_but_never_approval(tmp_path, monkeypatch):
    cli.main(["--workspace", str(tmp_path), "import-sample", "acme", "--write"])
    prepare(tmp_path, "acme")
    azure = SyntheticAzure(tmp_path)
    monkeypatch.setattr(command("deploy-client"), "run", azure.run)
    cli.main(["--workspace", str(tmp_path), *PREVIEW_ARGUMENTS])
    workspace = reader(tmp_path, monkeypatch, "acme")
    status = workspace.workflow_status("acme")
    assert status.completion == status.status == "preview-recorded"
    assert status.current_step.stage == "review-plan"
    assert not status.next_command
    assert step(status, "preview")["plan"]["reviewToken"]
    assert status.approval_status == "not-granted" and "apply" not in status.allowed_actions
    assert all(args[:4] != ["az", "deployment", "group", "create"] and "DELETE" not in args for args in azure.calls)
    index, html = workspace.dashboard()
    assert index["workflow"][0] == status.model_dump(mode="json", by_alias=True)
    assert "preview-recorded" in html and "Recorded preview plan (not approval)" in html
    inputs = progress.input_digest(tmp_path, "acme")
    changed = target().model_copy(update={"apim_name": "other-apim"}).model_dump(mode="json", by_alias=True)
    assert progress.read_step(tmp_path, "acme", "preview", inputs, profile="native-mcp", target=changed)["status"] == "stale"
    equivalent = target().model_dump(mode="json", by_alias=True)
    for name in ("account", "resourceGroup", "apimName"):
        equivalent[name] = equivalent[name].upper()
    assert progress.read_step(tmp_path, "acme", "preview", inputs, profile="native-mcp", target=equivalent)["status"] == "recorded-current"
    assert "Contract validation" not in status.checks_pending
    path = progress.receipt_path(tmp_path, "acme", "preview")
    value = json.loads(path.read_text("utf-8"))
    malformed = {**value, "plan": {"reviewToken": "forged"}}
    path.write_text(json.dumps(malformed), encoding="utf-8")
    assert step(workspace.workflow_status("acme"), "preview")["status"] == "invalid"
    value["recordedAt"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    path.write_text(json.dumps(value), encoding="utf-8")
    assert workspace.workflow_status("acme").completion == "preview-required"
    assert workspace.workflow_status("acme").current_step.stage == "preview"


def test_static_catalog_does_not_embed_nondeterministic_receipts(mcp_workspace):
    from mcp_openapi_creator_kit.catalog import build_index
    before = build_index(mcp_workspace)
    prepare(mcp_workspace)
    assert build_index(mcp_workspace) == before
