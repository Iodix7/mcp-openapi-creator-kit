import pytest
from mcp import Client

from mcp_openapi_creator_kit.server import create_server

@pytest.mark.anyio
async def test_in_process_server_exposes_read_only_surfaces(mcp_workspace):
    runtime = create_server(mcp_workspace)
    async with Client(runtime.mcp, mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"

        tools = (await client.list_tools()).tools
        by_name = {tool.name: tool for tool in tools}
        assert set(by_name) == {
            "kit-info",
            "workflow-guide",
            "kit-reference",
            "workspace-status",
            "catalog-search",
            "recommend-profile",
            "policy-budget",
            "dashboard-get-url",
            "dashboard-refresh",
            "target-capabilities",
            "target-report",
            "workflow-status",
            "inspect-gateway",
            "scenario-contract",
        }
        assert all(tool.annotations.read_only_hint for tool in tools)
        assert all(tool.annotations.destructive_hint is False for tool in tools)

        status = await client.call_tool("workspace-status", {})
        assert status.structured_content["valid"] is True
        assert [item["id"] for item in status.structured_content["clients"]] == ["fixture"]

        search = await client.call_tool("catalog-search", {
            "query": "reschedule",
            "limit": 5,
        })
        assert search.structured_content["total"] >= 1
        recommendation = await client.call_tool("recommend-profile", {
            "requires_mcp": True,
            "external_backend": False,
            "private_network": False,
            "avoid_fixed_gateway_cost": True,
        })
        assert recommendation.structured_content["profile"] is None
        assert recommendation.structured_content["provisionalProfile"] == (
            "policy-mcp-consumption")
        assert "gateway_mode" in recommendation.structured_content["missingInputs"]
        budget = await client.call_tool("policy-budget", {
            "contract": "customer-care",
        })
        assert budget.structured_content["result"][0]["supported"] is True
        invalid_budget = await client.call_tool("policy-budget", {
            "contract": "apis/customer-care/openapi.yaml",
        })
        assert invalid_budget.is_error
        assert "catalog-search" in invalid_budget.content[0].text
        assert "not an OpenAPI file path" in invalid_budget.content[0].text

        resources = (await client.list_resources()).resources
        assert {str(item.uri) for item in resources} >= {
            "kit://constitution",
            "kit://catalog/index",
            "kit://workspace/status",
        }
        constitution = await client.read_resource("kit://constitution")
        assert "Non-negotiable rules" in constitution.contents[0].text

        prompts = (await client.list_prompts()).prompts
        assert {item.name for item in prompts} == {
            "discovery", "onboarding", "lifecycle"}
        prompt = await client.get_prompt("discovery", {"context": "Support agent"})
        assert "Support agent" in prompt.messages[0].content.text


@pytest.mark.anyio
async def test_dashboard_tools_return_stable_live_url(mcp_workspace):
    runtime = create_server(mcp_workspace)
    async with Client(runtime.mcp, mode="legacy") as client:
        first = await client.call_tool("dashboard-get-url", {})
        second = await client.call_tool("dashboard-refresh", {})

        assert first.structured_content["url"] == second.structured_content["url"]
        assert second.structured_content["generation"] == (
            first.structured_content["generation"] + 1)


@pytest.mark.anyio
async def test_catalog_browse_default_empty_and_bounded_errors(mcp_workspace):
    async with Client(create_server(mcp_workspace).mcp, mode="legacy") as client:
        for arguments in ({}, {"query": ""}, {"query": "   ", "limit": 1}):
            result = await client.call_tool("catalog-search", arguments)
            assert not result.is_error
            assert result.structured_content["total"] > 0
            assert len(result.structured_content["matches"]) <= arguments.get("limit", 10)
        result = await client.call_tool("catalog-search", {"limit": 51})
        assert result.is_error
        assert "between 1 and 50" in result.content[0].text


@pytest.mark.anyio
async def test_declared_tier_is_not_gateway_evidence(mcp_workspace):
    async with Client(create_server(mcp_workspace).mcp, mode="legacy") as client:
        result = await client.call_tool("recommend-profile", {"existing_tier": "BasicV2"})
        assert result.structured_content["profile"] is None
        assert result.structured_content["evidenceStatus"] == "missing"
        assert "gateway_evidence" in result.structured_content["missingInputs"]
