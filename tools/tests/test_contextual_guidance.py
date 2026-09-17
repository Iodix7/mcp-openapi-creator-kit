"""Contextual guidance is an exact projection of trusted, versioned procedures."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import get_args

import pytest
from mcp import Client

from mcp_openapi_creator_kit import guidance
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.server import create_server
from mcp_openapi_creator_kit.workflow import GuidanceStage, evaluate_workflow
from mcp_openapi_creator_kit.workspace import WorkspaceReader
from test_workflow_progress import prepare, reader, spec
from test_workflow import target


@pytest.mark.parametrize("stage", get_args(GuidanceStage))
def test_every_stage_is_extracted_exactly_from_one_full_procedure(stage):
    fields, source = guidance.step_document(stage)
    workflow = guidance.STEP_SOURCES[stage]
    text = guidance.workflow_guide(workflow)["procedure"]["content"]
    excerpt = text.split(f"<!-- kit-step:{stage} -->", 1)[1].split(f"<!-- /kit-step:{stage} -->", 1)[0].strip()
    assert set(fields) == {"instructions", "consent", "completion"}
    assert all(content in excerpt for content in fields.values())
    assert source.document_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert source.excerpt_sha256 == hashlib.sha256(excerpt.encode()).hexdigest()
    assert source.resource_uri == f"kit://skills/{workflow}"
    assert len(excerpt) < 4500


@pytest.mark.parametrize("broken", [
    "", "<!-- kit-step:prepare --><!-- /kit-step:prepare -->",
    "<!-- /kit-step:prepare --><!-- kit-step:prepare -->",
    "<!-- kit-step:prepare --><!-- kit-step:prepare --><!-- /kit-step:prepare -->",
    "<!-- kit-step:prepare -->\n### Instructions\nOnly instructions.\n<!-- /kit-step:prepare -->",
    "<!-- kit-step:prepare -->\n### Instructions\n\n### Consent\nNo.\n### Completion\nNo.\n<!-- /kit-step:prepare -->",
])
def test_invalid_packaged_procedure_fails_actionably(monkeypatch, broken):
    monkeypatch.setattr(guidance, "asset_text", lambda _: broken)
    with pytest.raises(ValueError, match="packaged procedure"):
        guidance.step_document("prepare")


@pytest.mark.parametrize("preferences,stage", [
    ({}, "collect-context"),
    ({"requires_mcp": True}, "collect-context"),
    ({"requires_mcp": False, "gateway_mode": "existing"}, "inspect-gateway"),
    ({"requires_mcp": True, "gateway_mode": "new"}, "configure-client"),
    ({"requires_mcp": True, "gateway_mode": "existing", "selected_profile": "rest-consumption"}, "resolve-blockers"),
])
def test_planned_client_gets_immediate_bounded_guidance_without_writes(tmp_path, preferences, stage):
    value = WorkspaceReader(tmp_path).workflow_status("acme", **preferences)
    assert value.next_stage == value.current_step.stage == stage
    assert value.current_step.instructions and value.current_step.consent and value.current_step.completion
    assert not value.next_command
    assert value.approval_status == "not-granted"
    assert not list(tmp_path.iterdir())


def test_current_step_advances_and_recovers_with_real_local_state(mcp_workspace, monkeypatch):
    workspace = reader(mcp_workspace, monkeypatch)
    value = workspace.workflow_status("fixture")
    assert value.current_step.stage == "define-scenario"
    spec(mcp_workspace)
    value = workspace.workflow_status("fixture")
    assert value.current_step.stage == "prepare" and "prepare" in value.next_command
    assert "Missing input: " not in "\n".join(value.current_step.why)
    prepare(mcp_workspace)
    value = workspace.workflow_status("fixture")
    assert value.current_step.stage == "preview" and "deploy" in value.next_command
    assert "--yes" not in value.next_command and "--preview" not in value.next_command
    (mcp_workspace / "skills" / "onboarding.md").write_text("EVIL_GUIDANCE", encoding="utf-8")
    assert workspace.workflow_status("fixture").current_step == value.current_step
    evidence_id = value.evidence.evidence_id
    workspace.gateway_evidence._records[evidence_id] = value.evidence.model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    expired = workspace.workflow_status("fixture")
    assert expired.current_step.stage == "inspect-gateway" and not expired.next_command
    assert "Missing input: gateway_evidence" in expired.current_step.why
    assert expired.approval_status == "not-granted"
    path = mcp_workspace / "docs" / "fixture" / "spec.md"
    path.write_text(path.read_text("utf-8") + "\nChanged story.\n", encoding="utf-8")
    stale = workspace.workflow_status("fixture")
    assert stale.current_step.stage == "inspect-gateway" and stale.profile is None
    fresh = workspace.gateway_evidence.inspect(target())
    recover = workspace.workflow_status("fixture", evidence_id=fresh.evidence_id)
    assert recover.current_step.stage == "prepare"
    assert any("stale" in reason for reason in recover.current_step.why)


def test_new_gateway_does_not_receive_a_selective_deploy_command(mcp_workspace):
    prepare(mcp_workspace, profile="policy-mcp-consumption")
    value = WorkspaceReader(mcp_workspace).workflow_status("fixture", requires_mcp=True, gateway_mode="new")
    assert value.current_step.stage == "provision-gateway" and not value.next_command
    assert "mcp-kit provision" in value.current_step.instructions
    assert "provisioning_target" in value.current_step.instructions
    assert "provisioning_target" in value.missing_inputs
    assert value.profile is None and value.approval_status == "not-granted"


def test_guidance_does_not_change_core_decisions_or_observations():
    value = evaluate_workflow(requires_mcp=True, gateway_mode="existing", planned_client="acme")
    enriched = guidance.with_current_step(value)
    assert enriched.model_dump(exclude={"current_step"}) == value.model_dump(exclude={"current_step"})
    assert enriched.current_step.stage == value.next_stage


@pytest.mark.anyio
async def test_mcp_resource_cli_dashboard_and_full_prompt_share_sources(mcp_workspace, capsys):
    runtime = create_server(mcp_workspace)
    async with Client(runtime.mcp, mode="legacy") as client:
        result = await client.call_tool("workflow-status", {"client": "fixture"})
        assert not result.is_error
        value = result.structured_content
        step = value["currentStep"]
        full = await client.read_resource(step["source"]["resourceUri"])
        assert step["instructions"] in full.contents[0].text
        assert step["consent"] in full.contents[0].text
        prompt = await client.get_prompt("discovery")
        assert step["instructions"] in prompt.messages[0].content.text
        resource = await client.read_resource("kit://workflow/status")
        assert json.loads(resource.contents[0].text)[0] == value
        index, html = runtime.workspace.dashboard()
        assert index["workflow"][0] == value and "How to confirm completion" in html
        main(["--workspace", str(mcp_workspace), "workflow-status", "--client", "fixture"])
        assert json.loads(capsys.readouterr().out) == value
        alias = await client.call_tool("recommend-profile", {})
        assert alias.structured_content["currentStep"]["stage"] == "collect-context"
