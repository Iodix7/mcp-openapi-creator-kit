"""Shared, offline workflow decisions. Observations are issued by the kit, not inputs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

Profile = Literal["native-mcp", "policy-mcp-consumption", "rest-consumption"]
GatewayMode = Literal["unknown", "new", "existing"]
PROFILES = {"native-mcp", "policy-mcp-consumption", "rest-consumption"}
NATIVE_TIERS = {"Developer", "Basic", "BasicV2", "Standard", "StandardV2", "Premium", "PremiumV2"}
EVIDENCE_LIFETIME = timedelta(minutes=5)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True,
                              alias_generator=to_camel)


class GatewayTarget(Record):
    subscription: str
    tenant: str
    resource_group: str = Field(pattern=r"^[A-Za-z0-9_().-]{1,90}$")
    apim_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,49}$")
    account: str = Field(min_length=1, max_length=320)

    @field_validator("subscription", "tenant")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("resource_group", "account")
    @classmethod
    def clean_value(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Target values cannot contain whitespace padding or control characters")
        if value.endswith("."):
            raise ValueError("Target values cannot end with a period")
        return value

    @property
    def resource_id(self) -> str:
        return (f"/subscriptions/{self.subscription}/resourceGroups/{self.resource_group}"
                f"/providers/Microsoft.ApiManagement/service/{self.apim_name}")


class GatewayProvisioningTarget(GatewayTarget):
    location: str = Field(pattern=r"^[a-z][a-z0-9]{1,63}$")
    publisher_name: str = Field(min_length=1, max_length=100)
    publisher_email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    resource_group_mode: Literal["existing", "new"] = "existing"
    resource_group_location: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9]{1,39}$")

    @model_validator(mode="after")
    def explicit_group_location(self) -> Self:
        if self.resource_group_mode == "new" and self.resource_group_location is None:
            raise ValueError("A new resource group requires an explicit resource_group_location")
        if self.resource_group_mode == "existing" and self.resource_group_location is not None:
            raise ValueError("resource_group_location is only for new groups; existing groups are never relocated")
        return self

    @field_validator("publisher_name")
    @classmethod
    def clean_publisher(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Publisher name cannot contain whitespace padding or control characters")
        return value


class GatewayFacts(Record):
    tier: str
    identity_type: str
    provisioning_state: str
    public_network_access: str
    virtual_network_type: str
    has_vnet: bool

    @classmethod
    def from_arm(cls, apim: dict) -> GatewayFacts:
        if not isinstance(apim, dict):
            raise ValueError("Incomplete APIM metadata")
        sku, props, identity = apim.get("sku"), apim.get("properties"), apim.get("identity")
        if identity is None:
            identity = {}
        if not all(isinstance(item, dict) for item in (sku, props, identity)):
            raise ValueError("Incomplete APIM SKU/properties/identity metadata")
        values = {
            "tier": sku.get("name"), "identity_type": identity.get("type", "None"),
            "provisioning_state": props.get("provisioningState"),
            "public_network_access": props.get("publicNetworkAccess", "Enabled"),
            "virtual_network_type": props.get("virtualNetworkType", "None"),
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("Incomplete APIM capability metadata")
        vnet = props.get("virtualNetworkConfiguration")
        if vnet is not None and not isinstance(vnet, dict):
            raise ValueError("Incomplete APIM virtual network metadata")
        return cls(**values, has_vnet=bool(vnet))


class GatewayObservation(Record):
    evidence_id: str
    workspace: str
    target: GatewayTarget
    facts: GatewayFacts
    observed_at: datetime
    expires_at: datetime
    diagnostic_issues: tuple[str, ...] = ()
    source: Literal["azure-cli-read-only"] = "azure-cli-read-only"

    @classmethod
    def issue(cls, workspace: str, target: GatewayTarget, apim: dict,
              diagnostics: list[dict], *, now: datetime | None = None) -> GatewayObservation:
        now = now or datetime.now(timezone.utc)
        return cls(evidence_id=uuid4().hex, workspace=workspace, target=target,
                   facts=GatewayFacts.from_arm(apim), observed_at=now,
                   expires_at=now + EVIDENCE_LIFETIME,
                   diagnostic_issues=tuple(diagnostic_violations(diagnostics)))


def gateway_violations(profile: str, facts: GatewayFacts, network: str) -> list[str]:
    violations = []
    if profile not in PROFILES:
        violations.append("Unknown gateway profile")
    elif profile == "native-mcp":
        if facts.tier not in NATIVE_TIERS:
            violations.append("existing APIM tier does not support native MCP")
        if "SystemAssigned" not in {part.strip() for part in facts.identity_type.split(",")}:
            violations.append("existing APIM requires a system-assigned managed identity")
    elif facts.tier != "Consumption":
        violations.append("Consumption profiles require an existing Consumption APIM; use native-mcp for BasicV2 mocks")
    if facts.provisioning_state != "Succeeded":
        violations.append("existing APIM provisioning state must be Succeeded")
    if network not in {"public", "hybrid", "isolated"}:
        violations.append("unknown manifest networkProfile")
    elif network == "public":
        if facts.public_network_access != "Enabled" or facts.virtual_network_type == "Internal":
            violations.append("manifest requires public access but the existing gateway is private")
    elif network == "isolated":
        if facts.virtual_network_type != "Internal":
            violations.append("isolated manifest requires an existing internally injected gateway")
    else:
        if not (facts.has_vnet or facts.virtual_network_type in {"Internal", "External"}):
            violations.append("hybrid manifest requires existing gateway VNet connectivity")
        if facts.public_network_access != "Enabled" or facts.virtual_network_type == "Internal":
            violations.append("hybrid manifest requires public ingress; use isolated for a private-only gateway")
    return violations


def diagnostic_violations(items: list[dict]) -> list[str]:
    if not isinstance(items, list):
        return ["Incomplete diagnostic metadata"]
    for item in items:
        properties = item.get("properties") if isinstance(item, dict) else None
        if not isinstance(properties, dict):
            return ["Incomplete diagnostic metadata"]
        for side in ("frontend", "backend"):
            value = properties.get(side)
            for key in ("response", "body"):
                if value is None:
                    value = {}
                if not isinstance(value, dict):
                    return ["Incomplete diagnostic metadata"]
                value = value.get(key)
            if value is None:
                value = {}
            if not isinstance(value, dict):
                return ["Incomplete diagnostic metadata"]
            size = value.get("bytes", 0)
            if isinstance(size, bool) or not isinstance(size, (int, float)) or size != 0:
                return ["Unsafe response-body diagnostic logging: inspect global/API frontend/backend "
                        "response payload bytes and set to 0 through your approved operations process. "
                        "No diagnostics were changed."]
    return []


class PreviewChange(Record):
    resource_id: str
    resource_type: str
    change_type: str


class PreviewPlan(Record):
    review_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    changes: list[PreviewChange]
    deletions: list[str]


class WorkflowStep(Record):
    id: Literal["manifest", "specification", "build", "build-policy", "prepare", "preview"]
    status: Literal["missing", "present", "incomplete", "invalid", "stale", "recorded-current"]
    detail: str
    recorded_at: str | None = None
    plan: PreviewPlan | None = None


GuidanceStage = Literal["collect-context", "inspect-gateway", "resolve-blockers",
                        "define-scenario", "configure-client", "prepare", "preview",
                        "review-plan", "provision-gateway", "provision-resource-group"]


class GuidanceSource(Record):
    asset: str
    resource_uri: str
    version: str
    source: str
    manifest_sha256: str | None
    document_sha256: str
    excerpt_sha256: str


class CurrentStep(Record):
    stage: GuidanceStage
    why: list[str]
    instructions: str
    consent: str
    completion: str
    source: GuidanceSource


class CliInvocation(Record):
    executable: str
    arguments: list[str]
    cwd: str
    cli_arguments: list[str]
    requires_operator_approval: Literal[True] = True
    effect: Literal["local-write", "deployment-preview", "provisioning-preview"]


class WorkflowStatus(Record):
    client: str | None
    status: Literal["needs-input", "provisional", "blocked", "ready-for-preparation", "ready-for-preview", "preview-recorded"]
    profile: Profile | None
    provisional_profile: Profile | None
    reason: str
    evidence_status: Literal["missing", "verified", "stale"]
    evidence: GatewayObservation | None
    provisioning_target: GatewayProvisioningTarget | None = None
    missing_inputs: list[str]
    blockers: list[str]
    allowed_actions: list[str]
    next_action: str
    checks_pending: list[str]
    approval_status: Literal["not-granted"] = "not-granted"
    completion: Literal["not-assessed", "not-started", "local-incomplete", "preview-required", "preview-recorded", "blocked"] = "not-assessed"
    steps: list[WorkflowStep] = Field(default_factory=list)
    next_command: list[str] = Field(default_factory=list)
    next_invocation: CliInvocation | None = None
    progress_notice: str = ""
    next_stage: GuidanceStage
    current_step: CurrentStep | None = None


def evaluate_workflow(manifest: dict | None = None, *, requires_mcp: bool | None = None,
                      gateway_mode: GatewayMode = "unknown",
                      selected_profile: Profile | None = None,
                      observation: GatewayObservation | None = None,
                      provisioning_target: GatewayProvisioningTarget | None = None,
                      external_backend: bool = False, private_network: bool = False,
                      avoid_fixed_gateway_cost: bool = True,
                      planned_client: str | None = None,
                      now: datetime | None = None) -> WorkflowStatus:
    """Readiness permits attempting preflight, never bypassing validation or approving apply."""
    if gateway_mode not in {"unknown", "new", "existing"}:
        raise ValueError("gateway_mode must be unknown, new or existing")
    if selected_profile is not None and selected_profile not in PROFILES:
        raise ValueError("Unknown selected_profile")
    if requires_mcp is not None and not isinstance(requires_mcp, bool):
        raise ValueError("requires_mcp must be an explicit boolean or unknown")
    if provisioning_target is not None:
        provisioning_target = GatewayProvisioningTarget.model_validate(provisioning_target)
    missing, blockers = [], []
    client = planned_client
    network = "hybrid" if private_network else "public"
    if manifest is not None:
        client = manifest.get("client")
        if not isinstance(client, str) or not client:
            raise ValueError("Workflow manifest requires a client ID")
        network = manifest.get("networkProfile", network)
        if private_network and network == "public":
            network = "hybrid"
        if isinstance(manifest.get("targets"), dict) and manifest["targets"].get("gateway") == "ai-gateway-preview":
            blockers.append("AI Gateway is plan-only; selective deployment is not supported")
        apis = manifest.get("apis")
        if not isinstance(apis, list) or not apis:
            blockers.append("Manifest requires a non-empty apis list")
        else:
            for api in apis:
                backend = api.get("backend") if isinstance(api, dict) else None
                mode = backend.get("mode") if isinstance(backend, dict) else None
                if mode not in {"mock", "external"}:
                    blockers.append("Each manifest backend must be mock or external")
                external_backend = external_backend or mode == "external"
        if network not in {"public", "hybrid", "isolated"}:
            blockers.append("unknown manifest networkProfile")
    if requires_mcp is None:
        missing.append("requires_mcp")
    if gateway_mode == "unknown":
        missing.append("gateway_mode")
    if observation and gateway_mode == "new":
        blockers.append("Existing gateway evidence conflicts with a new-gateway choice")
    if provisioning_target and gateway_mode != "new":
        blockers.append("A proposed provisioning target requires gateway_mode=new; it is not gateway evidence")
    if gateway_mode == "new":
        if network != "public":
            blockers.append("Standalone gateway provisioning supports public networking only; "
                            "private networking requires a separately provisioned existing gateway")
    now = now or datetime.now(timezone.utc)
    evidence_status = ("missing" if observation is None else "verified"
                       if observation.observed_at <= now < observation.expires_at else "stale")
    if gateway_mode == "existing" and evidence_status != "verified":
        missing.append("gateway_evidence")
    if client is None:
        missing.append("client")
    elif manifest is None:
        missing.append("client_manifest")
    if gateway_mode == "new" and provisioning_target is None:
        missing.append("provisioning_target")
    native_required = external_backend or private_network or network != "public"
    candidate = None
    if requires_mcp is not None:
        candidate = ("native-mcp" if native_required or (
                     gateway_mode == "new" and (selected_profile == "native-mcp" or
                                               requires_mcp and not avoid_fixed_gateway_cost)) else
                     "policy-mcp-consumption" if requires_mcp else "rest-consumption")
        if evidence_status == "verified" and observation.facts.tier in NATIVE_TIERS:
            candidate = "native-mcp"
    reason = "Choose the consumer experience and whether to reuse an existing gateway."
    if candidate:
        reason = ("External backends/private networking require native-mcp." if native_required else
                  "Offline candidate only; an existing gateway must be inspected before finalizing.")
    if observation and evidence_status == "verified":
        reason = f"Observed {observation.facts.tier} on the explicitly selected gateway."
        if avoid_fixed_gateway_cost and observation.facts.tier in NATIVE_TIERS:
            reason += " Reuse does not remove that gateway's existing charges."
        if candidate:
            profile = selected_profile or candidate
            blockers.extend(gateway_violations(profile, observation.facts, network))
            if native_required and profile != "native-mcp":
                blockers.append("External/private manifests require native-mcp; caller preferences cannot override them")
            if requires_mcp and profile == "rest-consumption":
                blockers.append("REST endpoints cannot serve an MCP consumer")
            if profile != "rest-consumption":
                blockers.extend(observation.diagnostic_issues)
    if selected_profile and candidate and selected_profile != candidate:
        blockers.append(f"selected_profile={selected_profile} conflicts with the derived profile {candidate}")
    verified = evidence_status == "verified" and gateway_mode == "existing" and candidate and not blockers
    allowed = ["read-guidance", "prepare-local-data", "build-offline"]
    if gateway_mode != "new":
        allowed.append("inspect-gateway")
    if verified and not missing:
        allowed.append("deploy-preview")
    if gateway_mode == "new" and provisioning_target and candidate and not blockers and not missing:
        allowed.append("provision-group-preview" if provisioning_target.resource_group_mode == "new"
                       else "provision-preview")
    status = ("blocked" if blockers else "needs-input" if missing else
              "ready-for-preview" if verified else "provisional")
    next_action = (
        "Resolve the reported blockers; do not deploy or change the gateway automatically." if blockers else
        f"Ask the operator for {missing[0]}." if missing and missing[0] != "gateway_evidence" else
        "Run inspect-gateway with the approved account/target; never supply a tier as evidence."
        if missing else
        "Run selective deploy preview with the same explicit target and derived profile; review its plan."
        if verified else
        "Follow the installed resource-group/gateway provision command after local preparation "
        "and separate full-context approval; "
        "a proposed target is not evidence that a gateway exists."
    )
    if selected_profile and candidate and selected_profile != candidate and evidence_status == "verified":
        next_action = (f"Re-evaluate workflow-status with selected_profile='{candidate}' to reuse the observed "
                       "gateway; resolve any remaining blockers. Do not replace the gateway automatically.")
    elif not blockers and missing and missing[0] == "client_manifest":
        next_action = (f"Follow currentStep to prepare the approved manifest for client '{client}' "
                       "with explicit local writes, then call workflow-status again.")
    stage: GuidanceStage = (
        "resolve-blockers" if blockers else
        "collect-context" if missing and missing[0] in {"requires_mcp", "gateway_mode", "client"} else
        "inspect-gateway" if "gateway_evidence" in missing else
        "configure-client" if "client_manifest" in missing else
        "provision-resource-group" if gateway_mode == "new" and provisioning_target
        and provisioning_target.resource_group_mode == "new" else
        "provision-gateway" if gateway_mode == "new" else "prepare"
    )
    return WorkflowStatus(
        client=client, status=status, profile=candidate if verified else None,
        provisional_profile=candidate, reason=reason, evidence_status=evidence_status,
        evidence=observation, provisioning_target=provisioning_target,
        missing_inputs=missing, blockers=list(dict.fromkeys(blockers)),
        allowed_actions=allowed, next_action=next_action,
        next_stage=stage,
        checks_pending=["Contract validation", "Selected API diagnostics, ownership and secret metadata",
                        "ARM what-if and reconciliation review", "Separate operator approval of the exact plan"],
    )
