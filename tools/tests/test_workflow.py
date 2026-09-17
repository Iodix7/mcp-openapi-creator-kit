"""Domain decisions and Azure command-boundary observations, entirely offline."""
import copy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import yaml

import pytest
from mcp import Client
from mcp.types import ElicitResult

from mcp_openapi_creator_kit import gateway
from mcp_openapi_creator_kit.cli import main as cli
from mcp_openapi_creator_kit.server import create_server
from mcp_openapi_creator_kit.workflow import (
    GatewayObservation, GatewayTarget, diagnostic_violations, evaluate_workflow,
)
from offline_scenarios import BASE, SUB, TENANT, SyntheticAzure

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
MANIFEST = {"client": "fixture", "apis": [{"name": "care", "backend": {"mode": "mock"}}]}


def target():
    return GatewayTarget(account="operator@example.test", subscription=SUB, tenant=TENANT,
                         resource_group="fixture", apim_name="fixture-apim")


async def approve_inspection(_context, params):
    assert target().resource_id in params.message and "Does NOT authorize" in params.message
    return ElicitResult(action="accept", content={"inspect": True})


def observation(tmp_path, tier="BasicV2", *, changes=None, diagnostics=None):
    apim = SyntheticAzure(tmp_path).gateway
    apim["sku"]["name"] = tier
    if changes:
        apim.update(changes)
    return GatewayObservation.issue(str(tmp_path.resolve()), target(), apim, diagnostics or [], now=NOW)


@pytest.mark.parametrize("mode", ["unknown", "new", "existing"])
@pytest.mark.parametrize("requires_mcp", [None, True, False])
def test_no_verified_profile_without_observation(mode, requires_mcp):
    result = evaluate_workflow(MANIFEST, requires_mcp=requires_mcp, gateway_mode=mode)
    assert result.profile is None and result.evidence_status == "missing"
    assert "deploy-preview" not in result.allowed_actions
    assert "build-offline" in result.allowed_actions
    assert result.approval_status == "not-granted"
    if requires_mcp is None:
        assert "requires_mcp" in result.missing_inputs
    if mode == "existing":
        assert "gateway_evidence" in result.missing_inputs


@pytest.mark.parametrize("tier,consumer,expected", [
    ("BasicV2", True, "native-mcp"), ("BasicV2", False, "native-mcp"),
    ("Consumption", True, "policy-mcp-consumption"), ("Consumption", False, "rest-consumption"),
    ("Premium", True, "native-mcp"),
])
def test_profile_is_derived_from_observed_tier(tmp_path, tier, consumer, expected):
    result = evaluate_workflow(MANIFEST, requires_mcp=consumer, gateway_mode="existing",
                               observation=observation(tmp_path, tier), now=NOW)
    assert result.profile == expected and result.status == "ready-for-preview"
    assert "deploy-preview" in result.allowed_actions
    assert "apply" not in result.allowed_actions and result.approval_status == "not-granted"
    assert result.checks_pending


def test_wrong_profile_unknown_tier_and_new_gateway_conflict(tmp_path):
    observed = observation(tmp_path)
    result = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="existing",
                               selected_profile="policy-mcp-consumption", observation=observed, now=NOW)
    assert result.status == "blocked" and result.profile is None
    assert any("native-mcp" in error for error in result.blockers)
    assert "selected_profile='native-mcp'" in result.next_action
    result = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="existing",
                               observation=observation(tmp_path, "UnknownTier"), now=NOW)
    assert result.status == "blocked"
    result = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="new", observation=observed, now=NOW)
    assert result.status == "blocked"


@pytest.mark.parametrize("offset", [-1, 300, 301])
def test_expired_or_future_evidence_cannot_enable_preview(tmp_path, offset):
    result = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="existing",
                               observation=observation(tmp_path), now=NOW + timedelta(seconds=offset))
    assert result.evidence_status == "stale" and result.profile is None
    assert "gateway_evidence" in result.missing_inputs
    assert "deploy-preview" not in result.allowed_actions


@pytest.mark.parametrize("kind", ["external", "hybrid", "private-request", "ai-gateway", "unsupported-backend"])
def test_declared_preferences_cannot_weaken_manifest_or_network(tmp_path, kind):
    manifest = copy.deepcopy(MANIFEST)
    if kind == "external":
        manifest["apis"][0]["backend"]["mode"] = "external"
    elif kind == "hybrid":
        manifest["networkProfile"] = "hybrid"
    elif kind == "private-request":
        manifest["networkProfile"] = "public"
    elif kind == "ai-gateway":
        manifest["targets"] = {"gateway": "ai-gateway-preview"}
    else:
        manifest["apis"][0]["backend"]["mode"] = "hosted"
    result = evaluate_workflow(manifest, requires_mcp=True, gateway_mode="existing",
                               external_backend=False, private_network=kind == "private-request",
                               observation=observation(tmp_path, "Consumption"), now=NOW)
    assert result.status == "blocked" and result.profile is None


@pytest.mark.parametrize("changes", [
    {"identity": {"type": "UserAssigned"}},
    {"properties": {"provisioningState": "Updating"}},
    {"properties": {"provisioningState": "Succeeded", "publicNetworkAccess": "Disabled"}},
])
def test_unsupported_gateway_capabilities_block(tmp_path, changes):
    result = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="existing",
                               observation=observation(tmp_path, changes=changes), now=NOW)
    assert result.status == "blocked"


@pytest.mark.parametrize("vnet,access,expected", [
    ("External", "Enabled", "ready-for-preview"),
    ("Internal", "Enabled", "blocked"),
    ("External", "Disabled", "blocked"),
    ("Internal", "Disabled", "blocked"),
])
def test_hybrid_requires_public_ingress(tmp_path, vnet, access, expected):
    manifest = {**MANIFEST, "networkProfile": "hybrid"}
    result = evaluate_workflow(
        manifest, requires_mcp=True, gateway_mode="existing", now=NOW,
        observation=observation(tmp_path, changes={"properties": {
            "provisioningState": "Succeeded", "virtualNetworkType": vnet,
            "publicNetworkAccess": access}}))
    assert result.status == expected


@pytest.mark.parametrize("diagnostic", [
    {}, {"properties": {"frontend": "malformed"}},
    {"properties": {"frontend": False}},
    {"properties": {"backend": {"response": {"body": []}}}},
    {"properties": {"frontend": {"response": {"body": {"bytes": True}}}}},
    {"properties": {"backend": {"response": {"body": {"bytes": 1}}}}},
])
def test_diagnostics_shared_fail_closed(tmp_path, diagnostic):
    assert diagnostic_violations([diagnostic])
    result = evaluate_workflow(MANIFEST, requires_mcp=True, gateway_mode="existing",
                               observation=observation(tmp_path, diagnostics=[diagnostic]), now=NOW)
    assert result.status == "blocked"


@pytest.mark.parametrize("field", ["user", "id", "tenantId"])
def test_inspection_checks_account_before_resources(tmp_path, field):
    azure = SyntheticAzure(tmp_path)
    azure.account[field] = "wrong"
    with pytest.raises(ValueError, match="No resource was read"):
        gateway.inspect_gateway(tmp_path, target(), runner=azure.run)
    assert len(azure.calls) == 1


def test_inspection_uses_bound_gets_filters_metadata_and_never_writes(tmp_path):
    azure = SyntheticAzure(tmp_path)
    azure.gateway["sensitiveExtra"] = "do-not-return"
    value = gateway.inspect_gateway(tmp_path, target(), runner=azure.run)
    assert value.facts.tier == "BasicV2" and value.target == target()
    assert value.expires_at > value.observed_at
    assert "do-not-return" not in value.model_dump_json()
    assert len(azure.calls) == 3
    assert all(args[:4] == ["az", "rest", "--method", "GET"] for args in azure.calls[1:])
    assert list(tmp_path.iterdir()) == []


def test_status_rereads_manifest_and_expiry_without_cloud(mcp_workspace, monkeypatch):
    azure = SyntheticAzure(mcp_workspace)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    workspace = create_server(mcp_workspace).workspace
    inspected = workspace.gateway_evidence.inspect(target())
    status = workspace.workflow_status("fixture", requires_mcp=True, gateway_mode="existing",
                                      evidence_id=inspected.evidence_id)
    assert status.status == "ready-for-preparation"
    path = mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml"
    manifest = yaml.safe_load(path.read_text("utf-8"))
    manifest["networkProfile"] = "isolated"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    assert workspace.workflow_status("fixture").status == "blocked"
    workspace.gateway_evidence._records[inspected.evidence_id] = inspected.model_copy(update={"expires_at": NOW})
    expired = workspace.workflow_status("fixture")
    assert expired.evidence_status == "stale" and expired.profile is None
    assert len(azure.calls) == 3


def test_evicted_cached_evidence_requests_reinspection_without_breaking_dashboard(mcp_workspace, monkeypatch):
    azure = SyntheticAzure(mcp_workspace)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    workspace = create_server(mcp_workspace).workspace
    first = workspace.gateway_evidence.inspect(target())
    workspace.workflow_status("fixture", requires_mcp=True, gateway_mode="existing", evidence_id=first.evidence_id)
    for _ in range(64):
        workspace.gateway_evidence.inspect(target())
    calls = len(azure.calls)
    snapshot = workspace.workflow_snapshot()[0]
    assert snapshot["evidenceStatus"] == "missing" and snapshot["profile"] is None
    assert "gateway_evidence" in snapshot["missingInputs"]
    index, _ = workspace.dashboard()
    assert index["workflow"][0] == snapshot
    assert len(azure.calls) == calls
    with pytest.raises(ValueError, match="Unknown gateway evidence"):
        workspace.workflow_status("fixture", evidence_id=first.evidence_id)


@pytest.mark.parametrize("change", ["wrong-id", "missing-id", "malformed", "pagination"])
def test_incomplete_or_wrong_resource_never_issues_evidence(tmp_path, change):
    azure = SyntheticAzure(tmp_path)
    if change == "wrong-id":
        azure.gateway["id"] = BASE + "-other"
    elif change == "missing-id":
        del azure.gateway["id"]
    elif change == "malformed":
        azure.gateway["properties"] = []
    def run(args):
        if change == "pagination" and "/diagnostics?" in " ".join(args):
            return json.dumps({"value": [], "nextLink": "https://elsewhere.example/diagnostics"})
        return azure.run(args)
    with pytest.raises((ValueError, RuntimeError)):
        gateway.inspect_gateway(tmp_path, target(), runner=run)


def test_cloud_errors_are_actionable_without_raw_output(monkeypatch):
    monkeypatch.setattr(gateway.shutil, "which", lambda _: "az")
    monkeypatch.setattr(gateway.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 1, "SECRET", "SECRET"))
    with pytest.raises(RuntimeError, match="Raw Azure output is suppressed") as failure:
        gateway.run_azure(["az", "account", "show"])
    assert "SECRET" not in str(failure.value)
    with pytest.raises(ValueError, match="only account show"):
        gateway.run_azure(["az", "rest", "--method", "DELETE"])


@pytest.mark.anyio
async def test_mcp_evidence_state_and_dashboard_share_core(mcp_workspace, monkeypatch):
    azure = SyntheticAzure(mcp_workspace)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    runtime = create_server(mcp_workspace)
    async with Client(runtime.mcp, mode="legacy", elicitation_callback=approve_inspection) as client:
        before = await client.call_tool("workflow-status", {
            "client": "fixture", "requires_mcp": True, "gateway_mode": "existing"})
        assert before.structured_content["profile"] is None
        assert azure.calls == []
        inspected = await client.call_tool("inspect-gateway", {"target": target().model_dump(by_alias=True)})
        assert not inspected.is_error
        evidence_id = inspected.structured_content["evidenceId"]
        result = await client.call_tool("workflow-status", {"client": "fixture", "evidence_id": evidence_id})
        assert result.structured_content["profile"] == "native-mcp"
        assert result.structured_content["approvalStatus"] == "not-granted"
        resource = await client.read_resource("kit://workflow/status")
        assert json.loads(resource.contents[0].text)[0] == result.structured_content
        index, _ = runtime.workspace.dashboard()
        assert index["workflow"][0] == result.structured_content
        wrong = await client.call_tool("workflow-status", {
            "client": "fixture", "selected_profile": "policy-mcp-consumption"})
        assert wrong.structured_content["status"] == "blocked"
        assert len(azure.calls) == 3  # Status/resource/dashboard never refresh Azure implicitly.
        forged = await client.call_tool("workflow-status", {"client": "fixture", "evidence_id": "forged"})
        assert forged.is_error and "inspect-gateway" in forged.content[0].text
    other = create_server(mcp_workspace)
    with pytest.raises(ValueError, match="Unknown gateway evidence"):
        other.workspace.gateway_evidence.get(evidence_id)


def test_cli_offline_status_and_explicit_read_inspection(mcp_workspace, monkeypatch, capsys):
    azure = SyntheticAzure(mcp_workspace)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: target().resource_id)
    prefix = ["--workspace", str(mcp_workspace)]
    cli([*prefix, "workflow-status", "--client", "fixture", "--consumer", "mcp", "--gateway-mode", "existing"])
    offline = json.loads(capsys.readouterr().out)
    assert offline["profile"] is None and azure.calls == []
    cli([*prefix, "inspect-gateway", "--client", "fixture", "--consumer", "mcp",
         "--account", target().account, "--subscription", SUB, "--tenant", TENANT,
         "--resource-group", "fixture", "--apim-name", "fixture-apim"])
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready-for-preparation" and result["profile"] == "native-mcp"
    # Files or JSON emitted by CLI cannot bootstrap a verified MCP observation.
    (mcp_workspace / "gateway-evidence.json").write_text(json.dumps(result), encoding="utf-8")
    assert create_server(mcp_workspace).workspace.workflow_status("fixture").profile is None
    assert len(azure.calls) == 3


@pytest.mark.anyio
async def test_planned_client_has_structured_missing_input_and_recovers(tmp_path, monkeypatch):
    azure = SyntheticAzure(tmp_path)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    runtime = create_server(tmp_path)
    async with Client(runtime.mcp, mode="legacy", elicitation_callback=approve_inspection) as client:
        result = await client.call_tool("workflow-status", {
            "client": "acme", "requires_mcp": True, "gateway_mode": "existing",
            "selected_profile": "policy-mcp-consumption"})
        assert not result.is_error
        assert result.structured_content["client"] == "acme"
        assert result.structured_content["missingInputs"] == ["gateway_evidence", "client_manifest"]
        assert list(tmp_path.iterdir()) == [] and azure.calls == []
        observed = await client.call_tool("inspect-gateway", {"target": target().model_dump(by_alias=True)})
        rejected = await client.call_tool("workflow-status", {
            "client": "acme", "evidence_id": observed.structured_content["evidenceId"]})
        assert rejected.structured_content["status"] == "blocked"
        assert "selected_profile='native-mcp'" in rejected.structured_content["nextAction"]
        recovered = await client.call_tool("workflow-status", {
            "client": "acme", "selected_profile": "native-mcp"})
        assert recovered.structured_content["profile"] == "native-mcp"
        assert recovered.structured_content["missingInputs"] == ["client_manifest"]
        assert "deploy-preview" not in recovered.structured_content["allowedActions"]
        snapshot = runtime.workspace.workflow_snapshot()
        assert snapshot == [recovered.structured_content]
        assert list(tmp_path.iterdir()) == []
