#!/usr/bin/env python3
"""Explicit offline client variant preparation. Default is a read-only dry-run."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys

import yaml

if __package__:
    from .deployment import client_path, safe_path, slug
    from .lifecycle import ReconcileError
    from .local_python import local_python
else:
    from deployment import client_path, safe_path, slug
    from lifecycle import ReconcileError
    from local_python import local_python
from mcp_openapi_creator_kit.consumer_export import (
    _PROJECTION_CHILDREN, _PROJECTION_LITERALS, validate_schema)

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_CHILDREN = {
    **_PROJECTION_CHILDREN,
    "root": {"paths": "map:path", "components": "components"},
    "path": {**{method: "operation" for method in ("get", "put", "post", "patch", "delete", "head", "options")},
             "parameters": "parameter"},
    "components": {key: "map:" + kind for key, kind in (
        ("schemas", "schema"), ("parameters", "parameter"), ("responses", "response"),
        ("requestBodies", "requestBody"), ("examples", "example"), ("links", "link"),
        ("headers", "parameter"), ("callbacks", "callback"))},
}


def generator(root):
    from mcp_openapi_creator_kit.runtime import command
    module = command("build-facade")
    module.REPO_ROOT = root
    return module


def _pointer(spec, ref):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ReconcileError("Variant requires local JSON Pointer references; external refs are unsupported")
    if re.search(r"~(?![01])", ref):
        raise ReconcileError("Invalid JSON Pointer escape")
    value = spec
    try:
        for part in ref[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError) as error:
        raise ReconcileError("Unresolved local reference in variant source") from error
    return value


def _validate_refs(spec, node, depth=0, context="root"):
    if depth > 100:
        raise ReconcileError("Variant source nesting/alias cycle exceeds safe depth")
    if isinstance(node, list):
        for item in node:
            _validate_refs(spec, item, depth + 1, context)
    elif isinstance(node, dict):
        is_map = context.startswith("map:")
        for key, value in node.items():
            if is_map:
                _validate_refs(spec, value, depth + 1, context[4:])
            elif key == "$ref":
                _pointer(spec, value)
            elif key == "operationRef":
                _pointer(spec, value)
            elif key == "discriminator" and context == "schema":
                for target in (value.get("mapping") or {}).values():
                    if target in spec.get("components", {}).get("schemas", {}):
                        continue
                    _pointer(spec, target)
            elif (key == "callbacks" and context in {"operation", "components"} or
                  key == "externalValue" and context == "example") and value:
                raise ReconcileError("Callbacks and external examples require a manually reviewed variant")
            elif key in _PROJECTION_LITERALS.get(context, ()):
                continue
            elif str(key).startswith("x-"):
                if re.search(r'"(?:operationId|operationRef|\$ref)"\s*:', json.dumps(value)):
                    raise ReconcileError("Extension references require a manually reviewed variant")
            else:
                _validate_refs(spec, value, depth + 1, REFERENCE_CHILDREN.get(context, {}).get(key, "structural"))


def _rewrite_links(spec, node, operations, depth=0, context="root"):
    if depth > 100:
        raise ReconcileError("Variant source nesting exceeds safe depth")
    if context == "schema":
        return
    if isinstance(node, list):
        for item in node:
            _rewrite_links(spec, item, operations, depth + 1, context)
        return
    if not isinstance(node, dict):
        return
    if context == "link":
        if "$ref" in node:
            return
        if "operationId" in node:
            if node["operationId"] not in operations:
                raise ReconcileError("Link operationId is outside this contract")
            node["operationId"] = operations[node["operationId"]]
        if "operationRef" in node:
            _pointer(spec, node["operationRef"])
    for key, value in node.items():
        if context.startswith("map:"):
            child_context = context[4:]
        elif key in _PROJECTION_LITERALS.get(context, ()) or str(key).startswith("x-"):
            continue
        else:
            child_context = REFERENCE_CHILDREN.get(context, {}).get(key, "structural")
        _rewrite_links(spec, value, operations, depth + 1, child_context)


def prepare(root: Path, source: str, target: str, source_root: Path | None = None) -> dict[Path, str]:
    root = root.resolve()
    slug(target)
    source_root = (source_root or root).resolve()
    source_dir = client_path(source_root, str(Path("clients") / slug(source)))
    destination = safe_path(root, root / "clients" / target)
    if destination.exists():
        raise ReconcileError("Target client already exists; variants never overwrite")
    for directory in ("clients", "apis"):
        safe_path(root, root / directory)
        for path in (root / directory).rglob("*"):
            safe_path(root, path)
    bf = generator(root)
    original = yaml.safe_load((source_dir / "mcp-manifest.yaml").read_text(encoding="utf-8"))
    manifest = bf.validate_manifest(copy.deepcopy(original), source)
    if any(api["backend"]["mode"] != "mock" or api["backend"].get("outboundAuth")
           for api in manifest["apis"]):
        raise ReconcileError("Automatic variants support mock clients without outbound credentials only; "
                             "external/secret bindings need explicit manual review")
    manifest["client"] = target
    manifest["displayName"] = manifest["displayName"] + f" ({target})"
    occupied_operations = set()
    for path in (root / "apis").glob("*/openapi.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        occupied_operations.update(op.get("operationId") for _, _, op in bf.iter_operations(spec))
    planned = {}
    proposed = {}
    planned_ids = set()
    components = {}
    paths = set()
    for api in manifest["apis"]:
        old_name = slug(api["name"])
        new_name = slug(f"{old_name}-{target}")
        destination_api = safe_path(root, root / "apis" / new_name)
        if destination_api.exists():
            raise ReconcileError(f"Contract destination already exists: {new_name}")
        source_spec = safe_path(source_root, source_root / "apis" / old_name / "openapi.yaml")
        spec = yaml.safe_load(source_spec.read_text(encoding="utf-8"))
        bf.validate_openapi_version(old_name, spec)
        _validate_refs(spec, spec)
        validate_schema(spec, "openapi")
        operations = {}
        for _, _, operation in bf.iter_operations(spec):
            old = operation.get("operationId")
            if not old or old in operations:
                raise ReconcileError("Source operation IDs must be present and unique")
            new = slug(f"{target}-{old}")
            if new in occupied_operations or new in planned_ids:
                raise ReconcileError(f"Operation name collision: {new}")
            operations[old] = new
            planned_ids.add(new)
        for path, item in spec.get("paths", {}).items():
            if "$ref" in item:
                raise ReconcileError("Path-item refs are unsupported in automatic variants")
            if manifest["mcpExposure"]["mode"] != "perApi" and path in paths:
                raise ReconcileError("Duplicate facade path across variant contracts")
            paths.add(path)
        for _, _, operation in bf.iter_operations(spec):
            operation["operationId"] = operations[operation["operationId"]]
        _rewrite_links(spec, spec, operations)
        validate_schema(spec, "openapi")
        try:
            api["mcpTools"] = [operations[tool] for tool in api["mcpTools"]]
        except KeyError as error:
            raise ReconcileError("Source selected tool does not exist") from error
        api["name"] = new_name
        for section, entries in spec.get("components", {}).items():
            for name, value in entries.items():
                key = (section, name)
                if key in components and components[key] != value:
                    raise ReconcileError("Conflicting component definitions across variant contracts")
                components[key] = value
        bf.validate_backend(api)
        bf.validate_standards(new_name, spec, manifest["standards"])
        bf.validate_examples(new_name, spec)
        bf.build_api_policy(target, api, spec)
        # This invariant includes canonical schemas and example payloads.
        assert spec.get("components", {}).get("schemas") == yaml.safe_load(
            source_spec.read_text(encoding="utf-8")).get("components", {}).get("schemas")
        planned[destination_api / "openapi.yaml"] = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)
        proposed[new_name] = spec
    bf.validate_manifest(manifest, target)
    bf.check_schema_library(proposed)
    planned[destination / "mcp-manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    for path in planned:
        safe_path(root, path)
    return planned


def apply(root: Path, plan: dict[Path, str]):
    # Recheck every destination before the first write. Exclusive mkdir/open also
    # protects against ordinary concurrent creation; not a filesystem transaction.
    for path in plan:
        safe_path(root, path)
        if path.parent.exists():
            raise ReconcileError("Variant destination appeared after planning; nothing written")
    directories = []
    files = []
    try:
        for path, text in sorted(plan.items()):
            safe_path(root, path)
            path.parent.mkdir()
            directories.append(path.parent)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                files.append(path)
                stream.write(text)
    except OSError:
        for path in reversed(files):
            path.unlink()
        for path in reversed(directories):
            path.rmdir()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="existing client ID")
    parser.add_argument("target", help="new client ID; also namespaces every operation")
    parser.add_argument("--write", action="store_true", help="explicitly write the validated plan (never generated files)")
    args = parser.parse_args()
    try:
        local_python(REPO_ROOT)
        plan = prepare(REPO_ROOT, args.source, args.target)
        print(json.dumps({"mode": "WRITE" if args.write else "DRY-RUN",
                          "files": [str(path.relative_to(REPO_ROOT)) for path in sorted(plan)]}, indent=2))
        if args.write:
            apply(REPO_ROOT, plan)
            print("Variant source created. Run generators and preview deployment separately.")
    except (ReconcileError, RuntimeError, OSError, ValueError) as error:
        print(f"[prepare-variant] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
