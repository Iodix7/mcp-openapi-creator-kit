"""Manifest-driven MCP Streamable HTTP policies for APIM Consumption."""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import yaml


POLICY_LIMIT_BYTES = 16 * 1024
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


class PolicyBuildError(ValueError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    api_name: str
    path: str
    verb: str
    operation: dict
    path_parameters: list
    spec: dict

    @property
    def name(self) -> str:
        return self.operation["operationId"]


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def csharp_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def resolve_ref(spec: dict, value):
    seen = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not ref.startswith("#/"):
            raise PolicyBuildError(f"external $ref not supported: {ref}")
        if ref in seen:
            raise PolicyBuildError(f"cyclic $ref: {ref}")
        seen.add(ref)
        current = spec
        for part in ref[2:].split("/"):
            current = current[part.replace("~1", "/").replace("~0", "~")]
        value = current
    return value


def response_for_status(operation: dict, status: int | str):
    responses = operation.get("responses") or {}
    for key, response in responses.items():
        if str(key) == str(status):
            return response
    return None


def inline_schema(spec: dict, value):
    value = resolve_ref(spec, value)
    if isinstance(value, list):
        return [inline_schema(spec, item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: inline_schema(spec, item) for key, item in value.items()
            if key not in {"example", "examples", "xml", "externalDocs"}}


def first_json_media(content: dict):
    if not content:
        return None
    if "application/json" in content:
        return content["application/json"]
    problem = next((value for key, value in content.items()
                    if key.endswith("+json")), None)
    return problem or next(iter(content.values()))


def response_example(tool: ToolDefinition, respond: dict):
    status = str(respond["status"])
    response = resolve_ref(tool.spec, response_for_status(tool.operation, status))
    if response is None:
        raise PolicyBuildError(
            f"{tool.api_name}/{tool.name}: response {status} not declared")
    media = first_json_media(response.get("content") or {})
    if media is None:
        return None
    example_name = respond.get("example")
    if example_name:
        examples = media.get("examples") or {}
        if example_name not in examples:
            raise PolicyBuildError(
                f"{tool.api_name}/{tool.name}: example '{example_name}' missing")
        return examples[example_name]["value"]
    if "example" in media:
        return media["example"]
    examples = media.get("examples") or {}
    if examples:
        return next(iter(examples.values()))["value"]
    raise PolicyBuildError(f"{tool.api_name}/{tool.name}: response {status} has no example")


def default_respond(tool: ToolDefinition) -> dict:
    status = next((value for value in tool.operation.get("responses", {})
                   if str(value).isdigit()), None)
    if status is None:
        raise PolicyBuildError(f"{tool.api_name}/{tool.name}: no numeric status")
    return {"status": int(status)}


def operation_parameters(tool: ToolDefinition) -> list[dict]:
    values = [*tool.path_parameters, *tool.operation.get("parameters", [])]
    return [resolve_ref(tool.spec, value) for value in values]


def tool_input_schema(tool: ToolDefinition) -> dict:
    properties = {}
    required = []
    for parameter in operation_parameters(tool):
        name = parameter["name"]
        schema = inline_schema(tool.spec, parameter.get("schema", {"type": "string"}))
        if parameter.get("description"):
            schema["description"] = " ".join(parameter["description"].split())
        properties[name] = schema
        if parameter.get("required"):
            required.append(name)

    request_body = resolve_ref(tool.spec, tool.operation.get("requestBody"))
    if request_body:
        media = first_json_media(request_body.get("content") or {})
        schema = inline_schema(tool.spec, (media or {}).get("schema", {}))
        if schema.get("type") == "object" or schema.get("properties"):
            for name, definition in schema.get("properties", {}).items():
                if name in properties:
                    raise PolicyBuildError(
                        f"{tool.api_name}/{tool.name}: input '{name}' collides between parameter and body")
                properties[name] = definition
            required.extend(schema.get("required", []))
        else:
            if "body" in properties:
                raise PolicyBuildError(f"{tool.api_name}/{tool.name}: input body collide")
            properties["body"] = schema or {"type": "object"}
            if request_body.get("required"):
                required.append("body")

    result = {"type": "object", "properties": properties,
              "additionalProperties": False}
    if required:
        result["required"] = list(dict.fromkeys(required))
    return result


def tool_descriptor(tool: ToolDefinition) -> dict:
    description = " ".join((tool.operation.get("description") or
                             tool.operation.get("summary") or tool.name).split())
    return {"name": tool.name, "description": description,
            "inputSchema": tool_input_schema(tool)}


def sample_from_schema(spec: dict, schema: dict):
    schema = inline_schema(spec, schema or {})
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    schema_type = schema.get("type")
    if schema_type == "object" or schema.get("properties"):
        return {name: sample_from_schema(spec, definition)
                for name, definition in schema.get("properties", {}).items()
                if name in schema.get("required", [])}
    if schema_type == "array":
        return [sample_from_schema(spec, schema.get("items", {}))]
    if schema_type in ("integer", "number"):
        return 1
    if schema_type == "boolean":
        return True
    return "test"


def _sample_arguments(tool: ToolDefinition) -> dict:
    arguments = {}
    for parameter in operation_parameters(tool):
        if parameter.get("required"):
            value = parameter.get("example")
            if value is None:
                value = sample_from_schema(tool.spec, parameter.get("schema", {}))
            arguments[parameter["name"]] = value

    request_body = resolve_ref(tool.spec, tool.operation.get("requestBody"))
    if request_body:
        media = first_json_media(request_body.get("content") or {})
        if media:
            if "example" in media:
                body = media["example"]
            elif media.get("examples"):
                body = next(iter(media["examples"].values()))["value"]
            else:
                body = sample_from_schema(tool.spec, media.get("schema", {}))
            if isinstance(body, dict):
                arguments.update(body)
            else:
                arguments["body"] = body
    return arguments


def _condition_matches(condition: dict, arguments: dict) -> bool:
    value = arguments.get(condition["param"])
    if condition.get("missing") is True:
        return value is None or value == ""
    if value is None:
        return False
    actual = str(value).lower()
    if "equals" in condition:
        return actual == str(condition["equals"]).lower()
    if "contains" in condition:
        return str(condition["contains"]).lower() in actual
    return actual.startswith(str(condition["startsWith"]).lower())


def _apply_condition(condition: dict, arguments: dict):
    name = condition["param"]
    if condition.get("missing") is True:
        arguments.pop(name, None)
    elif "equals" in condition:
        arguments[name] = condition["equals"]
    elif "contains" in condition:
        arguments[name] = f"test-{condition['contains']}-test"
    elif "startsWith" in condition:
        arguments[name] = f"{condition['startsWith']}test"


def _avoid_prior_rules(tool: ToolDefinition, selected: dict,
                       prior_rules: list[dict], arguments: dict):
    selected_condition = selected.get("when")
    required = {parameter["name"] for parameter in operation_parameters(tool)
                if parameter.get("required")}
    for prior in prior_rules:
        condition = prior.get("when")
        if not condition or not _condition_matches(condition, arguments):
            continue
        name = condition["param"]
        candidates = ["unmatched-value", "z", "0", "branch-fallback"]
        if name not in required:
            candidates.append(None)
        original = arguments.get(name)
        for candidate in candidates:
            if candidate is None:
                arguments.pop(name, None)
            else:
                arguments[name] = candidate
            selected_matches = (not selected_condition or
                                _condition_matches(selected_condition, arguments))
            if selected_matches and not any(
                    previous.get("when") and
                    _condition_matches(previous["when"], arguments)
                    for previous in prior_rules):
                break
        else:
            if original is None:
                arguments.pop(name, None)
            else:
                arguments[name] = original
            raise PolicyBuildError(
                "x-mock branch is unreachable because an earlier rule always "
                f"matches parameter '{name}'")


def sample_tool_calls(tool: ToolDefinition) -> list[tuple[dict, object, bool]]:
    """Every effective x-mock branch -> MCP args and expected result."""
    calls = []
    rules = mock_rules(tool)
    for index, selected in enumerate(rules):
        arguments = _sample_arguments(tool)
        condition = selected.get("when")
        if condition:
            _apply_condition(condition, arguments)
        _avoid_prior_rules(tool, selected, rules[:index], arguments)
        respond = selected["respond"]
        calls.append((arguments, response_example(tool, respond),
                      int(respond["status"]) >= 400))
    return calls


def sample_tool_call(tool: ToolDefinition) -> tuple[dict, object, bool]:
    """Backward-compatible first smoke call for callers needing one case."""
    return sample_tool_calls(tool)[0]


def rpc_envelope(result_expression: str) -> str:
    return (
        'return new JObject(new JProperty("jsonrpc", "2.0"), '
        'new JProperty("id", request["id"] ?? JValue.CreateNull()), '
        f'new JProperty("result", {result_expression}))'
        '.ToString(Newtonsoft.Json.Formatting.None);'
    )


def error_envelope(code: int, message: str) -> str:
    return (
        'return new JObject(new JProperty("jsonrpc", "2.0"), '
        'new JProperty("id", request["id"] ?? JValue.CreateNull()), '
        'new JProperty("error", new JObject('
        f'new JProperty("code", {code}), '
        f'new JProperty("message", "{csharp_string(message)}"))))'
        '.ToString(Newtonsoft.Json.Formatting.None);'
    )


def initialize_body(server_name: str) -> str:
    return """@{
 var request=(JObject)context.Variables["rpc"];
 var requested=(string)request.SelectToken("params.protocolVersion");
 var version=string.IsNullOrEmpty(requested)?"2025-03-26":requested;
 var result=new JObject(new JProperty("protocolVersion",version),new JProperty("capabilities",new JObject(new JProperty("tools",new JObject(new JProperty("listChanged",false))))),new JProperty("serverInfo",new JObject(new JProperty("name","%s"),new JProperty("version","1.0.0"))));
 %s
}""" % (csharp_string(server_name), rpc_envelope("result"))


def ping_body() -> str:
    return """@{
 var request=(JObject)context.Variables["rpc"];
 %s
}""" % rpc_envelope("new JObject()")


def tools_list_body(tools: list[ToolDefinition]) -> str:
    payload = csharp_string(compact({"tools": [tool_descriptor(tool) for tool in tools]}))
    return """@{
 var request=(JObject)context.Variables["rpc"];
 var result=JObject.Parse("%s");
 %s
}""" % (payload, rpc_envelope("result"))


def mock_rules(tool: ToolDefinition) -> list[dict]:
    rules = copy.deepcopy(tool.operation.get("x-mock") or [])
    parameters = {parameter.get("name"): parameter
                  for parameter in operation_parameters(tool)}
    for index, rule in enumerate(rules):
        where = f"{tool.api_name}/{tool.name} x-mock rule {index + 1}"
        if not isinstance(rule, dict) or not isinstance(rule.get("respond"), dict):
            raise PolicyBuildError(f"{where}: each rule requires respond")
        if "when" not in rule:
            if index != len(rules) - 1:
                raise PolicyBuildError(
                    f"{where}: default rule without when must be last")
        else:
            condition = rule["when"]
            if not isinstance(condition, dict) or not isinstance(
                    condition.get("param"), str):
                raise PolicyBuildError(
                    f"{where}: when requires a parameter name")
            name = condition["param"]
            if name not in parameters:
                raise PolicyBuildError(
                    f"{where}: unknown parameter '{name}'")
            operators = [operator for operator in
                         ("equals", "contains", "startsWith", "missing")
                         if operator in condition]
            if len(operators) != 1:
                raise PolicyBuildError(
                    f"{where}: when requires exactly one operator")
            if operators[0] == "missing" and parameters[name].get("in") == "path":
                raise PolicyBuildError(
                    f"{where}: missing is invalid for path parameter '{name}'")
        respond = rule["respond"]
        if not isinstance(respond.get("status"), int):
            raise PolicyBuildError(f"{where}: respond.status must be an integer")
        response_example(tool, respond)
    if not rules or rules[-1].get("when") is not None:
        rules.append({"respond": default_respond(tool)})
    return rules


def condition_expression(condition: dict) -> str:
    name = csharp_string(condition["param"])
    value = f'(args["{name}"]?.ToString()??"")'
    if condition.get("missing") is True:
        return f"string.IsNullOrEmpty({value})"
    if "contains" in condition:
        expected = csharp_string(str(condition["contains"]))
        return (f'!string.IsNullOrEmpty({value})&&{value}.IndexOf("{expected}",'
                "StringComparison.OrdinalIgnoreCase)>=0")
    if "equals" in condition:
        expected = csharp_string(str(condition["equals"]))
        return (f'string.Equals({value},"{expected}",'
                "StringComparison.OrdinalIgnoreCase)")
    if "startsWith" in condition:
        expected = csharp_string(str(condition["startsWith"]))
        return (f'!string.IsNullOrEmpty({value})&&{value}.StartsWith("{expected}",'
                "StringComparison.OrdinalIgnoreCase)")
    raise PolicyBuildError(f"unsupported x-mock condition: {condition}")


def tool_call_branch(tool: ToolDefinition, first: bool) -> list[str]:
    prefix = "if" if first else "else if"
    lines = [f'{prefix}(toolName=="{csharp_string(tool.name)}"){{',
             " string payload=null;var isError=false;"]
    for index, rule in enumerate(mock_rules(tool)):
        respond = rule["respond"]
        payload = response_example(tool, respond)
        payload_expression = (csharp_string("null") if payload is None else
                      csharp_string(compact(payload)))
        condition = rule.get("when")
        if condition is None:
            branch = "else" if index else "if(true)"
        else:
            branch = ("if" if index == 0 else "else if") + \
                     f"({condition_expression(condition)})"
        lines += [f' {branch}{{payload="{payload_expression}";isError='
                  f"{str(int(respond['status']) >= 400).lower()};}}"]
    result = ('new JObject(new JProperty("content",new JArray(new JObject('
              'new JProperty("type","text"),new JProperty("text",payload)))), '
              'new JProperty("isError",isError))')
    lines += [f" {rpc_envelope(result)}", "}"]
    return lines


def tools_call_body(tools: list[ToolDefinition]) -> str:
    lines = ["@{", ' var request=(JObject)context.Variables["rpc"];',
             ' var toolName=(string)request.SelectToken("params.name");',
             ' var args=(JObject)request.SelectToken("params.arguments")??new JObject();']
    for index, tool in enumerate(tools):
        lines.extend(" " + value for value in tool_call_branch(tool, index == 0))
    lines += [" else{", f"  {error_envelope(-32602, 'Unknown tool')}", " }", "}"]
    return "\n".join(lines)


def add_json_response(parent, body: str, status: int = 200):
    response = ET.SubElement(parent, "return-response")
    ET.SubElement(response, "set-status", {
        "code": str(status), "reason": "Accepted" if status == 202 else "OK"})
    if status != 202:
        header = ET.SubElement(response, "set-header", {
            "name": "Content-Type", "exists-action": "override"})
        ET.SubElement(header, "value").text = "application/json"
        ET.SubElement(response, "set-body").text = body


def build_policy(server_name: str, tools: list[ToolDefinition]) -> str:
    if not tools:
        raise PolicyBuildError("an MCP server requires at least one tool")
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise PolicyBuildError(f"duplicate tools in server {server_name}: {names}")

    policies = ET.Element("policies")
    inbound = ET.SubElement(policies, "inbound")
    ET.SubElement(inbound, "base")
    ET.SubElement(inbound, "set-variable", {
        "name": "rpc", "value": "@(context.Request.Body.As<JObject>(preserveContent:true))"})
    ET.SubElement(inbound, "set-variable", {
        "name": "rpcMethod",
        "value": '@(((JObject)context.Variables["rpc"])["method"]?.ToString()??"")'})
    choose = ET.SubElement(inbound, "choose")

    methods = [
        ("initialize", initialize_body(server_name), 200),
        ("ping", ping_body(), 200),
        ("tools/list", tools_list_body(tools), 200),
        ("tools/call", tools_call_body(tools), 200),
    ]
    for method, body, status in methods:
        when = ET.SubElement(choose, "when", {
            "condition": f'@((string)context.Variables["rpcMethod"]=="{method}")'})
        add_json_response(when, body, status)
    notification = ET.SubElement(choose, "when", {
        "condition": '@(((string)context.Variables["rpcMethod"]).StartsWith("notifications/"))'})
    add_json_response(notification, "", 202)

    otherwise = ET.SubElement(choose, "otherwise")
    unknown = """@{
 var request=(JObject)context.Variables["rpc"];
 %s
}""" % error_envelope(-32601, "Method not found")
    add_json_response(otherwise, unknown)

    ET.SubElement(ET.SubElement(policies, "backend"), "base")
    ET.SubElement(ET.SubElement(policies, "outbound"), "base")
    on_error = ET.SubElement(policies, "on-error")
    response = ET.SubElement(on_error, "return-response")
    ET.SubElement(response, "set-status", {"code": "400", "reason": "Bad Request"})
    header = ET.SubElement(response, "set-header", {
        "name": "Content-Type", "exists-action": "override"})
    ET.SubElement(header, "value").text = "application/json"
    ET.SubElement(response, "set-body").text = compact({
        "jsonrpc": "2.0", "id": None,
        "error": {"code": -32700, "message": "Parse error"}})

    ET.indent(policies, space="  ")
    return ET.tostring(policies, encoding="unicode")


def policy_size(policy: str) -> int:
    return len(policy.encode("utf-8"))


def shard_tools(server_name: str, tools: list[ToolDefinition],
                limit: int = POLICY_LIMIT_BYTES) -> list[tuple[list[ToolDefinition], str]]:
    shards = []
    current = []
    for tool in tools:
        candidate = [*current, tool]
        policy = build_policy(server_name, candidate)
        if policy_size(policy) <= limit:
            current = candidate
            continue
        if not current:
            raise PolicyBuildError(
                f"tool '{tool.name}' generates {policy_size(policy)} bytes: exceeds limit {limit}")
        current_policy = build_policy(server_name, current)
        shards.append((current, current_policy))
        current = [tool]
        single = build_policy(server_name, current)
        if policy_size(single) > limit:
            raise PolicyBuildError(
                f"tool '{tool.name}' generates {policy_size(single)} bytes: exceeds limit {limit}")
    if current:
        shards.append((current, build_policy(server_name, current)))
    return shards


def load_client(repo_root: Path, client_dir: Path) -> tuple[dict, dict[str, list[ToolDefinition]]]:
    manifest = yaml.safe_load((client_dir / "mcp-manifest.yaml").read_text(encoding="utf-8"))
    tools_by_api = {}
    for api in manifest.get("apis", []):
        if (api.get("backend") or {}).get("mode") != "mock":
            raise PolicyBuildError(
                f"{manifest['client']}/{api['name']}: policy MCP supports mock backend only")
        spec_path = repo_root / "apis" / api["name"] / "openapi.yaml"
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        version = spec.get("openapi") if isinstance(spec, dict) else None
        if not isinstance(version, str) or not re.fullmatch(r"3\.0\.\d+", version):
            raise PolicyBuildError(
                f"{spec_path}: unsupported OpenAPI version '{version}'; "
                "version 3.0.x is required")
        requested = set(api.get("mcpTools", []))
        found = []
        for path, path_item in spec.get("paths", {}).items():
            path_parameters = path_item.get("parameters", []) if isinstance(path_item, dict) else []
            for verb, operation in path_item.items():
                if verb not in HTTP_VERBS or not isinstance(operation, dict):
                    continue
                if operation.get("operationId") in requested:
                    found.append(ToolDefinition(api["name"], path, verb, operation,
                                                path_parameters, spec))
        missing = requested - {tool.name for tool in found}
        if missing:
            raise PolicyBuildError(f"{api['name']}: missing tools {sorted(missing)}")
        tools_by_api[api["name"]] = found
    return manifest, tools_by_api


def desired_groups(manifest: dict, tools_by_api: dict[str, list[ToolDefinition]]):
    exposure = manifest.get("mcpExposure") or {}
    mode = exposure.get("mode", "perApi")
    groups = []
    if mode != "facade":
        groups.extend((api_name, tools) for api_name, tools in tools_by_api.items())
    if mode != "perApi":
        facade_name = exposure.get("facadeName", "agent")
        groups.append((facade_name, [tool for tools in tools_by_api.values() for tool in tools]))
    return groups


def build_client_plan(repo_root: Path, client_dir: Path,
                      limit: int = POLICY_LIMIT_BYTES) -> dict:
    manifest, tools_by_api = load_client(repo_root, client_dir)
    client = manifest["client"]
    servers = []
    for group_name, tools in desired_groups(manifest, tools_by_api):
        base_name = f"{client}-{group_name}-policy-mcp"
        shards = shard_tools(base_name, tools, limit)
        for index, (shard, policy) in enumerate(shards, start=1):
            suffix = f"-{index}" if len(shards) > 1 else ""
            resource_name = f"{base_name}{suffix}"
            path = f"{client}/{group_name}-policy-mcp{suffix}"
            servers.append({
                "resourceName": resource_name,
                "displayName": f"{manifest['displayName']} - {group_name}{suffix}",
                "path": path,
                "tools": [tool.name for tool in shard],
                "sourceApis": list(dict.fromkeys(tool.api_name for tool in shard)),
                "sizeBytes": policy_size(policy),
                "policy": policy,
            })
    return {"client": client, "displayName": manifest["displayName"],
            "limitBytes": limit, "servers": servers}


def write_client_plan(client_dir: Path, plan: dict) -> Path:
    output = client_dir / "generated" / "policy-mcp"
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("*.policy.xml"):
        old.unlink()
    serializable = {key: value for key, value in plan.items() if key != "servers"}
    serializable["servers"] = []
    for server in plan["servers"]:
        policy_file = f"{server['resourceName']}.policy.xml"
        (output / policy_file).write_text(
            server["policy"] + "\n", encoding="utf-8", newline="\n")
        serializable["servers"].append({
            key: value for key, value in server.items() if key != "policy"
        } | {"policyFile": policy_file})
    (output / "servers.json").write_text(
        json.dumps(serializable, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    (output / "client.bicep").write_text(
        emit_client_bicep(plan, serializable), encoding="utf-8", newline="\n")
    return output


def bicep_string(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("'", "\\'")
            .replace("$", "\\$").replace("\r\n", "\\n")
            .replace("\r", "\\n").replace("\n", "\\n"))


def bicep_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def emit_client_bicep(plan: dict, serializable: dict | None = None) -> str:
    servers = (serializable or plan)["servers"]
    client = plan["client"]
    lines = [
        "// GENERATED by tools/build-policy-mcp.py. DO NOT EDIT.",
        "param apimName string",
        "",
    ]
    module_names = []
    for server in servers:
        identifier = f"server_{bicep_identifier(server['resourceName'])}"
        module_names.append(identifier)
        policy_file = server.get("policyFile", f"{server['resourceName']}.policy.xml")
        lines += [
            f"module {identifier} '../../../../modules/policy-mcp-server.bicep' = {{",
            f"  name: 'policy-mcp-{bicep_string(server['resourceName'])}'",
            "  params: {",
            "    apimName: apimName",
            f"    resourceName: '{bicep_string(server['resourceName'])}'",
            f"    displayName: '{bicep_string(server['displayName'])}'",
            f"    apiPath: '{bicep_string(server['path'])}'",
            f"    productName: '{bicep_string(client)}-product'",
            f"    clientTag: '{bicep_string(client)}'",
            f"    policyXml: loadTextContent('{bicep_string(policy_file)}')",
            "  }",
            "}",
            "",
        ]
    lines += ["output serverUrls array = ["]
    lines += [f"  {name}.outputs.serverUrl" for name in module_names]
    lines += ["]", ""]
    return "\n".join(lines)


def emit_clients_index(client_ids: list[str]) -> str:
    lines = [
        "// GENERATED by tools/build-policy-mcp.py --all. DO NOT EDIT.",
        "param apimName string",
        "param enabled bool = false",
        "",
    ]
    previous = None
    for client in client_ids:
        identifier = f"client_{bicep_identifier(client)}"
        lines += [
            f"module {identifier} '../clients/{client}/generated/policy-mcp/client.bicep' = if (enabled) {{",
            f"  name: 'policy-mcp-client-{bicep_string(client)}'",
            "  params: { apimName: apimName }",
        ]
        if previous:
            lines.append(f"  dependsOn: [client_{bicep_identifier(previous)}]")
        lines += ["}", ""]
        previous = client
    lines += ["output serverUrls object = enabled ? {"]
    for client in client_ids:
        identifier = f"client_{bicep_identifier(client)}"
        lines.append(f"  {bicep_identifier(client)}: {identifier}!.outputs.serverUrls")
    lines += ["} : {}", ""]
    return "\n".join(lines)
