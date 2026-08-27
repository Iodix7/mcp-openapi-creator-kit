from pathlib import Path

import pytest
from mcp import Client

from mcp_openapi_creator_kit.server import create_server

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.anyio
async def test_in_process_server_exposes_read_only_surfaces():
    runtime = create_server(REPO_ROOT)
    async with Client(runtime.mcp, mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"

        tools = (await client.list_tools()).tools
        by_name = {tool.name: tool for tool in tools}
        assert set(by_name) == {
            "workspace-status",
            "catalog-search",
            "recommend-profile",
            "policy-budget",
            "dashboard-get-url",
            "dashboard-refresh",
        }
        assert all(tool.annotations.read_only_hint for tool in tools)
        assert all(tool.annotations.destructive_hint is False for tool in tools)

        status = await client.call_tool("workspace-status", {})
        assert status.structured_content["valid"] is True
        assert status.structured_content["clients"][0]["id"] == "sample"

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
        assert recommendation.structured_content["profile"] == (
            "policy-mcp-consumption")
        budget = await client.call_tool("policy-budget", {
            "contract": "customer-care",
        })
        assert budget.structured_content["result"][0]["supported"] is True

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
async def test_dashboard_tools_return_stable_live_url():
    runtime = create_server(REPO_ROOT)
    async with Client(runtime.mcp, mode="legacy") as client:
        first = await client.call_tool("dashboard-get-url", {})
        second = await client.call_tool("dashboard-refresh", {})

        assert first.structured_content["url"] == second.structured_content["url"]
        assert second.structured_content["generation"] == (
            first.structured_content["generation"] + 1)
