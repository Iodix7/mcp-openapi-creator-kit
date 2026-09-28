"""Offline consumer connection candidates, derived from contracts, never cloud evidence."""
from __future__ import annotations

from pathlib import Path

from .consumer_export import load_client, selected_operations
from .data_paths import validate_data_tree
from .policy import build_client_plan, desired_groups
from .runtime import command
from .targets import parse_targets
from .rest_runtime import (
    EXTENSION, build_runtime_policy, key_header, validate_profile_runtime, contract_runtime)

PROFILES = ("native-mcp", "policy-mcp-consumption", "rest-consumption")


def consumer_handoff(root: Path, client: str, profile: str,
                     gateway_url: str | None = None) -> dict:
    if profile not in PROFILES:
        raise ValueError("Choose an explicit supported gateway profile for consumer handoff.")
    validate_data_tree(root)
    manifest, specs = load_client(root, client)
    try:
        manifest = command("build-facade").validate_manifest(manifest, client)
    except SystemExit:
        raise ValueError("Invalid manifest; run mcp-kit build clients/" + client +
                         " for field diagnostics before consumer handoff.") from None
    targets = parse_targets(manifest)
    validate_profile_runtime(manifest, specs, profile)
    for api in manifest["apis"]:
        try:
            contract_runtime(specs[api["name"]], api, manifest, command("build-facade"))
            if EXTENSION in specs[api["name"]]:
                build_runtime_policy(specs[api["name"]], api, manifest, command("build-facade"))
        except SystemExit:
            raise ValueError("Invalid runtime contract; run build for field diagnostics.") from None
    if targets.gateway != "existing-apim":
        raise ValueError("Consumer handoff supports classic APIM only; use target-report for this target.")
    if profile != "native-mcp" and (
            manifest.get("networkProfile", "public") != "public" or
            any(api.get("backend", {}).get("mode") != "mock" for api in manifest["apis"])):
        raise ValueError("Consumption handoff requires public mock-only APIs.")
    if any(api.get("backend", {}).get("mode") not in {"mock", "external"}
           for api in manifest["apis"]):
        raise ValueError("Handoff requires an explicit supported backend mode for every API.")
    records = selected_operations(manifest, specs)
    verification = command("verification")
    origin = verification.gateway_origin(gateway_url) if gateway_url is not None else None
    inbound = manifest.get("inboundAuth", {}).get("mode", "subscriptionKey")
    if inbound not in {"subscriptionKey", "entraJwt"}:
        raise ValueError("Unsupported inbound authentication mode.")
    servers = []
    if profile == "policy-mcp-consumption":
        try:
            plan = build_client_plan(root, root / "clients" / client)
        except SystemExit:
            raise ValueError("Invalid policy MCP manifest; correct the build diagnostics before handoff.") from None
        for server in plan["servers"]:
            servers.append({"basePath": server["path"], "operationPath": "/mcp",
                            "endpointPath": server["endpointPath"], "tools": server["tools"],
                            "sizeBytes": server["sizeBytes"]})
    else:
        tools_by_api = {api["name"]: api["mcpTools"] for api in manifest["apis"]}
        for name, tools in desired_groups(manifest, tools_by_api):
            base = f"{client}/{name}" + ("-mcp" if profile == "native-mcp" else "")
            servers.append({"basePath": base,
                            "operationPath": "/mcp" if profile == "native-mcp" else None,
                            "endpointPath": base + ("/mcp" if profile == "native-mcp" else ""),
                            "tools": tools if profile == "native-mcp" else []})
    for server in servers:
        server["url"] = verification.endpoint_url(origin, server["endpointPath"]) if origin else None
    backends = {api["name"]: api["backend"]["mode"] for api in manifest["apis"]}
    operations = []
    for record in records:
        operation = record["operation"]
        generated_key = profile == "policy-mcp-consumption" and any(
            p["in"] == "header" and p["name"].lower() == "idempotency-key"
            for p in operation.get("parameters", []))
        operations.append({
            "tool": record["operationId"], "backendMode": backends[record["api"]],
            "persistence": "none" if backends[record["api"]] == "mock" else "not-verified",
            "idempotencyKey": "gateway-generated-per-call" if generated_key else "consult-consumer-schema",
            "callerKeyReuse": "not-supported" if generated_key else "not-verified",
            "deduplication": "not-guaranteed",
            "restResponseStatuses": list(map(str, operation["responses"])),
        })
    return {
        "client": client, "profile": profile, "status": "derived-not-verified",
        "runtimeSummary": (
            "NO: a fixed UUID in chat cannot prevent duplicate retries. Policy MCP generates "
            "a new Idempotency-Key internally for each call that needs it; caller key reuse "
            "is not supported. The stateless mock neither persists nor deduplicates requests. "
            "A required REST header is not a caller-controlled MCP argument."
            if profile == "policy-mcp-consumption" else
            "Mock operations neither persist nor deduplicate requests. External backend "
            "persistence and deduplication remain unverified; consult the actual consumer "
            "schema before claiming caller control of headers."
        ),
        "transport": "REST/OpenAPI" if profile == "rest-consumption" else "MCP Streamable HTTP",
        "gatewayOrigin": origin, "endpoints": servers, "operations": operations,
        "authentication": {"mode": inbound, "requiredHeaders": [
            key_header(manifest), *(["Authorization"] if inbound == "entraJwt" else [])],
            "credentialsIncluded": False},
        "restRuntime": [
            {"api": api["name"], "version": 1,
             "correlationHeader": specs[api["name"]][EXTENSION]["correlation"]["header"],
             "authorization": "API-scope-valid subscription must pass explicit subscription-ID allowlist",
             "rateLimit": api["runtime"]["rateLimit"],
             "simulation": api["runtime"].get("simulate"),
             "acceptance": "Not verified; inherited gateway policies and pre-routing errors need live acceptance"}
            for api in manifest["apis"] if "runtime" in api],
        "notice": "Offline candidates, not deployment, connection or approval evidence. "
                  "Use only the complete URL, not basePath, in an MCP consumer. "
                  "REST URLs cannot be entered in an MCP wizard.",
        "responseRules": [
            "In the final answer, label URLs as derived offline from contracts: deployment and "
            "connection have not been verified. Keep this warning even in a brief answer.",
            "Mock results are simulations; never claim persistence or business approval.",
            "A UUID in chat is not evidence that a caller header was transmitted or reused.",
            "MCP JSON-RPC success is not a REST response status or proof of deduplication.",
            "Successful tool calls and truthful chat explanations require separate checks.",
        ],
        "nextAction": ("Supply the approved HTTPS gateway origin to consumer-handoff."
                       if origin is None else
                       "Review the complete URLs and runtime limits; perform separately approved "
                       "protocol and consumer checks. Do not reconstruct paths or retry writes automatically."),
    }


def handoff_candidates(root: Path, client: str) -> list[dict]:
    reports = []
    for profile in PROFILES:
        try:
            reports.append(consumer_handoff(root, client, profile))
        except ValueError as error:
            reports.append({"client": client, "profile": profile, "status": "blocked",
                            "reason": str(error)})
        except OSError:
            reports.append({"client": client, "profile": profile, "status": "blocked",
                            "reason": "Missing or unreadable customer contract; complete local data before handoff."})
    return reports
