#!/usr/bin/env python3
"""Verify REST mock examples or explicitly reviewed contract fixtures on an approved gateway.

See docs/extended-verification.md for private authentication and fixture authorization.
"""
import http.client
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
from mcp_openapi_creator_kit._commands.verification import (
    Credentials, PrivateArgumentParser, VerificationFailure, add_extended_arguments, decode_json, endpoint_url, explicit_credentials,
    fixture_cases, fixture_endpoint, gateway_origin, open_request, read_response, review_fixtures,
    safe_error, validate_manifest,
)

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
        die("command failed; inspect CLI diagnostics privately (raw output suppressed)")
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
        die("external $ref not supported in verification")
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


def iter_cases(manifest: dict, *, requested_auth=None):
    validate_manifest(manifest, mock_only=True, requested_auth=requested_auth)
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


def invoke(url: str, method: str, headers: dict, body, *, body_present=False):
    data = json.dumps(body).encode() if body is not None or body_present else None
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method.upper())
    try:
        response = open_request(request, timeout=30)
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            error.close()
            raise RuntimeError("Redirect refused") from None
        response = error
    with response:
        try:
            raw = read_response(response)
            payload = decode_json(raw) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise VerificationFailure(
                f"HTTP {response.status}: response is not UTF-8 JSON (body suppressed)") from None
        return response.status, response.headers.get_content_type(), payload


def verify_fixtures(gateway, credentials, manifest, cases):
    prepared = []
    for case in cases:
        _, headers = case.request()
        prepared.append((case, endpoint_url(gateway, fixture_endpoint(manifest, case)),
                         credentials.headers(headers)))
    for case, url, headers in prepared:
        status, media, payload = invoke(url, case.method, headers, case.body, body_present=case.has_body)
        if status != case.status or media != case.media:
            raise VerificationFailure("Fixture HTTP status or content type differs from the selected contract response")
        case.assert_payload(payload)
        print(f"  [OK] {case.label}: HTTP {status}, contract schema and example")
    print(f"[verify-rest] RESULT: {len(cases)} explicitly authorized fixture calls verified")


def main():
    parser = PrivateArgumentParser(prog="mcp-kit verify-rest", description=__doc__)
    parser.add_argument("client", help="client directory")
    parser.add_argument("--gateway-url", help="HTTPS gateway origin; private environment credentials, no azd/Azure discovery")
    add_extended_arguments(parser)
    args = parser.parse_args()
    explicit = args.gateway_url is not None
    if (args.auth_mode or args.fixture or args.confirm_fixtures is not None) and not explicit:
        die("extended verification requires --gateway-url; no azd fallback")
    if args.confirm_fixtures is not None and not args.fixture:
        die("--confirm-fixtures requires an explicit --fixture allowlist")
    try:
        if explicit:
            gateway = gateway_origin(args.gateway_url)
    except ValueError as error:
        die(str(error))
    client_dir = REPO_ROOT / args.client
    if __package__:
        from .deployment import client_path
        client_dir = client_path(REPO_ROOT, args.client)
    manifest_path = client_dir / "mcp-manifest.yaml"
    if not manifest_path.exists():
        die(f"{args.client}: mcp-manifest.yaml not found")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    try:
        validate_manifest(manifest, mock_only=not args.fixture, requested_auth=args.auth_mode)
    except ValueError as error:
        die(str(error))
    if args.fixture:
        try:
            cases = fixture_cases(REPO_ROOT, manifest, args.fixture)
            if not review_fixtures(REPO_ROOT, manifest, gateway, args.auth_mode, cases, args.confirm_fixtures):
                return
            credentials = explicit_credentials(manifest, args.auth_mode)
            verify_fixtures(gateway, credentials, manifest, cases)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError,
                yaml.YAMLError, http.client.HTTPException) as error:
            die(safe_error(error))
        return
    if explicit:
        try:
            credentials = explicit_credentials(manifest, args.auth_mode)
        except ValueError as error:
            die(str(error))
    else:
        env = azd_env()
        normalized = {key.replace("_", "").lower(): value for key, value in env.items()}
        gateway = normalized.get("apimgatewayurl") or (
            f"https://{normalized['apimname']}.azure-api.net"
            if normalized.get("apimname") else die("apimGatewayUrl/apimName missing"))
        try:
            gateway = gateway_origin(gateway)
        except ValueError as error:
            die(str(error))
        credentials = Credentials(pilot_key(manifest["client"], env))

    cases = list(iter_cases(manifest, requested_auth=args.auth_mode))
    if not cases:
        die("No REST verification cases found; check the selected contracts and operations")
    try:
        for _, _, base, _, case in cases:
            credentials.headers(case[1])
            endpoint_url(gateway, f"{base}{case[0]}")
    except ValueError as error:
        die(str(error))
    print(f"[verify-rest] {manifest['client']}: {len(cases)} expected calls on {gateway}")
    failed = False
    for api_name, operation_id, base, method, case in cases:
        path, headers, body, expected_status, expected_media, expected_payload = case
        headers = credentials.headers(headers)
        try:
            status, media_type, payload = invoke(endpoint_url(gateway, f"{base}{path}"),
                                                 method, headers, body)
            mismatches = []
            if status != expected_status:
                mismatches.append(f"status {status}, expected {expected_status}")
            if expected_media and media_type != expected_media:
                mismatches.append(f"content-type differs, expected {expected_media}")
            if payload != expected_payload:
                mismatches.append("payload differs from example")
            if mismatches:
                failed = True
                print(f"  [FAIL] {api_name}/{operation_id} via {base}: "
                      + "; ".join(mismatches))
            else:
                print(f"  [OK]   {api_name}/{operation_id} via {base}: {status}")
        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
            failed = True
            print(f"  [FAIL] {api_name}/{operation_id} via {base}: {safe_error(error)}")
    if failed:
        die("one or more REST mocks do not comply with the contract")
    print("[verify-rest] RESULT: all REST mocks comply with the contract")


if __name__ == "__main__":
    main()