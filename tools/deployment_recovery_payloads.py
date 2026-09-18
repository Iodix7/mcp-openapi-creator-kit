"""Bounded comparison of failed-import payloads, never resource ownership."""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET

import yaml

from mcp_openapi_creator_kit.deployment_names import deployment_name


class PayloadMismatch(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _contract_shape(text):
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        pairs = loader.construct_pairs(node, deep=True)
        result = {}
        for key, value in pairs:
            if not isinstance(key, str) or key in result:
                raise PayloadMismatch("ambiguous historical OpenAPI mapping")
            result[key] = value
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        spec = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as error:
        raise PayloadMismatch("historical OpenAPI could not be parsed") from error
    if not isinstance(spec, dict) or not re.fullmatch(r"3\.0\.\d+", str(spec.get("openapi", ""))):
        raise PayloadMismatch("historical payload is not OpenAPI 3.0")

    def schema(node):
        if not isinstance(node, dict):
            return
        # Repair only the known OAS 3.1-style bound representation. All other
        # schema fields, references, identities and extension rules stay exact.
        for bound, exclusive in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
            value = node.get(exclusive)
            if type(value) in (int, float):
                if bound in node and node[bound] != value:
                    raise PayloadMismatch("ambiguous historical exclusive bound")
                node[bound], node[exclusive] = value, True
        for child in (node.get("properties") or {}).values():
            schema(child)
        for name in ("items", "additionalProperties", "not"):
            schema(node.get(name))
        for name in ("allOf", "oneOf", "anyOf"):
            for child in node.get(name, []):
                schema(child)

    def media(node, *, response=False):
        if not isinstance(node, dict):
            return
        for item in node.get("content", {}).values():
            schema(item.get("schema"))
            if response:
                if "example" in item:
                    item["example"] = "<response-data>"
                for example in item.get("examples", {}).values():
                    if isinstance(example, dict) and "value" in example:
                        example["value"] = "<response-data>"

    def parameter(node):
        if isinstance(node, dict):
            schema(node.get("schema"))
            media(node)

    def response(node):
        if isinstance(node, dict):
            media(node, response=True)
            for header in node.get("headers", {}).values():
                parameter(header)

    components = spec.get("components", {})
    for node in components.get("schemas", {}).values():
        schema(node)
    for section in ("parameters", "headers"):
        for node in components.get(section, {}).values():
            parameter(node)
    for node in components.get("requestBodies", {}).values():
        media(node)
    for node in components.get("responses", {}).values():
        response(node)
    for path in spec.get("paths", {}).values():
        for node in path.get("parameters", []):
            parameter(node)
        for method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
            operation = path.get(method, {})
            for node in operation.get("parameters", []):
                parameter(node)
            media(operation.get("requestBody"))
            for node in operation.get("responses", {}).values():
                response(node)
    return _json(spec)


def _policy_shape(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise PayloadMismatch("policy entities are unsupported")
    try:
        root = ET.fromstring(text, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)))
    except ET.ParseError as error:
        raise PayloadMismatch("historical policy could not be parsed") from error

    def shape(node, parent=None):
        value = node.text
        if node.tag == "set-body" and parent == "return-response" and not node.attrib and not len(node):
            value = value or ""
            if any(marker in value for marker in ("@(", "@{", "{{")):
                raise PayloadMismatch("response body is executable or contains a named-value reference")
            json.loads(value)
            value = "<literal-json-response>"
        return [str(node.tag), node.attrib, value, node.tail,
                [shape(child, node.tag) for child in node]]

    return _json(shape(root))


def payload_value(template, value):
    """Only Bicep's literal file-content variable, not an ARM expression evaluator."""
    if not isinstance(value, str):
        raise PayloadMismatch("payload argument is not a string")
    match = re.fullmatch(r"\[variables\('(\$fxv#\d+)'\)\]", value)
    if match:
        result = template.get("variables", {}).get(match[1])
        if not isinstance(result, str):
            raise PayloadMismatch("file-content variable is not a string")
        return result, match[1]
    if value.startswith("["):
        raise PayloadMismatch("payload argument is not a literal file")
    return value, None


def templates_match(current, historical, manifest, *, root=False):
    if _json(current) == _json(historical):
        return
    if not root or not isinstance(historical, dict):
        raise PayloadMismatch("nested module template changed")
    apis = manifest.get("apis")
    if (not isinstance(apis, list) or not apis or
            any(not isinstance(api, dict) or api.get("backend") != {"mode": "mock"} for api in apis)):
        raise PayloadMismatch("payload corrections require explicit credential-free mock backends")
    before, after = copy.deepcopy(historical), copy.deepcopy(current)
    expected = {}
    cid = manifest["client"]
    for api in manifest["apis"]:
        expected[deployment_name(f"api-{cid}-{api['name']}")] = api["name"]
    exposure = manifest.get("mcpExposure", {})
    if exposure.get("mode", "perApi") != "perApi":
        expected[deployment_name("facade-" + cid)] = exposure.get("facadeName", "agent")
    old_resources, new_resources = before.get("resources"), after.get("resources")
    if not isinstance(old_resources, list) or not isinstance(new_resources, list) or len(old_resources) != len(new_resources):
        raise PayloadMismatch("resource structure changed")
    definitions = {"apimName", "clientId", "apiName", "displayName", "specValue", "policyXml",
                   "backendMode", "backendUrl", "toolOperations", "exposeMcp", "tagIds"}
    allowed_paths, variables, changed = set(), set(), False
    for index, (old, new) in enumerate(zip(old_resources, new_resources)):
        name = new.get("name")
        if name not in expected or new.get("type", "").lower() != "microsoft.resources/deployments":
            continue
        props = new.get("properties", {})
        template = props.get("template", {})
        params = props.get("parameters", {})
        signature = template.get("parameters", {})
        if (set(signature) != definitions or
                any(signature.get(slot, {}).get("type") != "string" for slot in ("specValue", "policyXml")) or
                params.get("clientId") != {"value": cid} or
                params.get("apiName") != {"value": expected[name]} or
                params.get("backendMode") != {"value": "mock"} or
                params.get("backendUrl", {"value": ""}) != {"value": ""}):
            continue
        old_params = old.get("properties", {}).get("parameters", {})
        for slot, comparator in (("specValue", _contract_shape), ("policyXml", _policy_shape)):
            new_entry, old_entry = params.get(slot), old_params.get(slot)
            if (not isinstance(new_entry, dict) or set(new_entry) != {"value"} or
                    not isinstance(old_entry, dict) or set(old_entry) != {"value"}):
                raise PayloadMismatch("missing or unexpected payload argument")
            new_value, new_var = payload_value(current, new_entry["value"])
            old_value, old_var = payload_value(historical, old_entry["value"])
            if new_var != old_var:
                raise PayloadMismatch("file-content argument binding changed")
            if new_value != old_value:
                if comparator(new_value) != comparator(old_value):
                    raise PayloadMismatch("payload identity, authentication, backend or policy structure changed")
                changed = True
            path = ("resources", index, "properties", "parameters", slot, "value")
            allowed_paths.add(path)
            if new_var:
                variables.add(new_var)
                before["variables"][new_var] = after["variables"][new_var] = "<validated-file-content>"
            else:
                old_entry["value"] = new_entry["value"] = "<validated-file-content>"
    # A file variable reused in a scope/name/auth expression is not merely data.
    def check_references(node, path=()):
        if isinstance(node, dict):
            for key, child in node.items():
                check_references(child, (*path, key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                check_references(child, (*path, index))
        elif isinstance(node, str) and path not in allowed_paths:
            if any(f"variables('{name}')" in node for name in variables):
                raise PayloadMismatch("payload variable also controls a non-payload field")
    check_references(current)
    check_references(historical)
    if changed:
        old_generator = before.get("metadata", {}).get("_generator", {})
        new_generator = after.get("metadata", {}).get("_generator", {})
        if (old_generator.get("name") != "bicep" or new_generator.get("name") != "bicep" or
                not isinstance(new_generator.get("version"), str) or not new_generator["version"] or
                not all(isinstance(g.get("templateHash"), str) and
                        re.fullmatch(r"\d{1,20}", g["templateHash"])
                        for g in (old_generator, new_generator))):
            raise PayloadMismatch("unknown compiler metadata")
        old_generator["templateHash"] = new_generator["templateHash"] = "<payload-derived-hash>"
    if _json(before) != _json(after):
        raise PayloadMismatch("compiled structure, resource identity or non-payload inputs changed")
