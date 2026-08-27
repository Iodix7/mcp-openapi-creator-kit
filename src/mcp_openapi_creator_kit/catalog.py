#!/usr/bin/env python3
"""Build a deterministic, repository-agnostic capability catalog as JSON and HTML."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .policy import (HTTP_VERBS, POLICY_LIMIT_BYTES, PolicyBuildError,
                     ToolDefinition, inline_schema, resolve_ref, shard_tools)
FORMAT_VERSION = "1.0"

PROFILES = [
    {
        "id": "native-mcp",
        "label": {"it": "MCP nativo", "en": "Native MCP"},
        "gateway": "APIM Basic v2 o superiore",
        "interface": "MCP Streamable HTTP",
        "fixedCost": True,
        "mockOnly": False,
        "supports": ["tools", "external-backends", "private-network"],
    },
    {
        "id": "policy-mcp-consumption",
        "label": {"it": "MCP policy Consumption", "en": "Consumption policy MCP"},
        "gateway": "APIM Consumption",
        "interface": "MCP Streamable HTTP",
        "fixedCost": False,
        "mockOnly": True,
        "supports": ["tools", "automatic-sharding", "public-mocks"],
        "policyLimitBytes": POLICY_LIMIT_BYTES,
    },
    {
        "id": "rest-consumption",
        "label": {"it": "REST Consumption", "en": "Consumption REST"},
        "gateway": "APIM Consumption",
        "interface": "REST / OpenAPI",
        "fixedCost": False,
        "mockOnly": True,
        "supports": ["rest", "custom-connector", "public-mocks"],
    },
]


def read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def json_media(content: dict):
    if not content:
        return None, None
    if "application/json" in content:
        return "application/json", content["application/json"]
    key = next((key for key in content if key.endswith("+json")), next(iter(content)))
    return key, content[key]


def examples_from_media(media: dict | None):
    if not media:
        return []
    if "example" in media:
        return [{"name": "default", "value": media["example"]}]
    return [{"name": name, "summary": value.get("summary"),
             "value": value.get("value")}
            for name, value in (media.get("examples") or {}).items()]


def operation_record(spec: dict, path: str, verb: str, operation: dict,
                     path_parameters: list) -> dict:
    parameters = []
    for value in [*path_parameters, *operation.get("parameters", [])]:
        parameter = resolve_ref(spec, value)
        parameters.append({
            "name": parameter.get("name"), "in": parameter.get("in"),
            "required": bool(parameter.get("required")),
            "description": parameter.get("description", ""),
            "schema": inline_schema(spec, parameter.get("schema", {})),
            "example": parameter.get("example"),
        })
    request = resolve_ref(spec, operation.get("requestBody"))
    request_record = None
    if request:
        media_type, media = json_media(request.get("content") or {})
        request_record = {
            "required": bool(request.get("required")), "mediaType": media_type,
            "schema": inline_schema(spec, (media or {}).get("schema", {})),
            "examples": examples_from_media(media),
        }
    responses = []
    for status, raw in (operation.get("responses") or {}).items():
        response = resolve_ref(spec, raw)
        media_type, media = json_media(response.get("content") or {})
        responses.append({
            "status": str(status), "description": response.get("description", ""),
            "mediaType": media_type,
            "schema": inline_schema(spec, (media or {}).get("schema", {})),
            "examples": examples_from_media(media),
        })
    return {
        "operationId": operation.get("operationId"), "method": verb.upper(),
        "path": path, "summary": operation.get("summary", ""),
        "description": " ".join(operation.get("description", "").split()),
        "parameters": parameters, "requestBody": request_record,
        "responses": responses, "mockRules": operation.get("x-mock") or [],
    }


def contract_tools(name: str, spec: dict) -> list[ToolDefinition]:
    tools = []
    for path, path_item in spec.get("paths", {}).items():
        path_parameters = path_item.get("parameters", []) if isinstance(path_item, dict) else []
        for verb, operation in path_item.items():
            if verb in HTTP_VERBS and isinstance(operation, dict) and operation.get("operationId"):
                tools.append(ToolDefinition(name, path, verb, operation,
                                            path_parameters, spec))
    return tools


def policy_compatibility(name: str, spec: dict) -> dict:
    try:
        shards = shard_tools(f"catalog-{name}", contract_tools(name, spec))
        return {
            "supported": True,
            "servers": [{"tools": [tool.name for tool in tools],
                         "sizeBytes": len(policy.encode("utf-8")),
                         "usagePercent": round(len(policy.encode("utf-8")) /
                                               POLICY_LIMIT_BYTES * 100, 1)}
                        for tools, policy in shards],
        }
    except (PolicyBuildError, KeyError, TypeError, ValueError) as error:
        return {"supported": False, "reason": str(error), "servers": []}


def metadata_for(metadata: dict, contract_name: str) -> dict:
    return ((metadata.get("contracts") or {}).get(contract_name) or {})


def load_usages(root: Path) -> tuple[dict, list]:
    usages = {}
    clients = []
    for manifest_path in sorted((root / "clients").glob("*/mcp-manifest.yaml")):
        manifest = read_yaml(manifest_path)
        client_record = {
            "id": manifest["client"], "displayName": manifest["displayName"],
            "exposure": manifest.get("mcpExposure", {}), "apis": [],
        }
        for api in manifest.get("apis", []):
            record = {
                "contract": api["name"], "displayName": api.get("displayName"),
                "backendMode": (api.get("backend") or {}).get("mode"),
                "tools": api.get("mcpTools", []),
            }
            client_record["apis"].append(record)
            usages.setdefault(api["name"], []).append({
                "client": manifest["client"], "displayName": manifest["displayName"],
                **record,
            })
        clients.append(client_record)
    return usages, clients


def build_index(root: Path, metadata_path: Path | None = None) -> dict:
    metadata = (read_yaml(metadata_path) if metadata_path and metadata_path.exists()
                else {}) or {}
    canonical_path = root / "apis" / "canonical-schemas.yaml"
    canonical = set((read_yaml(canonical_path) or {}).get("schemas", [])) \
        if canonical_path.exists() else set()
    usages, clients = load_usages(root)
    scenarios = []
    all_schema_names = set()
    warnings = []

    for spec_path in sorted((root / "apis").glob("*/openapi.yaml")):
        name = spec_path.parent.name
        spec = read_yaml(spec_path)
        info = spec.get("info", {})
        override = metadata_for(metadata, name)
        operations = []
        for path, path_item in spec.get("paths", {}).items():
            path_parameters = path_item.get("parameters", []) if isinstance(path_item, dict) else []
            for verb, operation in path_item.items():
                if verb in HTTP_VERBS and isinstance(operation, dict):
                    operations.append(operation_record(spec, path, verb, operation,
                                                       path_parameters))
        schemas = []
        for schema_name, definition in sorted(
                (((spec.get("components") or {}).get("schemas") or {}).items())):
            all_schema_names.add(schema_name)
            schemas.append({"name": schema_name, "canonical": schema_name in canonical,
                            "schema": inline_schema(spec, definition)})
        scenario = override.get("scenario") or {}
        if not scenario.get("persona"):
            warnings.append({"contract": name, "field": "scenario.persona"})
        scenarios.append({
            "id": name,
            "title": scenario.get("title") or {"source": info.get("title", name)},
            "description": info.get("description", ""),
            "domain": override.get("domain") or name,
            "tags": override.get("tags") or [],
            "persona": scenario.get("persona"),
            "jobToBeDone": scenario.get("jobToBeDone"),
            "outcome": scenario.get("outcome"),
            "sourceLanguage": override.get("sourceLanguage", "en"),
            "mock": {"type": "dynamic" if any(op["mockRules"] for op in operations)
                     else "static",
                     "ruleCount": sum(len(op["mockRules"]) for op in operations)},
            "compatibility": {
                "native-mcp": {"supported": True},
                "rest-consumption": {"supported": True},
                "policy-mcp-consumption": policy_compatibility(name, spec),
            },
            "operations": operations, "schemas": schemas,
            "usedBy": usages.get(name, []),
            "manifestSnippet": yaml.safe_dump({
                "name": name, "displayName": info.get("title", name),
                "backend": {"mode": "mock"},
                "mcpTools": [op["operationId"] for op in operations],
            }, sort_keys=False).strip(),
        })

    return {
        "formatVersion": FORMAT_VERSION,
        "profiles": PROFILES,
        "summary": {
            "scenarios": len(scenarios),
            "operations": sum(len(item["operations"]) for item in scenarios),
            "schemas": len(all_schema_names), "clients": len(clients),
        },
        "scenarios": scenarios, "clients": clients,
        "canonicalSchemas": sorted(canonical), "warnings": warnings,
    }


def safe_script_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) \
        .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def render_outputs(root: Path, metadata_path: Path | None = None) -> tuple[dict, str]:
    index = build_index(root, metadata_path)
    template = (root / "catalog" / "template.html").read_text(encoding="utf-8")
    return index, template.replace("__CATALOG_DATA__", safe_script_json(index))


def write_outputs(root: Path, output_dir: Path, metadata_path: Path | None = None):
    index, html = render_outputs(root, metadata_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    (output_dir / "catalog.json").write_text(
        json_text, encoding="utf-8", newline="\n")
    (output_dir / "catalog.html").write_text(
        html, encoding="utf-8", newline="\n")
    print(f"[build-catalog] {index['summary']['scenarios']} scenarios, "
          f"{index['summary']['operations']} operations -> {output_dir}")


def main(root: Path | None = None):
    root = (root or Path.cwd()).resolve()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="catalog/generated")
    parser.add_argument("--metadata", default="catalog/metadata.yaml")
    args = parser.parse_args()
    write_outputs(root, root / args.output, root / args.metadata)


if __name__ == "__main__":
    main()
