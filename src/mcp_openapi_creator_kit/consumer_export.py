"""Offline projections. Canonical contracts are never modified or dereferenced online."""
from __future__ import annotations

import copy
import json
import re
from importlib.resources import files
from pathlib import Path

import yaml
from jsonschema import Draft4Validator, FormatChecker

from .policy import HTTP_VERBS
from .targets import TargetError, https_url, parse_targets, target_capabilities


def json_bytes(value) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                           allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise TargetError("Artifacts require finite, acyclic JSON data") from None


def contained(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or relative.startswith("/")
            or any(p in ("", ".", "..") for p in relative.split("/"))):
        raise TargetError("Expected a workspace-relative path without traversal")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise TargetError("Input path escapes workspace (including symlinks)")
    return path


def load_client(root: Path, client: str) -> tuple[dict, dict]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", client):
        raise TargetError("Invalid client slug")
    manifest = yaml.safe_load(contained(root, f"clients/{client}/mcp-manifest.yaml").read_text("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("client") != client:
        raise TargetError("Manifest client must match its directory")
    parse_targets(manifest)
    specs = {}
    apis = manifest.get("apis")
    if not isinstance(apis, list) or not apis:
        raise TargetError("Manifest requires non-empty apis")
    for api in apis:
        name = api.get("name") if isinstance(api, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise TargetError("Invalid API slug")
        if name in specs:
            raise TargetError("Duplicate API name")
        specs[name] = yaml.safe_load(contained(root, f"apis/{name}/openapi.yaml").read_text("utf-8"))
    return manifest, specs


_PROJECTION_CHILDREN = {
    "operation": {"parameters": "parameter", "requestBody": "requestBody",
                  "responses": "map:response", "callbacks": "map:callback"},
    "schema": {"properties": "map:schema", "items": "schema",
               "additionalProperties": "schema", "allOf": "schema",
               "anyOf": "schema", "oneOf": "schema", "not": "schema"},
    "parameter": {"schema": "schema", "content": "map:media", "examples": "map:example"},
    "requestBody": {"content": "map:media"},
    "response": {"content": "map:media", "headers": "map:parameter", "links": "map:link"},
    "media": {"schema": "schema", "examples": "map:example", "encoding": "map:encoding"},
    "encoding": {"headers": "map:parameter"},
}
_PROJECTION_LITERALS = {
    "schema": {"example", "default", "enum"},
    "parameter": {"example"},
    "media": {"example"},
    "example": {"value"},
}


def inline_local(spec: dict, value, stack=(), budget=None, *, context="schema"):
    """Bounded local-only projection; cycles and external refs fail, never fetch."""
    budget = [50000] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or len(stack) > 64:
        raise TargetError("Reference projection exceeds safe size/depth budget")
    if isinstance(value, list):
        return [inline_local(spec, v, stack + ("[]",), budget, context=context) for v in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    # Map entry names are user-defined, never OpenAPI keywords. Carry the
    # entry's structural type even when its name is "content" or "$ref".
    is_named_map = context.startswith("map:")
    if not is_named_map and "externalValue" in value:
        raise TargetError("External examples are not supported; supply an inline example")
    if not is_named_map and "discriminator" in value:
        raise TargetError("Discriminator mappings require an explicit target projection; they cannot reference removed components")
    if not is_named_map and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/"):
            raise TargetError("Only local #/components/ references are supported")
        if re.search(r"~(?![01])", ref):
            raise TargetError("Invalid JSON Pointer escape in component reference")
        if ref in stack:
            raise TargetError("Recursive reference is not supported by this target; provide an acyclic projection")
        if set(value) != {"$ref"}:
            raise TargetError("OpenAPI 3.0 reference siblings are ambiguous; remove them")
        result = spec
        try:
            for part in ref[2:].split("/"):
                result = result[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise TargetError("Unresolved local component reference") from None
        return inline_local(spec, result, stack + (ref,), budget, context=context)
    result = {}
    for key, child in value.items():
        literal = not is_named_map and (
            key in _PROJECTION_LITERALS.get(context, ()) or str(key).startswith("x-"))
        child_context = (context[4:] if is_named_map else
                         _PROJECTION_CHILDREN.get(context, {}).get(key, "structural"))
        result[key] = copy.deepcopy(child) if literal else inline_local(
            spec, child, stack + (str(key),), budget, context=child_context)
    return result


def selected_operations(manifest: dict, specs: dict) -> list[dict]:
    records = []
    seen = set()
    for api in manifest["apis"]:
        name = api["name"]
        spec = specs[name]
        if not isinstance(spec, dict) or not re.fullmatch(r"3\.0\.\d+", str(spec.get("openapi", ""))):
            raise TargetError(f"{name}: requires OpenAPI 3.0.x")
        validate_schema(spec, "openapi")
        selected = api.get("mcpTools")
        if (not isinstance(selected, list) or not selected
                or not all(isinstance(s, str) for s in selected)
                or len(set(selected)) != len(selected)):
            raise TargetError(f"{name}: mcpTools must contain unique selected operation IDs")
        found = {}
        for path, item in spec.get("paths", {}).items():
            if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                    or any(p in (".", "..") for p in path.split("/"))
                    or any(c in path for c in ("\\", "?", "#", "%"))):
                raise TargetError(f"{name}: unsafe operation path")
            if not isinstance(item, dict) or "$ref" in item:
                raise TargetError(f"{name}: path items must be inline objects")
            for method, operation in item.items():
                if method not in HTTP_VERBS:
                    continue
                if not isinstance(operation, dict):
                    raise TargetError(f"{name}: invalid operation")
                opid = operation.get("operationId")
                if opid not in selected:
                    continue
                if opid in found or opid in seen:
                    raise TargetError(f"Duplicate selected operationId: {opid}")
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", opid):
                    raise TargetError("Canonical operation IDs must remain kebab-case")
                if operation.get("callbacks") or any(
                        r.get("links") for r in operation.get("responses", {}).values()
                        if isinstance(r, dict)):
                    raise TargetError(f"{opid}: callbacks/links need an explicit target projection")
                projected = inline_local(spec, operation, context="operation")
                projected.pop("x-mock", None)
                params = {}
                for param in inline_local(spec, item.get("parameters", []), context="parameter") + projected.get("parameters", []):
                    if not isinstance(param, dict) or not param.get("name") or not param.get("in"):
                        raise TargetError(f"{opid}: invalid parameter")
                    params[(param["name"], param["in"])] = param
                projected["parameters"] = list(params.values())
                for parameter in params.values():
                    if not str(parameter.get("description", "")).strip():
                        raise TargetError(f"{opid}: parameter {parameter['name']} needs a description")
                path_params = {p["name"] for p in params.values() if p["in"] == "path" and p.get("required") is True}
                if path_params != set(re.findall(r"\{([^{}]+)\}", path)):
                    raise TargetError(f"{opid}: path placeholders must match required path parameters")
                if not projected.get("responses"):
                    raise TargetError(f"{opid}: responses are required")
                if any(r.get("links") for r in projected["responses"].values()):
                    raise TargetError(f"{opid}: response links need an explicit target projection")
                projected.pop("servers", None)
                projected.pop("security", None)
                found[opid] = {"api": name, "operationId": opid, "method": method,
                               "path": path, "operation": projected}
                seen.add(opid)
        missing = sorted(set(selected) - set(found))
        if missing:
            raise TargetError(f"{name}: selected operations not found: {', '.join(missing)}")
        records.extend(found.values())
    return sorted(records, key=lambda r: (r["api"], r["operationId"]))


def validate_schema(document: dict, kind: str):
    schema = json.loads(files("mcp_openapi_creator_kit").joinpath("schemas", f"{kind}.json").read_text("utf-8"))
    try:
        errors = list(Draft4Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    except (TypeError, RecursionError):
        raise TargetError(f"{kind}: invalid YAML/JSON structure or cyclic object") from None
    if errors:
        error = errors[0]
        raise TargetError(f"{kind} schema: {'.'.join(map(str, error.absolute_path))}: "
                          f"violates {error.validator}; check the official schema (payload omitted)")


def preview_plan(manifest: dict, specs: dict) -> dict[str, bytes]:
    target = parse_targets(manifest)
    if target.preview is None:
        raise TargetError("Configure targets.gateway ai-gateway-preview first")
    config = target.preview
    blockers = ["Management resource paths/payloads not verified: automatic apply is disabled."]
    warnings = [
        "AI Gateway tier preview is not classic APIM AI policy support; existing profiles are unchanged.",
        "Runtime api-key grants access to every model/tool; isolate applications and environments.",
        "Preview has no SLA or guaranteed pricing. Check region availability for the approved subscription.",
        "No evidence of x-mock execution: OpenAPI import is tool routing, not mock serving.",
        "OAuth backend setup is interactive; oauth2-cc cannot be silently migrated.",
    ]
    if target.consumer == "rest":
        blockers.append("AI Gateway tier exposes MCP tools, not a replacement deployed REST API base URL.")
    artifacts = {}
    sources = []
    if config.source == "remote-mcp":
        sources = [s.model_dump(exclude_none=True) for s in config.remoteServers]
    else:
        records = selected_operations(manifest, specs)
        api_names = {api["name"] for api in manifest["apis"]}
        if set(config.restBaseUrls) - api_names:
            raise TargetError("restBaseUrls references unknown APIs")
        for api in manifest["apis"]:
            name = api["name"]
            base = config.restBaseUrls.get(name)
            if not base:
                blockers.append(f"{name}: provide approved existing REST backend URL in preview.restBaseUrls.")
                continue
            backend = api.get("backend", {})
            if backend.get("mode") == "mock":
                warnings.append(f"{name}: URL must serve an existing mock gateway or real REST service; the new tier will not execute examples/x-mock.")
            auth = backend.get("outboundAuth", {})
            if isinstance(auth, dict) and auth.get("type", auth.get("mode")) == "oauth2-cc":
                blockers.append(f"{name}: client credentials requires a separately verified backend authentication design.")
            doc = {"openapi": "3.0.3", "info": {"title": name, "version": "1.0.0"},
                   "servers": [{"url": https_url(base)}], "paths": {}}
            for record in records:
                if record["api"] == name:
                    doc["paths"].setdefault(record["path"], {})[record["method"]] = record["operation"]
            filename = f"gateway-openapi-{name}.json"
            validate_schema(doc, "openapi")
            artifacts[filename] = json_bytes(doc)
            sources.append({"name": name, "file": filename,
                            "backendAuthentication": "Configure and verify independently in portal; no credentials exported."})
    plan = {
        "formatVersion": 1, "target": "ai-gateway-preview", "experimental": True,
        "region": config.region, "managementApiVersion": "2026-05-01-preview",
        "applyAllowed": False, "liveVerified": False, "sources": sources,
        "blockers": blockers, "warnings": warnings,
        "approvalRequired": ["account", "tenant", "subscription", "azdEnvironment",
                             "resourceGroup", "region", "gatewayProfile", "gatewayTarget"],
        "portalSteps": [
            "Confirm the exact approved context; do not infer azd context from Azure CLI.",
            "Use https://ai.gateway.azure.com to create an isolated preview gateway after approval.",
            "Add MCP server using the exported OpenAPI or approved remote MCP sources; select the intended operations.",
            "Configure backend authentication; verify tools after any interactive OAuth sign-in.",
            "Create separate runtime key in secret store (gateway-wide api-key); never write its value here.",
            "Copy actual runtime URL; initialize MCP, list tools, then call an approved read-only tool.",
            "Verify unauthorized requests, namespace mapping and telemetry; record evidence and rollback plan.",
        ],
        "sourcesDocumentation": [
            "https://learn.microsoft.com/azure/api-management/ai-gateway-overview",
            "https://learn.microsoft.com/azure/api-management/quickstart-ai-gateway-create",
            "https://learn.microsoft.com/azure/api-management/ai-gateway-manage-models-tools"],
    }
    artifacts["ai-gateway-plan.json"] = json_bytes(plan)
    return artifacts


def inspect_targets(root: Path, manifest: dict, specs: dict) -> dict:
    """Pure in-memory preview for CLI, catalog, and MCP; never writes output."""
    target = parse_targets(manifest)
    result = {"consumer": target.consumer, "gateway": target.gateway,
              "artifacts": [], "errors": [], "reports": {}, "liveVerified": False}
    try:
        artifacts = preview_plan(manifest, specs) if target.preview else {}
        result["artifacts"] = [
            {"name": name, "bytes": len(data)} for name, data in sorted(artifacts.items())]
        result["reports"] = {name: json.loads(data) for name, data in artifacts.items()
                             if name == "ai-gateway-plan.json"}
    except (TargetError, OSError) as error:
        result["errors"].append(str(error))
    result["offlineReady"] = not result["errors"]
    result["capabilities"] = target_capabilities()
    return result
