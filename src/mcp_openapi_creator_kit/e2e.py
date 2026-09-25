"""Explicit installed-package acceptance on one exclusively used test gateway."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import uuid

import yaml

from .assets import kit_root, verify_assets
from .data_paths import safe_data_path, workspace_root
from .runtime import child_command, command

PROFILE = "policy-mcp-consumption"
OPERATIONS = ("get-customer-context", "get-open-cases", "create-reschedule-request")
CONTEXT_FIELDS = ("account", "tenant", "subscription", "resource_group", "location",
                  "publisher_name", "publisher_email")
NOTICE = (
    "Dedicated temporary test APIM only. The operator must prevent all other writers "
    "and reuse until cleanup finishes. Whole-service deletion is authorized separately "
    "from ordinary provisioning; there is no atomic concurrent-change protection. "
    "The resource group and ARM deployment history remain. No purge. "
    "Process/machine loss or uncertain creation can require operator recovery."
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _installed(expected: str | None = None) -> dict:
    info = verify_assets()
    if info["source"] != "installed-package":
        raise ValueError("E2E requires an installed wheel in a dedicated virtual environment, not source/editable code.")
    if expected is not None and (not re.fullmatch(r"[0-9a-f]{64}", expected)
                                 or info["manifestSha256"] != expected):
        raise ValueError("Installed kit manifest SHA256 differs from the approved candidate.")
    return info


def _new_directory(root: Path, output: Path) -> Path:
    candidate = output.expanduser().absolute()
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("E2E directory must not traverse a symlink/junction.")
    if candidate.exists() or not candidate.parent.is_dir():
        raise ValueError("Choose a new E2E directory with an existing parent; runs are never overwritten.")
    candidate = candidate.resolve()
    import sys
    for protected in (root.resolve(), kit_root().resolve(), Path(sys.prefix).resolve()):
        if candidate.is_relative_to(protected) or protected.is_relative_to(candidate):
            raise ValueError("E2E directory must not overlap customer data, kit assets or the installation.")
    return candidate


def _save(directory: Path, report: dict):
    path = safe_data_path(directory, directory / "run.json")
    report["updatedAtUtc"] = _now()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".run-", suffix=".tmp",
                                         delete=False, mode="wb") as stream:
            temporary = Path(stream.name)
            stream.write(_json(report))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_json(root: Path, path: Path) -> dict:
    path = safe_data_path(root, path)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Missing or oversized E2E record; preserve the run for operator inspection.")
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid E2E record.")
    return value


def _load(directory: Path, creation_kit: Path | None = None) -> tuple[Path, dict]:
    # Check ancestors too: a moved/aliased record must not select another workspace.
    original = directory.expanduser().absolute()
    for part in (original, *original.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("E2E directory must not traverse a symlink/junction.")
    directory = workspace_root(original)
    report = _read_json(directory, directory / "run.json")
    if (report.get("schemaVersion") != 1 or report.get("kind") != "ephemeral-apim-e2e"
            or report.get("directory") != str(directory)):
        raise ValueError("E2E record identity/path changed; automatic resume is not supported.")
    if (not all(isinstance(report.get(name), str) for name in ("runId", "client", "apimName", "profile", "status"))
            or not isinstance(report.get("kit"), dict)
            or not isinstance(report["kit"].get("manifestSha256"), str)
            or not isinstance(report.get("stages"), list)):
        raise ValueError("Incomplete E2E record.")
    run_id = str(uuid.UUID(report["runId"]))
    if run_id != report["runId"] or not uuid.UUID(run_id).int:
        raise ValueError("Invalid E2E run identity.")
    scenario = report.get("scenario")
    if scenario is not None and (
        not isinstance(scenario, dict) or scenario.get("kind") != "workspace"
        or scenario.get("client") != report["client"]
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", report["client"])
    ):
        raise ValueError("Invalid isolated workspace scenario identity.")
    consumer = report.get("consumer")
    if consumer is not None and (
        not isinstance(consumer, dict) or consumer.get("kind") != "copilot-studio"
    ):
        raise ValueError("Invalid consumer stage.")
    if ((scenario is None and report["client"] != "e2e-" + uuid.UUID(run_id).hex[:12])
            or report["apimName"] != "mcp-kit-e2e-" + uuid.UUID(run_id).hex
            or report["profile"] != PROFILE):
        raise ValueError("E2E target identity differs from the dedicated reference scenario.")
    if creation_kit is None:
        _installed(report["kit"]["manifestSha256"])
    else:
        _installed()
        from .ephemeral_gateway import verify_creation_kit
        verify_creation_kit(creation_kit, report["kit"])
    root = workspace_root(safe_data_path(directory, directory / "workspace"))
    command("local_python").local_python(root)
    return root, report


@contextmanager
def _provisioner(root: Path):
    module = command("provision-gateway")
    previous = module.REPO_ROOT
    module.REPO_ROOT = root
    try:
        yield module
    finally:
        module.REPO_ROOT = previous


def _execute(root: Path, name: str, *arguments: str, timeout: int = 300) -> str:
    # Reuse the established bounded Windows/POSIX process-tree transport.
    with _provisioner(root) as module:
        return module.run(child_command(root, name, *arguments), timeout=timeout)


def _reference(root: Path, client: str) -> dict[Path, bytes]:
    manifest = yaml.safe_load((kit_root() / "clients" / "sample" / "mcp-manifest.yaml").read_text("utf-8"))
    contract = yaml.safe_load((kit_root() / "apis" / "customer-care" / "openapi.yaml").read_text("utf-8"))
    manifest["client"] = client
    manifest["displayName"] = "Isolated E2E Customer Care"
    manifest["apis"][0]["name"] = "e2e-care"
    manifest["apis"][0]["mcpTools"] = [f"{client}-{name}" for name in OPERATIONS]
    paths = {}
    for path, item in contract["paths"].items():
        selected = {}
        for method, operation in item.items():
            if isinstance(operation, dict) and operation.get("operationId") in OPERATIONS:
                operation["operationId"] = client + "-" + operation["operationId"]
                selected[method] = operation
        if selected:
            paths[path] = selected
    contract["paths"] = paths
    if sum(len(item) for item in paths.values()) != 3:
        raise ValueError("Packaged customer-care reference no longer supplies exactly three E2E operations.")
    customer = paths["/v1/customer-context"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
    cases = paths["/v1/stores/{storeCode}/cases"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
    write = paths["/v1/reschedule-requests"]["post"]
    request = write["responses"]["202"]["content"]["application/json"]["example"]
    if (customer["customerId"] != "CUST-SME-001" or customer["account"]["name"] != "Sample Retail Group"
            or len(cases) != 1 or cases[0]["caseId"] != "CASE-004" or cases[0]["storeCode"] != "STORE-004"
            or request["requestId"] != "RESCHEDULE-004-001" or request["status"] != "submitted"
            or request["orderId"] != "ORDER-004" or request["requestedDate"] != "2026-08-07"
            or write["x-mock"] != [{"when": {"param": "Idempotency-Key", "missing": True},
                                   "respond": {"status": 400}}, {"respond": {"status": 202}}]):
        raise ValueError("Packaged reference differs from the independent acceptance anchors; review the fixture.")
    narrative = (
        "# Isolated customer-care acceptance\n\n"
        "A fictional service operator identifies Sample Retail Group, reads the open "
        "case for STORE-004, then explicitly confirms a simulated reschedule request "
        "for ORDER-004 on 2026-08-07. The expected customer is CUST-SME-001, case "
        "CASE-004 and request RESCHEDULE-004-001. No real customer or backend is used.\n\n"
        "Confirm before the mock write; use Idempotency-Key. The missing-key REST "
        "branch must return RFC 7807 status 400. Mocks do not persist state. "
        "Success requires exact MCP discovery, mock payload checks, REST checks "
        "and verified service cleanup; local generation alone is not success.\n"
    )
    return {
        root / "clients" / client / "mcp-manifest.yaml": yaml.safe_dump(manifest, sort_keys=False).encode(),
        root / "apis" / "e2e-care" / "openapi.yaml": yaml.safe_dump(contract, sort_keys=False).encode(),
        root / "docs" / client / "spec.md": narrative.encode(),
    }


def _snapshot(root: Path) -> dict:
    result = {}
    for name in ("clients", "apis", "docs", "catalog"):
        for path in sorted((root / name).rglob("*")):
            safe_data_path(root, path)
            if path.is_file():
                result[path.relative_to(root).as_posix()] = _hash(path.read_bytes())
    return result


def _check_reference(root: Path, report: dict):
    expected = {} if report.get("scenario") else _reference(root, report["client"])
    for path, content in expected.items():
        actual = safe_data_path(root, path).read_bytes()
        if path.name == "spec.md":
            if not actual.startswith(content):
                raise ValueError("E2E reference narrative changed.")
        elif actual != content:
            raise ValueError("E2E reference manifest/contract changed; arbitrary scenarios are not authorized.")
    if _snapshot(root) != report["artifacts"]:
        raise ValueError("E2E artifacts changed since preparation; start a new run, never broaden this one.")


def _workspace_scenario(root: Path, client: str) -> tuple[dict[Path, bytes], list[str]]:
    from .consumer_export import load_client
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", client):
        raise ValueError("Use a client slug, not a path.")
    manifest, specs = load_client(root, client)
    if (manifest.get("networkProfile", "public") != "public"
            or manifest.get("inboundAuth", {}).get("mode") != "subscriptionKey"
            or any(api.get("backend", {}).get("mode") != "mock" for api in manifest["apis"])):
        raise ValueError("E2E workspace scenarios require public, subscriptionKey, mock-only configuration.")
    paths = [Path("clients") / client / "mcp-manifest.yaml", Path("docs") / client / "spec.md"]
    paths.extend(Path("apis") / name / "openapi.yaml" for name in specs)
    canonical = safe_data_path(root, root / "apis" / "canonical-schemas.yaml")
    if canonical.exists():
        paths.append(Path("apis") / "canonical-schemas.yaml")
    files = {}
    for relative in paths:
        path = safe_data_path(root, root / relative)
        if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
            raise ValueError(f"Missing, empty or oversized scenario source: {relative.as_posix()}")
        files[relative] = path.read_bytes()
    tools = [tool for api in manifest["apis"] for tool in api.get("mcpTools", [])]
    if (not tools or any(not isinstance(tool, str) for tool in tools)
            or len(tools) != len(set(tools))):
        raise ValueError("Scenario must select a nonempty, unique MCP tool inventory.")
    return files, tools


def prepare(root: Path, output: Path, expected_kit_sha256: str,
            client: str | None = None, consumer: str | None = None) -> dict:
    info = _installed(expected_kit_sha256)
    command("local_python").local_python(root)
    directory = _new_directory(root, output)
    if consumer not in {None, "copilot-studio"}:
        raise ValueError("Only the opt-in copilot-studio consumer stage is supported.")
    source_files, selected = _workspace_scenario(root, client) if client is not None else ({}, [])
    run_id = uuid.uuid4()
    report = {
        "schemaVersion": 1, "kind": "ephemeral-apim-e2e", "directory": str(directory),
        "runId": str(run_id), "client": client if client is not None else "e2e-" + run_id.hex[:12],
        "apimName": "mcp-kit-e2e-" + run_id.hex, "profile": PROFILE, "kit": info,
        "createdAtUtc": _now(), "status": "preparing", "azure": "not-started",
        "cleanup": {"status": "not-started"}, "stages": [], "notice": NOTICE,
    }
    if client is not None:
        report["scenario"] = {
            "kind": "workspace", "client": client,
            "sourceHashes": {path.as_posix(): _hash(data) for path, data in source_files.items()},
        }
    if consumer:
        report["consumer"] = {
            "kind": consumer, "status": "not-started",
            "notice": "Manual observed evidence, not authenticated consent. After a successful run the "
                      "service remains billable until explicit cleanup; no background cleanup is scheduled.",
        }
    directory.mkdir()
    workspace = directory / "workspace"
    workspace.mkdir()
    _save(directory, report)
    try:
        outputs = ({workspace / path: data for path, data in source_files.items()}
                   if client is not None else _reference(workspace, report["client"]))
        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for name, arguments in (
            ("spec-sync", [report["client"], "--write"]),
            ("prepare", [f"clients/{report['client']}", "--profile", PROFILE]),
            ("catalog", ["--write"]),
        ):
            _execute(workspace, name, *arguments)
            report["stages"].append({"name": name, "status": "passed"})
            _save(directory, report)
        servers = _read_json(workspace, workspace / "clients" / report["client"] /
                             "generated" / "policy-mcp" / "servers.json")
        actual = [tool for server in servers["servers"] for tool in server["tools"]]
        expected = selected if client is not None else [report["client"] + "-" + name for name in OPERATIONS]
        if sorted(actual) != sorted(expected):
            raise ValueError("Generated MCP inventory differs from the selected scenario.")
        policies = list((workspace / "clients" / report["client"] / "generated" / "policy-mcp").glob("*.xml"))
        if not policies or any(not 0 < len(path.read_bytes()) <= 16384 for path in policies):
            raise ValueError("Generated MCP policy is missing or exceeds 16 KiB.")
        report.update(status="prepared", artifacts=_snapshot(workspace), expectedTools=expected,
                      dashboard="workspace/catalog/generated/catalog.html")
        _save(directory, report)
        return report
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, KeyboardInterrupt, SystemExit) as error:
        report.update(status="preparation-failed", failure={"phase": "prepare", "type": type(error).__name__})
        _save(directory, report)
        raise


def _context(module, report: dict):
    context = module.ProvisionContext(**report["target"])
    context.validate()
    if context.profile != PROFILE or context.apim_name != report["apimName"]:
        raise ValueError("E2E context escaped the exact temporary service.")
    return context


def preview(directory: Path, target: dict, confirmation: str) -> dict:
    root, report = _load(directory)
    directory = root.parent
    if report["status"] not in {"prepared", "previewed"}:
        raise ValueError("A started E2E run cannot be previewed/recreated; inspect its cleanup state.")
    if (directory / "run-attempt.json").exists():
        raise ValueError("This run has already attempted creation; no new preview/replay is allowed.")
    _check_reference(root, report)
    if set(target) != set(CONTEXT_FIELDS):
        raise ValueError("E2E preview requires the complete explicit Azure target.")
    report["target"] = {**target, "apim_name": report["apimName"], "profile": PROFILE}
    with _provisioner(root) as module:
        context = _context(module, report)
        if confirmation != context.subscription:
            raise ValueError("Explicit subscription confirmation is required before Azure inspection.")
        creation = module.provision(context, confirmation=confirmation)
    report.update(status="previewed", azure="preview-only", creationPreview=creation)
    _save(directory, report)
    return report


def _authorize(report: dict, confirmation: str, exclusive_test: bool, resource_id: str):
    target = report.get("target")
    if not isinstance(target, dict) or set(target) != {*CONTEXT_FIELDS, "apim_name", "profile"}:
        raise ValueError("A complete E2E target preview is required before execution or cleanup.")
    if (target["apim_name"] != report["apimName"] or target["profile"] != PROFILE
            or not all(isinstance(value, str) for value in target.values())):
        raise ValueError("E2E target differs from the dedicated reference service.")
    if not exclusive_test or confirmation != target["subscription"]:
        raise ValueError("Explicit exclusive-use test approval and subscription confirmation are required.")
    expected = (f"/subscriptions/{report['target']['subscription']}/resourceGroups/"
                f"{report['target']['resource_group']}/providers/Microsoft.ApiManagement/service/{report['apimName']}")
    if resource_id != expected:
        raise ValueError("Whole-service cleanup confirmation must match the exact displayed test resource ID.")


def _deploy_reference(root: Path, report: dict):
    _check_reference(root, report)
    context = report["target"]
    arguments = [f"clients/{report['client']}"]
    for field in ("subscription", "tenant", "resource_group", "apim_name", "profile"):
        arguments += ["--" + field.replace("_", "-"), context[field]]
    arguments += ["--confirm-subscription", context["subscription"]]
    _execute(root, "deploy", *arguments, timeout=900)
    from .progress import receipt_path
    receipt = _read_json(root, receipt_path(root, report["client"], "preview"))
    plan = receipt["plan"]
    if plan.get("deletions") or not plan.get("changes"):
        raise ValueError("E2E only permits a new reference client without reconciliation DELETEs.")
    client_dir = root / "clients" / report["client"]
    manifest = yaml.safe_load((client_dir / "mcp-manifest.yaml").read_text("utf-8"))
    inventory = command("deployment").template_inventory(
        SimpleNamespace(base=report["creation"]["resourceId"]), manifest, PROFILE, client_dir)
    allowed = {rid.casefold() for _, ids, _ in inventory for rid in ids}
    required = {rid.casefold() for _, _, ids in inventory for rid in ids}
    seen = set()
    for change in plan["changes"]:
        rid = change["resourceId"].casefold()
        nested = "/providers/microsoft.resources/deployments/" in rid
        kinds = {"Create", "NoChange", "Deploy"} if nested else {"Create", "NoChange"}
        if change["changeType"] not in kinds or rid not in allowed:
            raise ValueError("E2E client preview exceeds the approved creation-only scope.")
        seen.add(rid)
    if not required <= seen:
        raise ValueError("E2E client preview did not expand every required reference resource.")
    token = plan.get("reviewToken")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Missing exact client preview token.")
    report["clientPreview"] = plan
    _save(root.parent, report)
    _check_reference(root, report)
    _execute(root, "deploy", *arguments, "--yes", "--review-token", token, timeout=1800)


def _verify(root: Path, report: dict):
    with _provisioner(root) as module:
        context = _context(module, report)
        module.verify_context(context)
        # This exact run-owned product subscription is the only secret read.
        uri = (f"{context.resource_id}/subscriptions/{report['client']}-pilot/"
               "listSecrets?api-version=2024-05-01")
        secret = module.rest(context, "POST", uri)
    key = secret.get("primaryKey")
    if not isinstance(key, str) or not key or re.search(r"[\x00-\x20\x7f]", key):
        raise ValueError("Run-owned subscription key could not be read; credentials are never logged.")
    previous = os.environ.get("MCP_KEY")
    try:
        os.environ["MCP_KEY"] = key
        gateway = report["creation"]["gatewayUrl"]
        _execute(root, "verify-rest", f"clients/{report['client']}", "--gateway-url", gateway)
        _execute(root, "verify-mcp", f"clients/{report['client']}", "--gateway-url", gateway,
                 "--profile", PROFILE)
        _verify_missing_auth(root, report)
    finally:
        if previous is None:
            os.environ.pop("MCP_KEY", None)
        else:
            os.environ["MCP_KEY"] = previous


def _verify_missing_auth(root: Path, report: dict):
    from ._commands.verification import endpoint_url
    verifier = command("verify-rest")
    previous = verifier.REPO_ROOT
    verifier.REPO_ROOT = root
    try:
        manifest = yaml.safe_load((root / "clients" / report["client"] / "mcp-manifest.yaml").read_text("utf-8"))
        paths = {base + case[0] for _, _, base, method, case in verifier.iter_cases(manifest)
                 if method.upper() == "GET"}
        servers = command("verify-mcp").expected_policy_servers(root / "clients" / report["client"])
        requests = [(path, "GET", None) for path in sorted(paths)]
        requests += [(path, "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                     for path, _ in servers.values()]
        for path, method, body in requests:
            status, _, _ = verifier.invoke(endpoint_url(report["creation"]["gatewayUrl"], path),
                                            method, {}, body)
            if status != 401:
                raise ValueError("Unauthenticated reference REST/MCP request did not return HTTP 401.")
    finally:
        verifier.REPO_ROOT = previous


def _cleanup_failure(error: BaseException) -> dict:
    from .ephemeral_gateway import CleanupError
    if isinstance(error, CleanupError):
        return {**error.report, "operatorActionRequired": True}
    return {"status": "failed", "errorType": type(error).__name__, "operatorActionRequired": True}


def _consumer_evidence(root: Path, report: dict, path: Path) -> tuple[dict, dict[str, bytes]]:
    evidence = _read_json(root, path)
    fields = {"schemaVersion", "runId", "resourceId", "environmentId", "agentId",
              "observedAtUtc", "observations"}
    if (set(evidence) != fields or evidence["schemaVersion"] != 1
            or evidence["runId"] != report["runId"]
            or evidence["resourceId"] != report["creation"]["resourceId"]):
        raise ValueError("Consumer evidence must identify this exact run and service.")
    for field in ("environmentId", "agentId"):
        value = evidence[field]
        if isinstance(value, str) and field == "environmentId":
            value = value.removeprefix("Default-")
        if not isinstance(value, str) or str(uuid.UUID(value)) != value or not uuid.UUID(value).int:
            raise ValueError("Consumer environmentId/agentId must be canonical nonzero UUIDs (Default- is supported for environments).")
    observed = datetime.fromisoformat(evidence["observedAtUtc"])
    retained = datetime.fromisoformat(report["consumer"]["retainedAtUtc"])
    if (observed.tzinfo is None or retained.tzinfo is None
            or not retained <= observed <= datetime.now(timezone.utc)):
        raise ValueError("Consumer observation must be timezone-aware, after retention and not in the future.")
    observations = evidence["observations"]
    if not isinstance(observations, list) or len(observations) != len(report["expectedTools"]):
        raise ValueError("Provide one observed consumer result for every selected tool.")
    tools = []
    traces = {}
    for observation in observations:
        if (not isinstance(observation, dict)
                or set(observation) != {"operationId", "outcome", "trace", "sha256"}
                or observation["outcome"] not in ("passed", "failed")):
            raise ValueError("Each consumer observation needs operationId, outcome, trace and sha256.")
        tool = observation["operationId"]
        if not isinstance(tool, str):
            raise ValueError("Invalid observed operationId.")
        tools.append(tool)
        name = observation["trace"]
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.(txt|json|md)", name)
                or name.casefold() == "observation.json"
                or any(name != prior and name.casefold() == prior.casefold() for prior in traces)):
            raise ValueError("Trace must be a local text/JSON/Markdown filename, not a path or URL.")
        trace = safe_data_path(root, path.parent / name)
        if not trace.is_file() or not 0 < trace.stat().st_size <= 1024 * 1024:
            raise ValueError("Consumer trace is missing, empty or exceeds 1 MiB.")
        data = trace.read_bytes()
        if not data.strip() or _hash(data) != observation["sha256"]:
            raise ValueError("Consumer trace hash does not match the supplied observation.")
        data.decode("utf-8")
        traces[name] = data
    if sorted(tools) != sorted(report["expectedTools"]):
        raise ValueError("Consumer evidence must cover the exact selected tools without duplicates.")
    return evidence, traces


def record_consumer(directory: Path, evidence_path: Path) -> dict:
    root, report = _load(directory)
    if (report["status"] != "awaiting-consumer" or not report.get("consumer")
            or report.get("transportPassed") is not True):
        raise ValueError("Consumer evidence requires a successful retained run awaiting its consumer.")
    _check_reference(root, report)
    source = evidence_path.expanduser().absolute()
    if any(part.is_symlink() or part.is_junction() for part in (source, *source.parents)):
        raise ValueError("Consumer evidence must not traverse a symlink/junction.")
    # Evidence is explicitly supplied, never discovered from other host sessions.
    evidence_root = workspace_root(source.parent)
    evidence, traces = _consumer_evidence(evidence_root, report, source)
    destination = safe_data_path(root.parent, root.parent / "consumer-evidence")
    destination.mkdir()
    for name, data in {"observation.json": _json(evidence), **traces}.items():
        with safe_data_path(root.parent, destination / name).open("xb") as stream:
            stream.write(data)
    passed = all(item["outcome"] == "passed" for item in evidence["observations"])
    report["consumer"].update(
        status="recorded", outcome="observed-passed" if passed else "observed-failed",
        recordedAtUtc=_now(), evidenceSha256=_hash(_json(evidence)),
    )
    report.update(status="consumer-recorded", testPassed=passed)
    _save(root.parent, report)
    return report


def cleanup_run(directory: Path, confirmation: str, exclusive_test: bool, resource_id: str,
                creation_kit: Path | None = None) -> dict:
    root, report = _load(directory, creation_kit)
    _authorize(report, confirmation, exclusive_test, resource_id)
    if "creation" not in report:
        raise ValueError("No verified creation receipt. Creation may be uncertain: inspect manually; do not adopt or retry.")
    consumer = report.get("consumer")
    if consumer and consumer.get("status") == "recorded":
        try:
            evidence, _ = _consumer_evidence(root.parent, report, root.parent / "consumer-evidence" / "observation.json")
            if _hash(_json(evidence)) != consumer["evidenceSha256"]:
                raise ValueError("Recorded consumer evidence changed.")
            report["testPassed"] = (report.get("transportPassed") is True
                                    and all(item["outcome"] == "passed" for item in evidence["observations"]))
        except (ValueError, OSError, KeyError, TypeError) as error:
            # Invalid evidence must not prevent approved service cleanup or become a pass.
            report["testPassed"] = False
            report["status"] = "failed"
            report["failure"] = {"phase": "consumer-evidence", "type": type(error).__name__}
    from .ephemeral_gateway import cleanup
    report["cleanup"] = {"status": "running"}
    if creation_kit is not None:
        report["cleanupRuntime"] = _installed()
    _save(root.parent, report)
    try:
        provenance = {"creation_kit": creation_kit, "creation_kit_info": report["kit"]} if creation_kit is not None else {}
        report["cleanup"] = cleanup(root, report["creation"], run_id=report["runId"],
                                    confirm_resource_id=resource_id, exclusive_test=True, **provenance)
        if report["cleanup"].get("status") != "deleted" or report["cleanup"].get("serviceAbsent") is not True:
            raise RuntimeError("Cleanup did not establish service absence.")
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, KeyboardInterrupt, SystemExit) as error:
        report["cleanup"] = _cleanup_failure(error)
        report["status"] = "failed"
        _save(root.parent, report)
        raise
    if report["status"] in {"running", "cleaning"}:
        report["status"] = "passed" if report.get("testPassed") is True and "failure" not in report else "failed"
    elif consumer and report["status"] in {"awaiting-consumer", "consumer-recorded"}:
        if consumer.get("status") != "recorded":
            report.update(status="incomplete-cleaned", testPassed=False)
        else:
            report["status"] = "passed" if report.get("testPassed") is True and "failure" not in report else "failed"
    _save(root.parent, report)
    return report


def run(directory: Path, confirmation: str, review_token: str,
        exclusive_test: bool, resource_id: str, retain_for_consumer: bool = False) -> dict:
    root, report = _load(directory)
    if report["status"] != "previewed":
        raise ValueError("Run requires a fresh provisioning preview; started runs are never replayed.")
    _authorize(report, confirmation, exclusive_test, resource_id)
    if retain_for_consumer != bool(report.get("consumer")):
        raise ValueError("Consumer runs require explicit --retain-for-consumer; reference runs cannot be retained implicitly.")
    _check_reference(root, report)
    if not review_token or review_token != report["creationPreview"]["reviewToken"]:
        raise ValueError("Missing/stale provisioning review token; nothing was created.")
    attempt = safe_data_path(root.parent, root.parent / "run-attempt.json")
    with attempt.open("xb") as stream:
        stream.write(_json({"runId": report["runId"], "resourceId": resource_id, "recordedAtUtc": _now()}))
        stream.flush()
        os.fsync(stream.fileno())
    report.update(status="running", azure="creating", testPassed=False)
    _save(root.parent, report)
    phase = "provision"
    failure = None
    retained = False
    try:
        with _provisioner(root) as module:
            context = _context(module, report)
            report["creation"] = module.provision(
                context, confirmation=confirmation, apply=True, review_token=review_token)
        report["azure"] = "created"
        report["stages"].append({"name": "provision", "status": "passed"})
        _save(root.parent, report)
        phase = "deploy"
        _deploy_reference(root, report)
        report["azure"] = "deployed"
        report["stages"].append({"name": "deploy", "status": "passed"})
        _save(root.parent, report)
        phase = "verify"
        _verify(root, report)
        report["transportPassed"] = True
        report["testPassed"] = not retain_for_consumer
        report["stages"].append({"name": "verify-rest-mcp-and-auth", "status": "passed"})
        if retain_for_consumer:
            report["consumer"]["status"] = "awaiting-observation"
            report["consumer"]["retainedAtUtc"] = _now()
            report["status"] = "awaiting-consumer"
            _save(root.parent, report)
            retained = True
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, http.client.HTTPException,
            KeyboardInterrupt, SystemExit) as error:
        # Persist only a bounded classification, never provider output/credentials.
        failure = error
        from .diagnostics import failure_record
        report["failure"] = failure_record(error, phase)
    finally:
        if not retained:
            report["status"] = "cleaning" if "creation" in report else "creation-uncertain"
            _save(root.parent, report)
            if "creation" in report:
                try:
                    cleaned = cleanup_run(root.parent, confirmation, exclusive_test, resource_id)
                    report["cleanup"] = cleaned["cleanup"]
                except (ValueError, RuntimeError, OSError, KeyError, TypeError, KeyboardInterrupt, SystemExit) as error:
                    report["cleanup"] = _cleanup_failure(error)
                    failure = failure or error
            else:
                report["cleanup"] = {"status": "blocked", "operatorActionRequired": True,
                                     "reason": "No verified creation receipt; no automatic adoption/deletion."}
            report["status"] = "passed" if report["testPassed"] and failure is None else "failed"
            _save(root.parent, report)
    if failure is not None:
        raise RuntimeError("E2E failed; inspect run.json for the stage and cleanup outcome. "
                           "Raw provider output is suppressed; do not automatically retry creation.") from failure
    return report


@contextmanager
def _exclusive_run(directory: Path, creation_kit: Path | None = None):
    root, _ = _load(directory, creation_kit)
    path = safe_data_path(root.parent, root.parent / "runner.lock")
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Another process is operating on this E2E run; do not run cleanup concurrently.") from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("Another process is operating on this E2E run; do not run cleanup concurrently.") from error
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def main(root: Path, arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mcp-kit e2e", description=NOTICE)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare_parser = commands.add_parser("prepare", help="Write one new isolated mock run; default is the three-tool reference; no Azure.")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--expected-kit-sha256", required=True,
                                help="manifestSha256 from the reviewed installed mcp-kit info.")
    prepare_parser.add_argument("--client", help="Snapshot this existing workspace client instead of the built-in reference.")
    prepare_parser.add_argument("--consumer", choices=["copilot-studio"],
                                help="Plan an explicit retained consumer stage; no Studio changes are made.")
    consumer_parser = commands.add_parser("record-consumer", help="Record supplied observed evidence; does not execute Studio or authenticate consent.")
    consumer_parser.add_argument("--run-directory", type=Path, required=True)
    consumer_parser.add_argument("--evidence", type=Path, required=True)
    for name in ("preview", "run", "cleanup", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-directory", type=Path, required=True)
        if name == "preview":
            for field in CONTEXT_FIELDS:
                sub.add_argument("--" + field.replace("_", "-"), required=True)
        if name != "status":
            sub.add_argument("--confirm-subscription", required=True)
        if name in {"run", "cleanup"}:
            sub.add_argument("--approve-exclusive-test", action="store_true")
            sub.add_argument("--confirm-resource-id", required=True)
        if name == "run":
            sub.add_argument("--review-token", required=True)
            sub.add_argument("--retain-for-consumer", action="store_true",
                             help="Leave a successfully verified consumer run live and billable until explicit cleanup.")
        if name == "cleanup":
            sub.add_argument("--creation-kit", type=Path,
                             help="Original installed mcp_openapi_creator_kit package directory; verify as data for recovery after upgrade.")
    args = parser.parse_args(arguments)
    guard = _exclusive_run(args.run_directory, getattr(args, "creation_kit", None)) if args.action in {"preview", "run", "cleanup", "record-consumer"} else nullcontext()
    with guard:
        if args.action == "prepare":
            result = prepare(root, args.output, args.expected_kit_sha256, args.client, args.consumer)
        elif args.action == "preview":
            result = preview(args.run_directory, {field: getattr(args, field) for field in CONTEXT_FIELDS},
                             args.confirm_subscription)
        elif args.action == "status":
            _, result = _load(args.run_directory)
        elif args.action == "cleanup":
            result = cleanup_run(args.run_directory, args.confirm_subscription,
                                 args.approve_exclusive_test, args.confirm_resource_id, args.creation_kit)
        elif args.action == "record-consumer":
            result = record_consumer(args.run_directory, args.evidence)
        else:
            result = run(args.run_directory, args.confirm_subscription, args.review_token,
                         args.approve_exclusive_test, args.confirm_resource_id, args.retain_for_consumer)
    print(json.dumps(result, indent=2))
    return 0
