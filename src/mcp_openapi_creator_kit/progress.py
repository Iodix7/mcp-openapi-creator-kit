"""Local execution receipts for UX only. Never authorize cloud access or apply."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from .assets import kit_root, verify_assets
from .data_paths import safe_data_path
from .workflow import CliInvocation, GatewayTarget, PreviewPlan, WorkflowStatus, WorkflowStep

STEPS = {"build", "build-policy", "prepare", "preview"}
MAX_RECEIPT_BYTES = 1024 * 1024
NOTICE = ("Local CLI execution receipts are advisory history, not authenticated Azure evidence or human approval. "
          "Selective deploy always revalidates independently; unrestricted same-user writes are outside this boundary.")


def receipt_path(root: Path, client: str, step: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", client) or step not in STEPS:
        raise ValueError("Invalid workflow receipt client or step")
    path = safe_data_path(root, root / ".mcp-kit" / "workflow" / client / (step + ".json"))
    if path.exists() and not path.is_file():
        raise ValueError("Workflow receipt path must be a file")
    return path


def _hash(root: Path, paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        path = safe_data_path(root, path)
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def input_digest(root: Path, client: str) -> str:
    # All shared contracts/manifests participate in generator collision checks.
    paths = []
    for pattern in ("clients/*/mcp-manifest.yaml", "apis/*/openapi.yaml", "apis/canonical-schemas.yaml"):
        paths.extend(root.glob(pattern))
    spec = safe_data_path(root, root / "docs" / client / "spec.md")
    if spec.is_file():
        paths.append(spec)
    provenance = verify_assets()
    if provenance["source"] == "source-development":
        source = kit_root()
        code = [*source.glob("tools/*.py"), *source.glob("src/mcp_openapi_creator_kit/**/*.py"),
                *source.glob("modules/*"), *source.glob("skills/*.md"), source / "AGENTS.md"]
        provenance = {**provenance, "sourceHash": _hash(source, (p for p in code if p.is_file()))}
    return hashlib.sha256((os.path.normcase(str(root.resolve())) + _hash(root, paths) +
                           json.dumps(provenance, sort_keys=True)).encode()).hexdigest()


def output_digest(root: Path, client: str, step: str) -> str:
    generated = safe_data_path(root, root / "clients" / client / "generated")
    paths = []
    for path in generated.rglob("*"):
        safe_data_path(root, path)
        if not path.is_file():
            continue
        relative = path.relative_to(generated)
        if relative.parts[0] == "targets":
            continue
        if step == "build" and relative.parts[0] == "policy-mcp":
            continue
        if step == "build-policy" and relative.parts[0] not in {"policy-mcp", "kit-modules"}:
            continue
        paths.append(path)
    return _hash(root, paths)


def _write(path: Path, value: dict) -> None:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ValueError("Workflow history exceeds the 1 MiB receipt limit; review the CLI output. "
                         "No completed preview was recorded.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".receipt-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def begin_step(root: Path, client: str, step: str) -> str:
    path = receipt_path(root, client, step)
    inputs = input_digest(root, client)
    _write(path, {"version": 1, "client": client, "step": step, "outcome": "incomplete",
                  "inputs": inputs, "recordedAt": datetime.now(timezone.utc).isoformat()})
    return inputs


def finish_step(root: Path, client: str, step: str, inputs: str, *, profile: str | None = None,
                target: dict | None = None, plan: dict | None = None) -> None:
    path = receipt_path(root, client, step)
    if input_digest(root, client) != inputs:
        raise ValueError("Local inputs changed during execution; rerun before recording workflow completion")
    if step in {"build", "prepare", "preview"}:
        required = safe_data_path(root, root / "clients" / client / "generated" / "client.bicep")
        if not required.is_file():
            raise ValueError("Generated client.bicep is missing; cannot record successful preparation")
    _write(path, {"version": 1, "client": client, "step": step, "outcome": "succeeded",
                  "inputs": inputs, "outputs": output_digest(root, client, step),
                  "profile": profile, "target": target, "plan": plan,
                  "recordedAt": datetime.now(timezone.utc).isoformat()})


def read_step(root: Path, client: str, step: str, inputs: str, *, profile: str | None = None,
              target: dict | None = None) -> dict:
    path = receipt_path(root, client, step)
    result = {"id": step, "status": "missing", "detail": f"No recorded {step} execution."}
    if not path.is_file():
        return result
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        return {**result, "status": "invalid", "detail": "Oversized local receipt; rerun the CLI step."}
    try:
        value = json.loads(path.read_text("utf-8"))
        if not isinstance(value, dict) or any(value.get(k) != v for k, v in
                {"version": 1, "client": client, "step": step}.items()):
            raise ValueError("Invalid receipt shape")
        recorded = datetime.fromisoformat(value["recordedAt"])
        if recorded.tzinfo is None:
            raise ValueError("Receipt timestamp must be timezone-aware")
    except (ValueError, KeyError, TypeError):
        return {**result, "status": "invalid", "detail": "Invalid local receipt; rerun the CLI step."}
    if value.get("outcome") != "succeeded":
        return {**result, "status": "incomplete", "detail": "Last execution did not record success."}
    stale = (value.get("inputs") != inputs or value.get("outputs") != output_digest(root, client, step) or
             (profile is not None and value.get("profile") != profile) or
             recorded > datetime.now(timezone.utc))
    if step == "preview":
        try:
            plan = PreviewPlan.model_validate(value.get("plan"))
            recorded_target = GatewayTarget.model_validate(value.get("target"))
        except ValueError:
            return {**result, "status": "invalid", "detail": "Invalid recorded preview plan/target; rerun the preview."}
        current_target = GatewayTarget.model_validate(target) if target is not None else None
        stale = stale or current_target is None
        if current_target is not None:
            stale = stale or (recorded_target.resource_id.casefold() != current_target.resource_id.casefold() or
                              recorded_target.tenant != current_target.tenant or
                              recorded_target.account.casefold() != current_target.account.casefold())
        stale = stale or datetime.now(timezone.utc) - recorded >= timedelta(minutes=5)
    if stale:
        return {**result, "status": "stale", "detail": "Inputs, outputs, kit, profile, target or freshness changed; rerun."}
    result.update(status="recorded-current", detail="Recorded local success; not authorization.", recordedAt=value["recordedAt"])
    if step == "preview":
        result["plan"] = plan.model_dump(mode="json", by_alias=True)
    return result


def with_progress(root: Path, status: WorkflowStatus) -> WorkflowStatus:
    client = status.client
    if client is None:
        return status.model_copy(update={"completion": "not-started", "progress_notice": NOTICE})
    inputs = input_digest(root, client)
    profile = status.profile or status.provisional_profile
    target = status.evidence.target.model_dump(mode="json", by_alias=True) if status.evidence else None
    manifest = safe_data_path(root, root / "clients" / client / "mcp-manifest.yaml")
    spec = safe_data_path(root, root / "docs" / client / "spec.md")
    steps = [
        {"id": "manifest", "status": "present" if manifest.is_file() else "missing",
         "detail": "Presence is not validation."},
        {"id": "specification", "status": "present" if spec.is_file() and spec.stat().st_size else "missing",
         "detail": "Presence is not semantic review or operator approval."},
    ]
    scenario_records = None
    if manifest.is_file() and not status.blockers:
        from .scenario import check_spec, inventory
        scenario_records = inventory(root, client)
        check = check_spec(root, client, scenario_records)
        steps[1].update(
            status={"missing": "missing", "mismatch": "invalid", "consistent": "present"}[check.status],
            detail=(check.notice if check.status == "consistent" else
                    "Run scenario-contract for exact imported assertions. " +
                    " ".join(f"Line {item.line or '-'}: {item.message}" for item in check.issues)))
    steps.extend(read_step(root, client, name, inputs) for name in ("build", "build-policy"))
    prepared = read_step(root, client, "prepare", inputs, profile=profile)
    preview = read_step(root, client, "preview", inputs, profile=profile, target=target)
    steps.extend([prepared, preview])
    commands = []
    completion = "local-incomplete"
    stage = status.next_stage
    next_action = status.next_action
    if status.blockers:
        completion = "blocked"
    elif any(key in status.missing_inputs for key in ("requires_mcp", "gateway_mode")):
        pass
    elif status.evidence_status == "stale":
        # Do not regenerate for a guessed Consumption profile merely because a
        # previously observed native gateway's evidence expired.
        stage = "inspect-gateway"
    elif manifest.is_file() and steps[1]["status"] in {"missing", "invalid"}:
        stage = "define-scenario"
        next_action = (f"Call scenario-contract(client='{client}') for exact imported operations and issues. "
                       f"Correct and review docs/{client}/spec.md with local-write approval; "
                       "do not run prepare or preview until its contract assertions match.")
        from .scenario import SpecSyncConflict, plan_spec_sync
        try:
            sync = plan_spec_sync(root, client, scenario_records)
        except SpecSyncConflict as error:
            next_action += " " + str(error)
        else:
            if sync.changed:
                commands = ["mcp-kit", "--workspace", str(root), "spec-sync", client, "--write"]
                next_action = (
                    f"Preview mcp-kit spec-sync {client} (or scenario-contract.specSync), then approve local writes "
                    "before nextInvocation generates only the contract block. Preserve and review the narrative; "
                    "generation is not scenario approval. Call workflow-status again after syncing and narrative edits.")
    elif manifest.is_file() and profile and prepared["status"] != "recorded-current":
        stage = "prepare"
        commands = ["mcp-kit", "--workspace", str(root), "prepare", f"clients/{client}", "--profile", profile]
        next_action = ("Follow currentStep, obtain local-write approval, then run nextCommand. "
                       "The profile is provisional unless gateway evidence is verified.")
    elif prepared["status"] == "recorded-current":
        completion = "preview-required"
        if stage == "provision-resource-group" and status.provisioning_target and profile:
            t = status.provisioning_target
            if t.resource_group_location is None:
                raise ValueError("New resource-group preview requires its explicit location")
            commands = [
                "mcp-kit", "--workspace", str(root), "provision-group",
                "--account", t.account, "--subscription", t.subscription, "--tenant", t.tenant,
                "--resource-group", t.resource_group, "--location", t.resource_group_location,
            ]
            next_action = (
                "Obtain separate approval of the complete account, subscription, resource-group name/location "
                "and planned APIM profile/costs; azd is not used. Run nextInvocation for a create-only "
                "resource-group preview, not creation. Review its exact plan before apply. Only after "
                "verified group creation, change provisioning_target.resource_group_mode to existing and "
                "remove resource_group_location, keeping gateway_mode=new. The next APIM preview rechecks "
                "the group in Azure; this proposal and any local receipt are not inspection evidence.")
        elif stage == "provision-gateway" and status.provisioning_target and profile:
            t = status.provisioning_target
            commands = [
                "mcp-kit", "--workspace", str(root), "provision",
                "--account", t.account, "--subscription", t.subscription, "--tenant", t.tenant,
                "--resource-group", t.resource_group, "--apim-name", t.apim_name,
                "--location", t.location, "--publisher-name", t.publisher_name,
                "--publisher-email", t.publisher_email, "--profile", profile,
            ]
            next_action = (
                "Obtain separate approval of the complete proposed account, target, publisher, profile and costs, "
                "then run nextInvocation for a create-only provisioning preview. It does not create Azure resources. "
                "Review the actual plan before any apply; afterwards inspect the created gateway as existing.")
        elif status.status == "ready-for-preview" and status.evidence:
            stage = "preview"
            t = status.evidence.target
            commands = ["mcp-kit", "--workspace", str(root), "deploy", f"clients/{client}", "--subscription", t.subscription,
                        "--tenant", t.tenant, "--resource-group", t.resource_group,
                        "--apim-name", t.apim_name, "--profile", status.profile]
            next_action = ("Local preparation is recorded, but the workflow is NOT complete. "
                           "Obtain preview/context approval, then run exactly nextCommand: deploy defaults to "
                           "preview, with NO --preview flag. For a noninteractive host, --confirm-subscription "
                           "is allowed only after the operator approves the complete displayed context.")
            if preview["status"] == "recorded-current":
                stage = "review-plan"
                completion = "preview-recorded"
                commands = []
                next_action = ("A matching local preview is recorded. Present the plan for operator review; "
                               "do not claim deployment or invoke apply without separate exact-plan approval.")
    public_status = status.status
    actions = list(status.allowed_actions)
    if prepared["status"] != "recorded-current":
        actions = [action for action in actions
                   if action not in {"provision-preview", "provision-group-preview"}]
    pending = list(status.checks_pending)
    if prepared["status"] == "recorded-current":
        pending = [check for check in pending if check != "Contract validation"]
        pending.insert(0, "Operator review of specification and mock examples")
    if completion == "preview-recorded":
        pending = ["Operator review of specification and mock examples",
                   "Operator review of recorded reconciliation and what-if plan",
                   "Separate operator approval of the exact plan", "Fresh deployment checks before apply"]
    if status.status == "ready-for-preview":
        if completion == "local-incomplete":
            public_status = "ready-for-preparation"
            actions = [action for action in actions if action != "deploy-preview"]
        elif completion == "preview-recorded":
            public_status = "preview-recorded"
    return status.model_copy(update={
        "status": public_status, "allowed_actions": actions,
        "checks_pending": pending,
        "completion": completion, "steps": [WorkflowStep.model_validate(item) for item in steps], "next_command": commands,
        "next_action": next_action, "progress_notice": NOTICE,
        "next_stage": stage,
        "next_invocation": CliInvocation(
            executable=str(Path(sys.executable).absolute()),
            arguments=["-I", "-m", "mcp_openapi_creator_kit.cli", *commands[1:]],
            cwd=str(root), cli_arguments=commands[3:],
            effect=("deployment-preview" if commands[3] == "deploy" else
                    "provisioning-preview" if commands[3] in {"provision", "provision-group"} else "local-write"),
        ) if commands else None,
    })
