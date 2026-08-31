"""Read-only MCP server for exploring and planning from a kit workspace."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .dashboard import DashboardHost
from .workspace import WorkspaceReader

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class ClientSummary(BaseModel):
    id: str | None
    display_name: str | None = Field(alias="displayName")
    apis: list[str | None]


class WorkspaceStatus(BaseModel):
    valid: bool
    root: str
    clients: list[ClientSummary]
    contracts: list[str]
    catalog_generated: bool = Field(alias="catalogGenerated")
    missing: list[str]


class CatalogMatch(BaseModel):
    id: str
    title: str
    description: str
    operations: list[str]
    profiles: list[str]


class CatalogSearchResult(BaseModel):
    query: str
    total: int
    matches: list[CatalogMatch]


class ProfileRecommendation(BaseModel):
    profile: Literal["native-mcp", "policy-mcp-consumption", "rest-consumption"]
    reason: str
    constraints: list[str]


class PolicyServerBudget(BaseModel):
    tools: list[str]
    size_bytes: int = Field(alias="sizeBytes")
    limit_bytes: int = Field(alias="limitBytes")
    remaining_bytes: int = Field(alias="remainingBytes")
    usage_percent: float = Field(alias="usagePercent")


class PolicyBudgetResult(BaseModel):
    contract: str
    supported: bool
    reason: str | None = None
    servers: list[PolicyServerBudget]


class DashboardResult(BaseModel):
    url: str
    generation: int
    scenarios: int


@dataclass
class LocalServer:
    mcp: MCPServer
    workspace: WorkspaceReader
    dashboard: DashboardHost


def _localized(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("en") or value.get("source") or value.get("it") or "")
    return ""


def create_server(workspace_root: Path) -> LocalServer:
    workspace = WorkspaceReader(workspace_root)
    dashboard = DashboardHost()

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        try:
            yield {}
        finally:
            dashboard.close()

    server = MCPServer(
        "mcp-openapi-creator",
        title="MCP OpenAPI Creator",
        description="Read-only guidance and catalog access for MCP OpenAPI Creator Kit.",
        version="1.1.1",
        lifespan=lifespan,
    )

    @server.resource(
        "kit://constitution",
        name="constitution",
        description="Repository constitution and non-negotiable safety rules.",
        mime_type="text/markdown",
    )
    def constitution() -> str:
        return workspace.read_text("AGENTS.md")

    @server.resource(
        "kit://skills/discovery",
        name="discovery-skill",
        description="Procedure for discovering an agent API scenario.",
        mime_type="text/markdown",
    )
    def discovery_skill() -> str:
        return workspace.read_text("skills", "discovery.md")

    @server.resource(
        "kit://skills/onboarding",
        name="onboarding-skill",
        description="Procedure for configuring and deploying a client.",
        mime_type="text/markdown",
    )
    def onboarding_skill() -> str:
        return workspace.read_text("skills", "onboarding.md")

    @server.resource(
        "kit://skills/lifecycle",
        name="lifecycle-skill",
        description="Procedure for safely changing deployed clients.",
        mime_type="text/markdown",
    )
    def lifecycle_skill() -> str:
        return workspace.read_text("skills", "lifecycle.md")

    @server.resource(
        "kit://catalog/index",
        name="capability-catalog",
        description="Live capability catalog built in memory from OpenAPI and manifests.",
        mime_type="application/json",
    )
    def catalog_index() -> str:
        return workspace.catalog_json()

    @server.resource(
        "kit://workspace/status",
        name="workspace-status",
        description="Read-only summary of the selected kit workspace.",
        mime_type="application/json",
    )
    def workspace_status_resource() -> str:
        return json.dumps(workspace.status(), indent=2, ensure_ascii=False) + "\n"

    def skill_prompt(skill: str, context: str) -> str:
        guidance = workspace.read_text("skills", f"{skill}.md")
        context_text = context.strip() or "No additional context was supplied."
        return (
            f"Follow the repository procedure below. Stay read-only and propose the "
            f"next safe steps for this workspace.\n\nContext:\n{context_text}\n\n"
            f"{guidance}"
        )

    @server.prompt(name="discovery", description="Plan scenario discovery using the repository procedure.")
    def discovery_prompt(context: str = "") -> str:
        return skill_prompt("discovery", context)

    @server.prompt(name="onboarding", description="Plan client onboarding using the repository procedure.")
    def onboarding_prompt(context: str = "") -> str:
        return skill_prompt("onboarding", context)

    @server.prompt(name="lifecycle", description="Plan a safe lifecycle change using the repository procedure.")
    def lifecycle_prompt(context: str = "") -> str:
        return skill_prompt("lifecycle", context)

    @server.tool(
        name="workspace-status",
        description="Inspect clients, contracts, generated catalog state, and missing kit files.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def workspace_status() -> WorkspaceStatus:
        return WorkspaceStatus.model_validate(workspace.status())

    @server.tool(
        name="catalog-search",
        description="Search the live capability catalog without changing workspace files.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def catalog_search(query: str, limit: int = 10) -> CatalogSearchResult:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        needle = query.casefold()
        matches = []
        for scenario in workspace.catalog()["scenarios"]:
            operations = scenario.get("operations", [])
            haystack = " ".join([
                scenario.get("id", ""),
                _localized(scenario.get("title")),
                scenario.get("description", ""),
                scenario.get("domain", ""),
                *scenario.get("tags", []),
                *[
                    " ".join([
                        item.get("operationId", ""),
                        item.get("summary", ""),
                        item.get("description", ""),
                        item.get("path", ""),
                    ])
                    for item in operations
                ],
            ]).casefold()
            if needle not in haystack:
                continue
            profiles = [
                name for name, value in scenario.get("compatibility", {}).items()
                if value.get("supported")
            ]
            matches.append(CatalogMatch(
                id=scenario["id"],
                title=_localized(scenario.get("title")),
                description=scenario.get("description", ""),
                operations=[item.get("operationId", "") for item in operations],
                profiles=profiles,
            ))
        return CatalogSearchResult(
            query=query,
            total=len(matches),
            matches=matches[:limit],
        )

    @server.tool(
        name="recommend-profile",
        description="Recommend the documented APIM profile from explicit consumer constraints.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def recommend_profile(
        requires_mcp: bool = True,
        external_backend: bool = False,
        private_network: bool = False,
        avoid_fixed_gateway_cost: bool = True,
    ) -> ProfileRecommendation:
        if not requires_mcp:
            return ProfileRecommendation(
                profile="rest-consumption",
                reason="The consumer needs REST/OpenAPI rather than MCP.",
                constraints=["Public mock backends only on the Consumption profile."],
            )
        if external_backend or private_network:
            return ProfileRecommendation(
                profile="native-mcp",
                reason="External backends or private networking require native MCP.",
                constraints=["Basic v2 or a compatible higher APIM tier has a fixed gateway cost."],
            )
        return ProfileRecommendation(
            profile="policy-mcp-consumption",
            reason=(
                "A public stateless mock MCP can use APIM Consumption without a "
                "fixed gateway charge."
                if avoid_fixed_gateway_cost
                else "The requested MCP is compatible with the Consumption policy runtime."
            ),
            constraints=[
                "Public mock backends only.",
                "Tools only; MCP resources and prompts are not supported.",
                "Each generated policy tool must fit within 16 KiB.",
            ],
        )

    @server.tool(
        name="policy-budget",
        description="Measure policy-MCP compatibility and per-shard byte budgets.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def policy_budget(contract: str = "") -> list[PolicyBudgetResult]:
        requested = contract.strip()
        results = []
        for scenario in workspace.catalog()["scenarios"]:
            if requested and scenario["id"] != requested:
                continue
            compatibility = scenario["compatibility"]["policy-mcp-consumption"]
            servers = [
                PolicyServerBudget(
                    tools=item["tools"],
                    sizeBytes=item["sizeBytes"],
                    limitBytes=16 * 1024,
                    remainingBytes=(16 * 1024) - item["sizeBytes"],
                    usagePercent=item["usagePercent"],
                )
                for item in compatibility.get("servers", [])
            ]
            results.append(PolicyBudgetResult(
                contract=scenario["id"],
                supported=compatibility["supported"],
                reason=compatibility.get("reason"),
                servers=servers,
            ))
        if requested and not results:
            raise ValueError(f"unknown contract: {requested}")
        return results

    def publish_dashboard() -> DashboardResult:
        index, html = workspace.dashboard()
        info = dashboard.publish(html)
        return DashboardResult(
            url=info.url,
            generation=info.generation,
            scenarios=index["summary"]["scenarios"],
        )

    @server.tool(
        name="dashboard-get-url",
        description="Start the secure loopback dashboard if needed and return its tokenized URL.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def dashboard_get_url() -> DashboardResult:
        if not dashboard.running:
            return publish_dashboard()
        index = workspace.catalog()
        info = dashboard.info()
        return DashboardResult(
            url=info.url,
            generation=info.generation,
            scenarios=index["summary"]["scenarios"],
        )

    @server.tool(
        name="dashboard-refresh",
        description="Rebuild the in-memory dashboard from current workspace files.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def dashboard_refresh() -> DashboardResult:
        return publish_dashboard()

    return LocalServer(server, workspace, dashboard)
