"""Consumer details are deterministic offline projections, not cloud evidence."""
import json

import pytest
import yaml
from mcp import Client

from mcp_openapi_creator_kit import handoff, policy
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.server import create_server
from mcp_openapi_creator_kit.workspace import WorkspaceReader


@pytest.mark.parametrize("profile,suffix,transport", [
    ("policy-mcp-consumption", "agent-policy-mcp/mcp", "MCP Streamable HTTP"),
    ("native-mcp", "agent-mcp/mcp", "MCP Streamable HTTP"),
    ("rest-consumption", "agent", "REST/OpenAPI"),
])
@pytest.mark.parametrize("origin", ["https://gateway.example.test", "https://gateway.example.test/"])
def test_exact_profile_endpoint_and_no_verification_claim(mcp_workspace, profile, suffix, transport, origin):
    result = handoff.consumer_handoff(mcp_workspace, "fixture", profile, origin)
    assert result["endpoints"][0]["url"] == f"https://gateway.example.test/fixture/{suffix}"
    assert result["transport"] == transport
    assert result["status"] == "derived-not-verified"
    assert result["authentication"]["credentialsIncluded"] is False
    assert not (mcp_workspace / "clients" / "fixture" / "generated").exists()
    assert result["operations"][0]["persistence"] == "none"
    assert "final answer" in result["responseRules"][0]
    assert "deployment and connection have not been verified" in result["responseRules"][0]


@pytest.mark.parametrize("value", [
    "http://gateway.test", "https://key@gateway.test", "https://gateway.test/path/mcp",
    "https://gateway.test?key=SECRET", "https://gateway.test#SECRET", "https://gateway.test//",
])
def test_origin_rejected_without_echoing_credentials(mcp_workspace, value):
    with pytest.raises(ValueError) as error:
        handoff.consumer_handoff(mcp_workspace, "fixture", "native-mcp", value)
    assert "SECRET" not in str(error.value)


def test_relative_candidates_and_exposure_modes(mcp_workspace):
    path = mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["mcpExposure"]["mode"] = "both"
    path.write_text(yaml.safe_dump(manifest))
    result = handoff.consumer_handoff(mcp_workspace, "fixture", "native-mcp")
    assert [s["endpointPath"] for s in result["endpoints"]] == [
        "fixture/customer-care-mcp/mcp", "fixture/agent-mcp/mcp"]
    assert all(s["url"] is None for s in result["endpoints"])
    assert result["gatewayOrigin"] is None


def test_real_policy_shards_have_complete_paths_and_full_tool_coverage(mcp_workspace, monkeypatch):
    path = mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["apis"][0]["mcpTools"] = ["get-customer-context", "get-open-cases", "create-reschedule-request"]
    path.write_text(yaml.safe_dump(manifest))
    _, groups = policy.load_client(mcp_workspace, path.parent)
    tools = groups["customer-care"]
    limit = max(policy.policy_size(policy.build_policy("fixture-agent-policy-mcp", [tool])) for tool in tools)
    original = policy.build_client_plan
    monkeypatch.setattr(handoff, "build_client_plan", lambda root, client: original(root, client, limit))
    result = handoff.consumer_handoff(mcp_workspace, "fixture", "policy-mcp-consumption", "https://gateway.test")
    assert len(result["endpoints"]) > 1
    assert all(s["endpointPath"] == s["basePath"] + "/mcp" for s in result["endpoints"])
    assert {name for s in result["endpoints"] for name in s["tools"]} == set(manifest["apis"][0]["mcpTools"])
    write = next(o for o in result["operations"] if o["tool"] == "create-reschedule-request")
    assert write["idempotencyKey"] == "gateway-generated-per-call"
    assert write["callerKeyReuse"] == "not-supported"
    assert result["runtimeSummary"].startswith("NO:")
    assert "neither persists nor deduplicates" in result["runtimeSummary"]
    assert "202" in write["restResponseStatuses"]


def test_external_backend_is_not_claimed_persistent_or_consumption_compatible(mcp_workspace):
    path = mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["apis"][0]["backend"] = {"mode": "external", "url": "https://backend.test"}
    path.write_text(yaml.safe_dump(manifest))
    native = handoff.consumer_handoff(mcp_workspace, "fixture", "native-mcp")
    assert native["operations"][0]["persistence"] == "not-verified"
    assert native["operations"][0]["callerKeyReuse"] == "not-verified"
    assert "remain unverified" in native["runtimeSummary"]
    assert "generates a new Idempotency-Key" not in native["runtimeSummary"]
    for profile in ("rest-consumption", "policy-mcp-consumption"):
        with pytest.raises(ValueError, match="mock-only"):
            handoff.consumer_handoff(mcp_workspace, "fixture", profile)


@pytest.mark.anyio
async def test_cli_mcp_and_dashboard_share_handoff(mcp_workspace, capsys):
    runtime = create_server(mcp_workspace)
    expected = handoff.consumer_handoff(mcp_workspace, "fixture", "policy-mcp-consumption", "https://gateway.test")
    assert main(["--workspace", str(mcp_workspace), "consumer-handoff", "fixture",
                 "--profile", "policy-mcp-consumption", "--gateway-url", "https://gateway.test"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    async with Client(runtime.mcp, mode="legacy") as client:
        result = await client.call_tool("consumer-handoff", {
            "client": "fixture", "profile": "policy-mcp-consumption", "gateway_url": "https://gateway.test"})
        assert not result.is_error and result.structured_content == expected
        tools = await client.list_tools()
        tool = next(t for t in tools.tools if t.name == "consumer-handoff")
        assert tool.annotations.read_only_hint is True
    index, html = runtime.workspace.dashboard()
    assert index["clients"][0]["consumerHandoff"] == expected
    assert "fixture/agent-policy-mcp/mcp" in html
    assert "derived-not-verified" in html
    assert "consumerHandoffCandidates" in WorkspaceReader(mcp_workspace).catalog()["clients"][0]


def test_oversized_policy_is_reported_not_truncated(mcp_workspace, monkeypatch):
    def too_large(*_):
        raise policy.PolicyBuildError("single tool 17000 bytes exceeds 16384")
    monkeypatch.setattr(handoff, "build_client_plan", too_large)
    with pytest.raises(ValueError, match="17000"):
        handoff.consumer_handoff(mcp_workspace, "fixture", "policy-mcp-consumption")
    blocked = handoff.handoff_candidates(mcp_workspace, "fixture")[1]
    assert blocked["status"] == "blocked" and "17000" in blocked["reason"]


def test_invalid_manifest_is_blocked_without_exiting_companion(mcp_workspace):
    path = mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["mcpExposure"]["mode"] = "typo"
    path.write_text(yaml.safe_dump(manifest))
    for profile in handoff.PROFILES:
        with pytest.raises(ValueError, match="Invalid manifest"):
            handoff.consumer_handoff(mcp_workspace, "fixture", profile)
    assert all(item["status"] == "blocked" for item in handoff.handoff_candidates(mcp_workspace, "fixture"))


def test_missing_contract_keeps_catalog_available_with_explicit_blocker(mcp_workspace):
    (mcp_workspace / "apis" / "customer-care" / "openapi.yaml").unlink()
    reports = WorkspaceReader(mcp_workspace).catalog()["clients"][0]["consumerHandoffCandidates"]
    assert all(item["status"] == "blocked" for item in reports)
    assert all("Missing or unreadable" in item["reason"] for item in reports)
