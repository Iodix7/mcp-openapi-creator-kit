"""One bounded, authoritative provider for tools, resources and prompts."""
from __future__ import annotations

from enum import Enum
import hashlib
import re

from .assets import asset_text, source_info
from .workflow import CurrentStep, GuidanceSource, GuidanceStage, WorkflowStatus


class Workflow(str, Enum):
    discovery = "discovery"
    onboarding = "onboarding"
    lifecycle = "lifecycle"


class Reference(str, Enum):
    handover = "handover"
    selective_deployment = "selective-deployment"
    canonical_schemas = "canonical-schemas"
    scenario_template = "scenario-template"
    sample_manifest = "sample-manifest"
    sample_contract = "sample-contract"
    consumer_targets = "consumer-targets"
    installation = "installation"
    migration = "migration"
    copilot_plugin = "copilot-plugin"
    gateway_provisioning = "gateway-provisioning"
    resource_group_provisioning = "resource-group-provisioning"
    gateway_retirement = "gateway-retirement"
    example_library = "example-library"
    extended_verification = "extended-verification"
    scenario_metadata = "scenario-metadata"


REFERENCES = {
    "handover": "HANDOVER.md",
    "selective-deployment": "docs/selective-deployment.md",
    "canonical-schemas": "apis/canonical-schemas.yaml",
    "scenario-template": "docs/templates/scenario-spec-template.md",
    "sample-manifest": "clients/sample/mcp-manifest.yaml",
    "sample-contract": "apis/customer-care/openapi.yaml",
    "consumer-targets": "docs/consumer-targets.md",
    "installation": "docs/installation.md",
    "migration": "docs/customer-workspace-migration.md",
    "copilot-plugin": "docs/copilot-plugin.md",
    "gateway-provisioning": "docs/gateway-provisioning.md",
    "resource-group-provisioning": "docs/resource-group-provisioning.md",
    "gateway-retirement": "docs/gateway-retirement.md",
    "example-library": "docs/example-library.md",
    "extended-verification": "docs/extended-verification.md",
    "scenario-metadata": "docs/scenario-metadata.md",
}

STEP_SOURCES: dict[GuidanceStage, str] = {
    "collect-context": "discovery",
    "define-scenario": "discovery",
    "inspect-gateway": "onboarding",
    "resolve-blockers": "onboarding",
    "configure-client": "onboarding",
    "prepare": "onboarding",
    "preview": "onboarding",
    "review-plan": "onboarding",
    "provision-gateway": "onboarding",
    "provision-resource-group": "onboarding",
}


def step_document(stage: GuidanceStage) -> tuple[dict[str, str], GuidanceSource]:
    """Extract exact, bounded sections from the same documents returned by full guides."""
    workflow = STEP_SOURCES[stage]
    asset = f"skills/{workflow}.md"
    doc = document(asset)
    start, end = f"<!-- kit-step:{stage} -->", f"<!-- /kit-step:{stage} -->"
    text = doc["content"]
    if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
        raise ValueError(f"Invalid packaged procedure markers for {asset}: {stage}; repair/reinstall the kit.")
    excerpt = text.split(start, 1)[1].split(end, 1)[0].strip()
    headings = list(re.finditer(r"^### (Instructions|Consent|Completion)\s*$", excerpt, re.MULTILINE))
    if ([match.group(1) for match in headings] != ["Instructions", "Consent", "Completion"] or
            excerpt[:headings[0].start()].strip() or len(excerpt) > 12000):
        raise ValueError(f"Invalid packaged procedure sections for {asset}: {stage}; repair/reinstall the kit.")
    fields = {}
    for index, match in enumerate(headings):
        stop = headings[index + 1].start() if index + 1 < len(headings) else len(excerpt)
        content = excerpt[match.end():stop].strip()
        if not content:
            raise ValueError(f"Empty packaged procedure section for {asset}: {stage}")
        fields[match.group(1).lower()] = content
    return fields, GuidanceSource(
        **doc["provenance"], resource_uri=f"kit://skills/{workflow}",
        document_sha256=doc["sha256"],
        excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest())


def with_current_step(status: WorkflowStatus) -> WorkflowStatus:
    fields, source = step_document(status.next_stage)
    why = [status.reason, *status.blockers]
    why.extend(f"Missing input: {key}" for key in status.missing_inputs)
    relevant = {"define-scenario": "specification", "configure-client": "manifest",
                "prepare": "prepare", "preview": "preview"}.get(status.next_stage)
    why.extend(f"{step.id}: {step.status}. {step.detail}" for step in status.steps if step.id == relevant)
    return status.model_copy(update={"current_step": CurrentStep(
        stage=status.next_stage, why=list(dict.fromkeys(why)), source=source, **fields)})


def document(path: str) -> dict:
    text = asset_text(path)
    return {"content": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "provenance": {**source_info(), "asset": path}}


def constitution() -> dict:
    return document("AGENTS.md")


def workflow_guide(workflow: Workflow | str) -> dict:
    name = Workflow(workflow).value
    return {
        "workflow": name, "constitution": constitution(),
        "procedure": document(f"skills/{name}.md"),
        "references": list(REFERENCES),
        "nextSteps": [
            "workflow-status includes currentStep instructions, consent and completion from these same packaged procedures; no separate guide retrieval is needed for that step.",
            "Call workflow-status with the operator's consumer/gateway choice; only inspect-gateway can issue gateway evidence.",
            "Use kit-reference for full packaged references; catalog-search source=builtin browses optional example data.",
            "MCP only reads and plans. Run explicit mcp-kit CLI commands from the customer root for writes.",
            "mcp-kit init (plan); mcp-kit init --write (create data directories only).",
            "Optional: mcp-kit import-sample <new-client> (plan); repeat with --write.",
            "mcp-kit examples lists the original optional library; import-example <scenario> <new-client> "
            "previews a selected original scenario. import-sample remains the separate neutral starter.",
            "Read onboarding; write/review docs/<id>/spec.md. After local-write approval run mcp-kit prepare clients/<id> --profile <profile>.",
            "After import preview mcp-kit spec-sync <id>, then --write only after approval. It generates contract-owned tables; write/review narrative separately. scenario-contract checks both.",
            "Call workflow-status again: preparation is not completion. Follow nextCommand for the separately approved selective preview.",
            "After preview, call workflow-status and dashboard-refresh. Present the recorded plan; never claim deployment from preview-recorded.",
            "For a new gateway, collect proposed provisioning_target from the operator, prepare locally, "
            "including existing/new resource-group choice and an explicit group region when new. "
            "Use provision-group first for a new group, then switch group mode to existing only after "
            "verified creation; use the exact provision preview invocation for APIM. Each creation "
            "requires a separately reviewed apply; "
            "inspect the resulting gateway as existing before client deployment.",
        ],
    }


def reference(name: Reference | str) -> dict:
    value = Reference(name).value
    return {"name": value, **document(REFERENCES[value])}


def kit_info() -> dict:
    return {
        **source_info(), "constitution": constitution(),
        "workflows": [item.value for item in Workflow],
        "references": list(REFERENCES),
        "startHere": "Call workflow-status for contextual currentStep instructions; workflow-guide returns full discovery, onboarding or lifecycle procedures.",
        "safety": "Companion is read-only. File writes use explicit installed CLI; deployment is separately gated.",
        "inspectionConsent": "inspect-gateway requires a per-invocation MCP form response from the host, not a model argument. Unsupported clients hand off to interactive CLI.",
        "commands": ["mcp-kit --help", "mcp-kit init", "mcp-kit vscode-config",
                     "mcp-kit plugin-export --help",
                     "mcp-kit catalog --source builtin", "mcp-kit import-sample <new-client>",
                     "mcp-kit examples", "mcp-kit import-example <scenario> <new-client>",
                     "mcp-kit workflow-status", "mcp-kit inspect-gateway --help", "mcp-kit prepare --help",
                     "mcp-kit provision-group --help", "mcp-kit provision --help", "mcp-kit retire --help",
                     "mcp-kit spec-sync <id>", "mcp-kit spec-sync <id> --write"],
        "scenarioContract": "scenario-contract(client=<id>) reads exact imported assertions; CLI: mcp-kit scenario-contract <id>.",
        "hostInvocation": "workflow-status.nextInvocation pins interpreter/cwd/arguments. Hosts must obtain separate approval; MCP never executes it.",
    }
