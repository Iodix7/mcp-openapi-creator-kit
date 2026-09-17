"""Read-only MCP server for exploring and planning from a kit workspace."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .dashboard import DashboardHost
from .workspace import WorkspaceReader, WorkspaceError
from .workflow import (
    GatewayMode, GatewayObservation, GatewayProvisioningTarget, GatewayTarget, Profile, WorkflowStatus,
)
from . import guidance
from . import __version__

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
    kit_version: str = Field(alias="kitVersion")
    kit_source: str = Field(alias="kitSource")
    workspace_mode: str = Field(alias="workspaceMode")
    next_steps: list[str] = Field(alias="nextSteps")
    local_instructions: str = Field(alias="localInstructions")


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
        description="Start with kit-info, then workflow-guide (discovery/onboarding/lifecycle) "
                    "for full authoritative instructions. Read-only companion; explicit mcp-kit CLI "
                    "does writes and separately gated deployment. Customer root is pinned.",
        version=__version__,
        lifespan=lifespan,
    )

    @server.resource(
        "kit://constitution",
        name="constitution",
        description="Versioned installed-kit constitution and non-negotiable safety rules.",
        mime_type="text/markdown",
    )
    def constitution() -> str:
        return guidance.constitution()["content"]

    @server.resource(
        "kit://skills/discovery",
        name="discovery-skill",
        description="Procedure for discovering an agent API scenario.",
        mime_type="text/markdown",
    )
    def discovery_skill() -> str:
        return guidance.workflow_guide("discovery")["procedure"]["content"]

    @server.resource(
        "kit://skills/onboarding",
        name="onboarding-skill",
        description="Procedure for configuring and deploying a client.",
        mime_type="text/markdown",
    )
    def onboarding_skill() -> str:
        return guidance.workflow_guide("onboarding")["procedure"]["content"]

    @server.resource(
        "kit://skills/lifecycle",
        name="lifecycle-skill",
        description="Procedure for safely changing deployed clients.",
        mime_type="text/markdown",
    )
    def lifecycle_skill() -> str:
        return guidance.workflow_guide("lifecycle")["procedure"]["content"]

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

    @server.resource(
        "kit://workflow/status", name="workflow-status",
        description="Current shared workflow decisions; never an approval. No Azure calls.",
        mime_type="application/json",
    )
    def workflow_status_resource() -> str:
        return json.dumps(workspace.workflow_snapshot(), indent=2, ensure_ascii=False) + "\n"

    def skill_prompt(skill: str, context: str) -> str:
        guide = guidance.workflow_guide(skill)
        context_text = context.strip() or "No additional context was supplied."
        return (
            f"Follow the installed kit procedure below. Stay read-only and propose the "
            f"next safe steps for this workspace.\n\nContext:\n{context_text}\n\n"
            f"{guide['constitution']['content']}\n\n{guide['procedure']['content']}"
        )

    @server.tool(name="kit-info", description="START HERE: installed kit provenance, full constitution, workflow discovery and exact CLI next steps.",
                 annotations=READ_ONLY, structured_output=True)
    def kit_info() -> dict[str, Any]:
        return guidance.kit_info()

    @server.tool(name="workflow-guide", description="Full trusted discovery/onboarding/lifecycle procedure and constitution. Tool fallback when prompts/resources are unavailable. Read-only.",
                 annotations=READ_ONLY, structured_output=True)
    def workflow_guide(workflow: guidance.Workflow) -> dict[str, Any]:
        return guidance.workflow_guide(workflow)

    @server.tool(name="kit-reference", description="Read an allowlisted packaged handover, selective deployment guide, canonical schemas, scenario template or fictional sample. No arbitrary file access.",
                 annotations=READ_ONLY, structured_output=True)
    def kit_reference(name: guidance.Reference) -> dict[str, Any]:
        return guidance.reference(name)

    @server.prompt(name="discovery", description="Plan scenario discovery using the installed procedure.")
    def discovery_prompt(context: str = "") -> str:
        return skill_prompt("discovery", context)

    @server.prompt(name="onboarding", description="Plan client onboarding using the installed procedure.")
    def onboarding_prompt(context: str = "") -> str:
        return skill_prompt("onboarding", context)

    @server.prompt(name="lifecycle", description="Plan a safe lifecycle change using the installed procedure.")
    def lifecycle_prompt(context: str = "") -> str:
        return skill_prompt("lifecycle", context)

    @server.tool(
        name="workspace-status",
        description="Inspect pinned customer data, kit version/source, workspace mode and initialization guidance.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def workspace_status() -> WorkspaceStatus:
        return WorkspaceStatus.model_validate(workspace.status())

    @server.tool(
        name="inspect-gateway",
        description="Request explicit client form consent, then read Azure for that single target/account. "
                    "Requires explicit account, tenant, subscription, resourceGroup and apimName. "
                    "Verifies active CLI context without switching it; reads APIM and global diagnostics. "
                    "Returns a short-lived evidenceId for workflow-status. Never deploys or approves writes.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=True),
        structured_output=True,
    )
    async def inspect_gateway(target: GatewayTarget, ctx: Context) -> GatewayObservation:
        from .consent import request_inspection
        await request_inspection(ctx, target, str(workspace.root))
        try:
            return workspace.gateway_evidence.inspect(target)
        except (ValueError, RuntimeError, OSError) as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="workflow-status",
        description="Return currentStep: essential instructions, required consent, completion checks and "
                    "versioned source, selected from the current state. Follow these instructions and nextCommand "
                    "directly; no separate workflow-guide call is needed for this step. "
                    "Evaluate missing inputs, derived profile and completion stages. "
                    "nextInvocation pins the installed executable, arguments and cwd for a host executor; "
                    "it is not execution or permission. scenario-contract supplies exact imported spec assertions. "
                    "Call again after prepare and preview; preview-recorded is NOT deployed. Offline unless "
                    "inspect-gateway was explicitly called. Consumer choice and gateway_mode must be "
                    "collected from the operator; tiers/pasted facts are NOT evidence. client is an "
                    "existing or planned slug; an absent manifest is reported as missing input. "
                    "evidence_id comes only from this session's inspect-gateway. No deployment approval. "
                    "For gateway_mode=new, provisioning_target holds the operator's proposed context "
                    "(including location and publisher), never evidence. Explicit resource_group_mode=new "
                    "also requires resource_group_location and guides a separate provision-group stage "
                    "before APIM; existing is the compatibility default, never an observation. "
                    "After local preparation it supplies "
                    "a create-only provisioning preview invocation, not automatic creation. "
                    "Omitted preferences retain this session's last choices.",
        annotations=READ_ONLY, structured_output=True,
    )
    def workflow_status(
        client: str = "", requires_mcp: bool | None = None,
        gateway_mode: GatewayMode | None = None, evidence_id: str | None = None,
        selected_profile: Profile | None = None, external_backend: bool | None = None,
        private_network: bool | None = None, avoid_fixed_gateway_cost: bool | None = None,
        clear_evidence: bool = False,
        provisioning_target: GatewayProvisioningTarget | None = None,
    ) -> WorkflowStatus:
        try:
            return workspace.workflow_status(
                client, requires_mcp=requires_mcp, gateway_mode=gateway_mode,
                evidence_id=evidence_id, selected_profile=selected_profile,
                external_backend=external_backend, private_network=private_network,
                avoid_fixed_gateway_cost=avoid_fixed_gateway_cost, clear_evidence=clear_evidence,
                provisioning_target=provisioning_target)
        except (ValueError, WorkspaceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="target-capabilities",
        description="Read consumer/gateway compatibility, preview restrictions and supported auth. No deployment.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def target_capabilities() -> dict[str, Any]:
        from .targets import target_capabilities as capabilities
        return capabilities()

    @server.tool(
        name="target-report",
        description="Validate a client target and preview artifacts in memory; does not export files, access tenants or deploy.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def target_report(client: str) -> dict[str, Any]:
        return workspace.target_report(client)

    @server.tool(
        name="scenario-contract",
        description="Read the selected client's actual imported operation IDs, routes, parameters, "
                    "response codes/examples and x-mock. Returns a read-only specSync preview, referenceMarkdown "
                    "and deterministic spec.md consistency issues. After import use explicit CLI spec-sync "
                    "to generate technical sections; write/review only the narrative. "
                    "do not guess routes or remove client prefixes. Does not write or approve prose.",
        annotations=READ_ONLY, structured_output=True,
    )
    def scenario_contract(client: str) -> dict[str, Any]:
        from .scenario import scenario_report
        try:
            return scenario_report(workspace.root, client)
        except (ValueError, OSError) as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="catalog-search",
        description="Search the live catalog; an empty/default query browses all entries (at most 50). Read-only.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def catalog_search(query: str = "", limit: int = 10,
                       source: Literal["workspace", "builtin"] = "workspace") -> CatalogSearchResult:
        if not 1 <= limit <= 50:
            raise ToolError("limit must be between 1 and 50")
        needle = query.strip().casefold()
        matches = []
        for scenario in workspace.catalog(source)["scenarios"]:
            operations = scenario.get("operations", [])
            haystack = " ".join([
                scenario.get("id", ""),
                _localized(scenario.get("title")),
                scenario.get("description", ""),
                scenario.get("domain", ""),
                json.dumps([scenario.get(field) for field in
                            ("persona", "jobToBeDone", "outcome", "scenarioContexts")],
                           ensure_ascii=False),
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
        description="Compatibility alias for workflow-status. Unknown consumer/gateway inputs yield "
                    "only a provisional profile. existing_tier is a legacy, unverified declaration, "
                    "never proof. Use inspect-gateway then workflow-status for a verified decision.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def recommend_profile(
        requires_mcp: bool | None = None,
        external_backend: bool = False,
        private_network: bool = False,
        avoid_fixed_gateway_cost: bool = True,
        existing_tier: Literal["Consumption", "Developer", "Basic", "BasicV2",
                               "Standard", "StandardV2", "Premium", "PremiumV2"] | None = None,
    ) -> WorkflowStatus:
        from .workflow import evaluate_workflow
        return guidance.with_current_step(evaluate_workflow(
            requires_mcp=requires_mcp, external_backend=external_backend,
            private_network=private_network, avoid_fixed_gateway_cost=avoid_fixed_gateway_cost,
            gateway_mode="existing" if existing_tier else "unknown"))

    @server.tool(
        name="policy-budget",
        description="Measure policy-MCP compatibility and per-shard byte budgets. "
                    "contract is the catalog scenario ID (API folder name), not a file path. "
                    "Omit contract to inspect all workspace contracts.",
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
            raise ToolError("Unknown contract. Use a scenario ID from catalog-search "
                            "(API folder name, not an OpenAPI file path), or omit contract.")
        return results

    def publish_dashboard(source: str = "workspace") -> DashboardResult:
        index, html = workspace.dashboard(source)
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
    def dashboard_refresh(source: Literal["workspace", "builtin"] = "workspace") -> DashboardResult:
        return publish_dashboard(source)

    return LocalServer(server, workspace, dashboard)
