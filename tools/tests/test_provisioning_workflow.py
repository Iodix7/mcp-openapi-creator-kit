"""Proposed gateway context is useful for commands, never observed cloud evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
from mcp import Client

from mcp_openapi_creator_kit import __version__, cli, gateway
from mcp_openapi_creator_kit.server import create_server
from mcp_openapi_creator_kit.workflow import GatewayProvisioningTarget, evaluate_workflow
from mcp_openapi_creator_kit.workspace import WorkspaceReader
from test_workflow import MANIFEST, target
from test_workflow_progress import prepare, spec


def proposed():
    return GatewayProvisioningTarget(
        **target().model_dump(), location="westeurope",
        publisher_name="Fictional Publisher", publisher_email="publisher@example.test",
    )


def test_package_versions_match():
    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__


def test_installed_dispatch_has_read_only_provision_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("Offline proposal started a process"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("Offline proposal started a process"))
    flags = []
    for name, value in proposed().model_dump(
        exclude={"resource_group_mode", "resource_group_location"},
    ).items():
        flags.extend(["--" + name.replace("_", "-"), value])
    assert cli.main([
        "--workspace", str(tmp_path), "provision", *flags,
        "--profile", "policy-mcp-consumption", "--dry-run",
    ]) == 0
    output = capsys.readouterr().out
    assert '"status": "dry-run"' in output
    assert '"applied": false' in output
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("profile", ["native-mcp", "policy-mcp-consumption", "rest-consumption"])
def test_proposed_target_never_becomes_evidence_or_approval(profile):
    result = evaluate_workflow(
        MANIFEST, requires_mcp=profile != "rest-consumption", gateway_mode="new",
        selected_profile=profile, provisioning_target=proposed(),
    )
    assert result.provisional_profile == profile and result.profile is None
    assert result.provisioning_target == proposed()
    assert result.evidence is None and result.evidence_status == "missing"
    assert result.approval_status == "not-granted"
    assert "provision-preview" in result.allowed_actions
    assert "apply" not in result.allowed_actions and "deploy-preview" not in result.allowed_actions


def test_incomplete_target_and_private_provisioning_are_explicit():
    missing = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="new")
    assert "provisioning_target" in missing.missing_inputs
    assert "provision-preview" not in missing.allowed_actions
    with pytest.raises(ValueError):
        GatewayProvisioningTarget.model_validate(target().model_dump())
    private = evaluate_workflow(
        {**MANIFEST, "networkProfile": "hybrid"}, requires_mcp=True,
        gateway_mode="new", provisioning_target=proposed(),
    )
    assert private.status == "blocked"
    assert any("public networking only" in item for item in private.blockers)


def test_fixed_cost_preference_can_select_new_native_gateway():
    result = evaluate_workflow(
        MANIFEST, requires_mcp=True, gateway_mode="new",
        avoid_fixed_gateway_cost=False, provisioning_target=proposed(),
    )
    assert result.provisional_profile == "native-mcp"
    assert result.approval_status == "not-granted"


def test_preparation_precedes_pinned_provision_preview(mcp_workspace, monkeypatch):
    monkeypatch.setattr(gateway, "run_azure", lambda _: pytest.fail("Workflow contacted Azure"))
    spec(mcp_workspace)
    reader = WorkspaceReader(mcp_workspace)
    status = reader.workflow_status(
        "fixture", requires_mcp=True, gateway_mode="new", provisioning_target=proposed(),
    )
    assert status.next_command[3] == "prepare"
    assert "provision-preview" not in status.allowed_actions
    prepare(mcp_workspace, profile="policy-mcp-consumption")
    ready = reader.workflow_status("fixture")
    assert ready.current_step.stage == "provision-gateway"
    invocation = ready.next_invocation
    assert invocation.executable == str(Path(sys.executable).absolute())
    assert invocation.cwd == str(mcp_workspace)
    assert invocation.effect == "provisioning-preview"
    assert invocation.arguments[:4] == ["-I", "-m", "mcp_openapi_creator_kit.cli", "--workspace"]
    assert invocation.cli_arguments == ready.next_command[3:]
    assert invocation.cli_arguments[0] == "provision"
    assert invocation.cli_arguments[invocation.cli_arguments.index("--publisher-name") + 1] == "Fictional Publisher"
    assert "--yes" not in invocation.arguments and "--confirm-subscription" not in invocation.arguments
    assert ready.profile is None and ready.evidence is None
    assert reader.dashboard()[0]["workflow"][0] == ready.model_dump(mode="json", by_alias=True)
    existing = reader.workflow_status("fixture", gateway_mode="existing")
    assert existing.provisioning_target is None
    assert "gateway_evidence" in existing.missing_inputs
    assert not existing.next_command


def test_missing_new_target_does_not_construct_placeholder_command(mcp_workspace):
    prepare(mcp_workspace, profile="policy-mcp-consumption")
    status = WorkspaceReader(mcp_workspace).workflow_status(
        "fixture", requires_mcp=True, gateway_mode="new",
    )
    assert status.current_step.stage == "provision-gateway"
    assert status.next_invocation is None and not status.next_command


@pytest.mark.anyio
async def test_cli_and_mcp_share_proposed_context_without_azure(mcp_workspace, monkeypatch, capsys):
    monkeypatch.setattr(gateway, "run_azure", lambda _: pytest.fail("Offline command contacted Azure"))
    prepare(mcp_workspace, profile="policy-mcp-consumption")
    capsys.readouterr()
    flags = []
    for name, value in proposed().model_dump(exclude_none=True).items():
        flags.extend(["--" + name.replace("_", "-"), value])
    assert cli.main([
        "--workspace", str(mcp_workspace), "workflow-status", "--client", "fixture",
        "--consumer", "mcp", "--gateway-mode", "new", *flags,
    ]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    async with Client(create_server(mcp_workspace).mcp, mode="legacy") as client:
        result = await client.call_tool("workflow-status", {
            "client": "fixture", "requires_mcp": True, "gateway_mode": "new",
            "provisioning_target": proposed().model_dump(mode="json", by_alias=True),
        })
        assert not result.is_error
        assert result.structured_content == cli_result


@pytest.mark.parametrize("field,value", [
    ("publisher_name", " Publisher"), ("publisher_name", "Bad\nPublisher"),
    ("publisher_email", "not-an-email"), ("location", "West Europe"),
    ("tenant", "unverified-tenant"),
])
def test_proposed_context_validation(field, value):
    with pytest.raises(ValueError):
        GatewayProvisioningTarget.model_validate({**proposed().model_dump(), field: value})


def new_group():
    return GatewayProvisioningTarget.model_validate({
        **proposed().model_dump(), "resource_group_mode": "new",
        "resource_group_location": "northeurope",
    })


@pytest.mark.parametrize("overrides", [
    {"resource_group_mode": "new"},
    {"resource_group_mode": "create-if-missing"},
    {"resource_group_mode": "new", "resource_group_location": "North Europe"},
    {"resource_group_mode": "existing", "resource_group_location": "northeurope"},
])
def test_group_choice_is_explicit_and_cannot_relocate_existing_groups(overrides):
    with pytest.raises(ValueError):
        GatewayProvisioningTarget.model_validate({**proposed().model_dump(), **overrides})


def test_group_stage_follows_local_prepare_and_precedes_apim(mcp_workspace, monkeypatch):
    monkeypatch.setattr(gateway, "run_azure", lambda _: pytest.fail("Workflow contacted Azure"))
    spec(mcp_workspace)
    reader = WorkspaceReader(mcp_workspace)
    initial = reader.workflow_status(
        "fixture", requires_mcp=True, gateway_mode="new", provisioning_target=new_group(),
    )
    assert initial.next_invocation.cli_arguments[0] == "prepare"
    assert "provision-group-preview" not in initial.allowed_actions
    prepare(mcp_workspace, profile="policy-mcp-consumption")
    group = reader.workflow_status("fixture")
    assert group.current_step.stage == "provision-resource-group"
    assert "resource-group-provisioning" in group.current_step.instructions
    assert "separately" in group.current_step.consent
    invocation = group.next_invocation
    assert invocation.effect == "provisioning-preview"
    assert invocation.cli_arguments == [
        "provision-group", "--account", new_group().account,
        "--subscription", new_group().subscription, "--tenant", new_group().tenant,
        "--resource-group", new_group().resource_group, "--location", "northeurope",
    ]
    assert invocation.executable == str(Path(sys.executable).absolute())
    assert invocation.cwd == str(mcp_workspace)
    assert "--yes" not in invocation.arguments and "--confirm-subscription" not in invocation.arguments
    assert group.evidence is None and group.approval_status == "not-granted"
    assert "provision-group-preview" in group.allowed_actions
    assert "provision-preview" not in group.allowed_actions
    assert reader.dashboard()[0]["workflow"][0] == group.model_dump(mode="json", by_alias=True)

    # The caller records its new choice; only the later CLI preview observes Azure.
    next_stage = reader.workflow_status("fixture", provisioning_target=proposed())
    assert next_stage.current_step.stage == "provision-gateway"
    assert next_stage.next_invocation.cli_arguments[0] == "provision"
    assert next_stage.next_invocation.cli_arguments[
        next_stage.next_invocation.cli_arguments.index("--location") + 1] == "westeurope"
    assert next_stage.evidence is None and next_stage.profile is None
    assert next_stage.approval_status == "not-granted"


@pytest.mark.anyio
async def test_new_group_cli_mcp_and_dashboard_share_readonly_stage(mcp_workspace, monkeypatch, capsys):
    monkeypatch.setattr(gateway, "run_azure", lambda _: pytest.fail("Offline proposal contacted Azure"))
    prepare(mcp_workspace, profile="policy-mcp-consumption")
    capsys.readouterr()
    flags = []
    for name, value in new_group().model_dump(exclude_none=True).items():
        flags.extend(["--" + name.replace("_", "-"), value])
    assert cli.main([
        "--workspace", str(mcp_workspace), "workflow-status", "--client", "fixture",
        "--consumer", "mcp", "--gateway-mode", "new", *flags,
    ]) == 0
    expected = json.loads(capsys.readouterr().out)
    runtime = create_server(mcp_workspace)
    async with Client(runtime.mcp, mode="legacy") as client:
        result = await client.call_tool("workflow-status", {
            "client": "fixture", "requires_mcp": True, "gateway_mode": "new",
            "provisioning_target": new_group().model_dump(mode="json", by_alias=True),
        })
        assert not result.is_error
        assert result.structured_content == expected
        reference = await client.call_tool("kit-reference", {"name": "resource-group-provisioning"})
        assert not reference.is_error
        assert "provision-group" in str(reference.structured_content)
        dashboard, html = runtime.workspace.dashboard()
        assert dashboard["workflow"][0] == expected
        assert "Creazione del gruppo di risorse" in html
