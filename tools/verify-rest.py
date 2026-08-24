#!/usr/bin/env python3
"""Smoke-test deployed REST mocks against their OpenAPI contracts."""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


def die(message: str):
    print(f"[verify-rest] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args: list[str]) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        die(f"command '{args[0]}' not found in PATH")
    process = subprocess.run([executable, *args[1:]], cwd=REPO_ROOT,
                             capture_output=True, text=True)
    if process.returncode != 0:
        die(f"command failed: {' '.join(args)}\n{process.stderr.strip()}")
    return process.stdout


def azd_env() -> dict[str, str]:
    values = {}
    for line in run(["azd", "env", "get-values"]).splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def resolve_ref(spec: dict, value):
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not ref.startswith("#/"):
        die(f"external $ref not supported in verification: {ref}")
    current = spec
    for part in ref[2:].split("/"):
        current = current[part.replace("~1", "/").replace("~0", "~")]
    return current


def response_for_status(operation: dict, status: int | str):
    for key, response in (operation.get("responses") or {}).items():
        if str(key) == str(status):
            return response
    return None


def sample_from_schema(spec: dict, schema: dict):
    schema = resolve_ref(spec, schema) or {}
    if "example" in schema:
        return schema["example"]
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


def parameter_value(spec: dict, parameter: dict):
    parameter = resolve_ref(spec, parameter)
    if "example" in parameter:
        return str(parameter["example"])
    return str(sample_from_schema(spec, parameter.get("schema", {})))


def mock_rules(operation: dict):
    rules = list(operation.get("x-mock") or [])
    if not rules or rules[-1].get("when") is not None:
        status = int(next(status for status in operation["responses"]
                          if str(status).isdigit()))
        rules.append({"respond": {"status": status}})
    return rules


def expected_response(spec: dict, operation: dict, selected: dict):
    status = selected["respond"]["status"]
    response = resolve_ref(spec, response_for_status(operation, status))
    if response is None:
        die(f"response status {status} is not declared")
    content = response.get("content") or {}
    if not content:
        return selected, status, None, None
    media_type = "application/json" if "application/json" in content else next(iter(content))
    media = content[media_type]
    example_name = (selected or {}).get("respond", {}).get("example")
    if example_name:
        payload = media["examples"][example_name]["value"]
    elif "example" in media:
        payload = media["example"]
    else:
        payload = next(iter(media["examples"].values()))["value"]
    return selected, status, media_type, payload


def apply_rule_value(rule: dict, parameters: dict[str, dict], values: dict[str, str]):
    condition = (rule or {}).get("when")
    if not condition:
        return
    name = condition["param"]
    if condition.get("missing") is True:
        values.pop(name, None)
        return
    if "equals" in condition:
        values[name] = condition["equals"]
    elif "contains" in condition:
        values[name] = f"test-{condition['contains']}-test"
    elif "startsWith" in condition:
        values[name] = f"{condition['startsWith']}test"
    if name not in parameters:
        die(f"x-mock references unknown parameter '{name}'")


def condition_matches(condition: dict, values: dict[str, str]):
    value = values.get(condition["param"])
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


def avoid_prior_rules(selected: dict, prior_rules: list[dict],
                      parameters: dict[str, dict], values: dict[str, str]):
    selected_condition = selected.get("when")
    for prior in prior_rules:
        condition = prior.get("when")
        if not condition or not condition_matches(condition, values):
            continue
        name = condition["param"]
        parameter = parameters[name]
        can_remove = parameter.get("in") != "path" and not parameter.get("required")
        candidates = ["unmatched-value", "z", "0", "branch-fallback"]
        if can_remove:
            candidates.append(None)
        original = values.get(name)
        for candidate in candidates:
            if candidate is None:
                values.pop(name, None)
            else:
                values[name] = candidate
            selected_matches = (not selected_condition or
                                condition_matches(selected_condition, values))
            if selected_matches and not any(
                    previous.get("when") and
                    condition_matches(previous["when"], values)
                    for previous in prior_rules):
                break
        else:
            if original is None:
                values.pop(name, None)
            else:
                values[name] = original
            die("x-mock branch is unreachable because an earlier rule always "
                f"matches parameter '{name}'")


def build_case(spec: dict, path: str, method: str, operation: dict,
               selected: dict, prior_rules: list[dict]):
    path_item = spec["paths"][path]
    raw_parameters = [*path_item.get("parameters", []),
                      *operation.get("parameters", [])]
    parameters = {resolve_ref(spec, item)["name"]: resolve_ref(spec, item)
                  for item in raw_parameters}
    values = {name: parameter_value(spec, parameter)
              for name, parameter in parameters.items()
              if parameter.get("required") or parameter.get("in") == "path"}
    apply_rule_value(selected, parameters, values)
    avoid_prior_rules(selected, prior_rules, parameters, values)
    _, status, media_type, expected = expected_response(spec, operation, selected)

    rendered_path = path
    query = {}
    headers = {}
    for name, parameter in parameters.items():
        value = values.get(name)
        if value is None:
            continue
        location = parameter.get("in")
        if location == "path":
            rendered_path = rendered_path.replace(
                "{" + name + "}", urllib.parse.quote(value, safe=""))
        elif location == "query":
            query[name] = value
        elif location == "header":
            headers[name] = value

    body = None
    request_body = resolve_ref(spec, operation.get("requestBody"))
    if request_body:
        content = request_body.get("content") or {}
        request_media = "application/json" if "application/json" in content else next(iter(content))
        media = content[request_media]
        if "example" in media:
            body = media["example"]
        elif media.get("examples"):
            body = next(iter(media["examples"].values()))["value"]
        else:
            body = sample_from_schema(spec, media.get("schema", {}))
        headers["Content-Type"] = request_media
    if query:
        rendered_path += "?" + urllib.parse.urlencode(query)
    return rendered_path, headers, body, status, media_type, expected


def iter_cases(manifest: dict):
    client = manifest["client"]
    exposure = manifest.get("mcpExposure") or {}
    mode = exposure.get("mode", "perApi")
    facade_name = exposure.get("facadeName", "agent")
    for api in manifest.get("apis", []):
        spec = yaml.safe_load((REPO_ROOT / "apis" / api["name"] / "openapi.yaml")
                              .read_text(encoding="utf-8"))
        bases = []
        if mode != "facade":
            bases.append(f"{client}/{api['name']}")
        if mode != "perApi":
            bases.append(f"{client}/{facade_name}")
        for path, path_item in spec.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in HTTP_VERBS or not isinstance(operation, dict):
                    continue
                rules = mock_rules(operation)
                for index, selected in enumerate(rules):
                    case = build_case(spec, path, method, operation, selected,
                                      rules[:index])
                    for base in bases:
                        yield api["name"], operation["operationId"], base, method, case


def pilot_key(client_id: str, env: dict[str, str]) -> str:
    if os.environ.get("REST_KEY"):
        return os.environ["REST_KEY"]
    normalized = {key.replace("_", "").lower(): value for key, value in env.items()}
    required = [normalized.get(name) for name in
                ("azuresubscriptionid", "azureresourcegroup", "apimname")]
    if not all(required):
        die("subscription/RG/apimName not found in azd environment; "
            "run azd up or provide REST_KEY")
    subscription, resource_group, apim = required
    return run(["az", "rest", "--method", "POST", "--uri",
                f"/subscriptions/{subscription}/resourceGroups/{resource_group}/"
                f"providers/Microsoft.ApiManagement/service/{apim}/subscriptions/"
                f"{client_id}-pilot/listSecrets?api-version=2024-06-01-preview",
                "--query", "primaryKey", "-o", "tsv"]).strip()


def invoke(url: str, method: str, headers: dict, body):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method.upper())
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    raw = response.read().decode()
    payload = json.loads(raw) if raw else None
    return response.status, response.headers.get_content_type(), payload


def main():
    if len(sys.argv) != 2:
        die("usage: verify-rest.py clients/<clientId>")
    client_dir = REPO_ROOT / sys.argv[1]
    manifest_path = client_dir / "mcp-manifest.yaml"
    if not manifest_path.exists():
        die(f"{sys.argv[1]}: mcp-manifest.yaml not found")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    env = azd_env()
    normalized = {key.replace("_", "").lower(): value for key, value in env.items()}
    gateway = normalized.get("apimgatewayurl") or (
        f"https://{normalized['apimname']}.azure-api.net"
        if normalized.get("apimname") else die("apimGatewayUrl/apimName missing"))
    key = pilot_key(manifest["client"], env)

    cases = list(iter_cases(manifest))
    print(f"[verify-rest] {manifest['client']}: {len(cases)} expected calls on {gateway}")
    failed = False
    for api_name, operation_id, base, method, case in cases:
        path, headers, body, expected_status, expected_media, expected_payload = case
        headers["Ocp-Apim-Subscription-Key"] = key
        try:
            status, media_type, payload = invoke(f"{gateway}/{base}{path}",
                                                 method, headers, body)
            mismatches = []
            if status != expected_status:
                mismatches.append(f"status {status}, expected {expected_status}")
            if expected_media and media_type != expected_media:
                mismatches.append(f"content-type {media_type}, expected {expected_media}")
            if payload != expected_payload:
                mismatches.append("payload differs from example")
            if mismatches:
                failed = True
                print(f"  [FAIL] {api_name}/{operation_id} via {base}: "
                      + "; ".join(mismatches))
            else:
                print(f"  [OK]   {api_name}/{operation_id} via {base}: {status}")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            failed = True
            print(f"  [FAIL] {api_name}/{operation_id} via {base}: {error}")
    if failed:
        die("one or more REST mocks do not comply with the contract")
    print("[verify-rest] RESULT: all REST mocks comply with the contract")


if __name__ == "__main__":
    main()