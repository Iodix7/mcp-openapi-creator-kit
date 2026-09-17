"""Shared safety checks for data-plane verification (no Azure discovery)."""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

import jsonschema
import yaml
from referencing import Registry
from referencing.exceptions import Unresolvable


class VerificationFailure(ValueError):
    """A deliberately public diagnostic, never a provider response or credential."""


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid verifier arguments; run --help. Credentials belong only in the private environment.\n")


@dataclass(frozen=True)
class Credentials:
    key: str = field(repr=False)
    bearer: str | None = field(default=None, repr=False)

    def headers(self, supplied=None):
        headers = dict(supplied or {})
        if any(not isinstance(name, str) for name in headers):
            raise VerificationFailure("Contract header names must be strings")
        protected = {"authorization", "ocp-apim-subscription-key", "host",
                     "proxy-authorization", "cookie", "connection", "transfer-encoding",
                     "content-length", "mcp-session-id", "mcp-protocol-version"}
        if any(name.lower() in protected for name in headers):
            raise VerificationFailure("Contract headers must not override authentication or transport headers")
        for name, value in headers.items():
            if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                    or not isinstance(value, str)
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                raise VerificationFailure("Contract contains an unsupported HTTP header")
        headers["Ocp-Apim-Subscription-Key"] = self.key
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        return headers

    def redact(self, value):
        for secret in (self.key, self.bearer):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value


def add_extended_arguments(parser):
    parser.add_argument("--auth-mode", choices=("subscriptionKey", "entraJwt", "dual"),
                        help="explicit manifest-matched auth; entraJwt/dual requires MCP_KEY + MCP_BEARER_TOKEN")
    parser.add_argument("--fixture", action="append", default=[], metavar="API/OPERATION/STATUS/EXAMPLE",
                        help="preview only these named contract fixtures; repeat for an exact allowlist")
    parser.add_argument("--confirm-fixtures", metavar="REVIEW_TOKEN",
                        help="authorize only the exact reviewed fixture plan; no automatic retries")


def auth_mode(manifest, requested=None):
    if not isinstance(manifest, dict):
        raise VerificationFailure("Manifest must be a mapping")
    inbound = manifest.get("inboundAuth") or {}
    declared = inbound.get("mode", "subscriptionKey") if isinstance(inbound, dict) else None
    if requested is None and declared != "subscriptionKey":
        raise VerificationFailure("Default verification supports inboundAuth.mode=subscriptionKey only; "
                                  "explicit --auth-mode entraJwt (or dual) is required. No token is acquired.")
    effective = "entraJwt" if requested == "dual" else requested or "subscriptionKey"
    if declared not in ("subscriptionKey", "entraJwt") or effective != declared:
        raise VerificationFailure("--auth-mode does not match the supported manifest inboundAuth.mode")
    if effective == "entraJwt":
        config = inbound.get("entraJwt") or {}
        if (not isinstance(config, dict) or not config.get("tenantId")
                or not config.get("audience")):
            raise VerificationFailure("entraJwt requires manifest tenantId and audience; no application is created")
    return effective


def explicit_credentials(manifest, requested=None):
    mode = auth_mode(manifest, requested)
    key = explicit_key()
    bearer = None
    if mode == "entraJwt":
        bearer = os.environ.get("MCP_BEARER_TOKEN", "")
        if not bearer or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", bearer):
            raise VerificationFailure("Explicit entraJwt/dual verification requires a valid MCP_BEARER_TOKEN "
                                      "environment variable; obtain it through your approved process")
    return Credentials(key, bearer)


def gateway_origin(value: str) -> str:
    message = ("--gateway-url must be an HTTPS origin, e.g. https://gateway.example.com "
               "(optional port; no credentials, API path, query or fragment)")
    try:
        if (not value or re.search(r"[\s\\%?#]", value)
                or not value.isascii()):
            raise ValueError(message)
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.port == 0 or parsed.netloc.endswith(":")):
            raise ValueError(message)
        if ":" in host:
            ipaddress.IPv6Address(host)
            if not re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]+)?", parsed.netloc):
                raise ValueError(message)
        elif not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                     for label in host.split(".")):
            raise ValueError(message)
        return urllib.parse.urlunsplit(("https", parsed.netloc, "", "", ""))
    except ValueError:
        raise ValueError(message) from None


def endpoint_url(gateway: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(path)
    decoded = urllib.parse.unquote(parsed.path)
    if (parsed.scheme or parsed.netloc or parsed.fragment or path.startswith("/")
            or re.search(r"[\s\\#]", path)
            or "\\" in decoded or any(p in (".", "..") for p in decoded.split("/"))):
        raise ValueError("Unsafe endpoint path; rebuild artifacts from the validated manifest")
    return f"{gateway_origin(gateway)}/{path}"


def explicit_key() -> str:
    key = os.environ.get("MCP_KEY", "")
    if not key or key.strip() != key or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise VerificationFailure("Explicit endpoint mode requires a valid MCP_KEY environment variable; "
                                  "obtain the product subscription key through your approved process. "
                                  "No azd or Azure management fallback is performed.")
    return key


def validate_manifest(manifest: dict, *, mock_only: bool, consumption: bool = False,
                      requested_auth=None):
    auth_mode(manifest, requested_auth)
    apis = manifest.get("apis")
    if not isinstance(apis, list) or not apis:
        raise ValueError("Manifest must contain APIs to verify")
    for api in apis:
        if not isinstance(api, dict) or not isinstance(api.get("backend"), dict):
            raise VerificationFailure("Every API requires an explicit backend mode")
        backend = (api.get("backend") or {}).get("mode")
        if backend not in ("mock", "external") or (mock_only and backend != "mock"):
            raise ValueError("This verification requires backend.mode=mock for every API; "
                             "external backends are not invoked automatically. "
                             "Use native MCP discovery or a separately approved read-only client.")
    if consumption and manifest.get("networkProfile", "public") != "public":
        raise ValueError("Consumption verification requires networkProfile=public")


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise RuntimeError("Redirect refused; confirm the HTTPS gateway origin with the operator")


def open_request(request, timeout=30):
    parsed = urllib.parse.urlsplit(request.full_url)
    gateway_origin(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    if parsed.fragment:
        raise ValueError("Request fragments are not supported")
    response = urllib.request.build_opener(NoRedirects()).open(request, timeout=timeout)
    if 300 <= response.status < 400:
        response.close()
        raise RuntimeError("Redirect refused; confirm the HTTPS gateway origin with the operator")
    return response


def safe_error(error: Exception) -> str:
    if isinstance(error, VerificationFailure):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}; check endpoint and product authentication privately"
    if isinstance(error, urllib.error.URLError):
        return "HTTPS request failed; check TLS, DNS and network reachability"
    if isinstance(error, TimeoutError):
        return "HTTPS request timed out; invocation outcome may be unknown (no automatic retry)"
    return "Invalid response or refused redirect; check the endpoint privately (response details suppressed)"


def local_ref(document, value, seen=()):
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise VerificationFailure("Only non-cyclic local contract references are supported")
        seen = (*seen, ref)
        try:
            value = document
            for part in ref[2:].split("/"):
                value = value[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise VerificationFailure("Contract reference cannot be resolved locally") from None
    return value


def contract_schema(document, schema, *, request=False, seen=(), depth=0):
    if depth > 64:
        raise VerificationFailure("Contract schema exceeds the supported reference/depth bound")
    if isinstance(schema, list):
        return [contract_schema(document, v, request=request, seen=seen, depth=depth + 1)
                for v in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            raise VerificationFailure("Recursive contract schemas are not supported by extended verification")
        return contract_schema(document, local_ref(document, schema), request=request,
                               seen=(*seen, ref), depth=depth + 1)
    result = {}
    for key, value in schema.items():
        if key in ("nullable", "example", "examples", "xml", "externalDocs"):
            continue
        if key in ("properties", "patternProperties", "definitions", "$defs") and isinstance(value, dict):
            result[key] = {name: contract_schema(document, child, request=request, seen=seen, depth=depth + 1)
                           for name, child in value.items()}
        elif key in ("items", "additionalProperties", "not", "allOf", "anyOf", "oneOf"):
            result[key] = contract_schema(document, value, request=request, seen=seen, depth=depth + 1)
        else:
            result[key] = value
    if "required" in result and isinstance(result.get("properties"), dict):
        annotation = "readOnly" if request else "writeOnly"
        result["required"] = [name for name in result["required"]
                              if not result["properties"].get(name, {}).get(annotation)]
        if not result["required"]:
            del result["required"]
    if schema.get("nullable") is True:
        if isinstance(result.get("type"), str):
            result["type"] = [result["type"], "null"]
        else:
            result = {"anyOf": [result, {"type": "null"}]}
    return result


def schema_validator(schema, *, mcp=False):
    def check_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("const", "enum", "default", "example", "examples"):
                    continue
                if key in ("$ref", "$dynamicRef") and (
                        not isinstance(child, str) or not child.startswith("#")):
                    raise VerificationFailure("Remote schema references are not permitted during verification")
                if key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
                    if isinstance(child, dict):
                        for definition in child.values():
                            check_refs(definition)
                else:
                    check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)
    check_refs(schema)
    validator = jsonschema.Draft202012Validator if mcp else jsonschema.Draft4Validator
    try:
        validator.check_schema(schema)
        return validator(schema, format_checker=jsonschema.FormatChecker(), registry=Registry())
    except (jsonschema.SchemaError, TypeError, RecursionError):
        raise VerificationFailure("Declared verification schema is invalid (details suppressed)") from None


def validate_value(value, schema, *, mcp=False):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise VerificationFailure("Verification values must be finite JSON data (details suppressed)") from None
    try:
        invalid = next(schema_validator(schema, mcp=mcp).iter_errors(value), None)
    except (Unresolvable, RecursionError):
        raise VerificationFailure("Declared schema cannot be resolved safely") from None
    if invalid is not None:
        raise VerificationFailure("Value violates the declared schema (instance and schema details suppressed)")


def exact_json(actual, expected):
    # Python equality alone equates True with 1 and does not represent JSON types.
    return json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(
        expected, sort_keys=True, allow_nan=False)


def read_response(response):
    raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise VerificationFailure("Response exceeds the 2 MiB verification limit")
    return raw.decode("utf-8")


def decode_json(text):
    def constant(_):
        raise VerificationFailure("Response contains a non-finite JSON number")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise VerificationFailure("Response contains duplicate JSON object keys")
            result[key] = value
        return result
    return json.loads(text, parse_constant=constant, object_pairs_hook=pairs)


def rpc_message(body, request_id):
    messages = []
    if any(line.startswith("data:") for line in body.splitlines()):
        data = []
        for line in [*body.splitlines(), ""]:
            if line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
            elif not line and data:
                messages.append(decode_json("\n".join(data)))
                data = []
    elif body.strip():
        messages.append(decode_json(body))
    if request_id is None:
        if messages:
            raise VerificationFailure("MCP notification unexpectedly returned a message")
        return None
    matching = [m for m in messages if isinstance(m, dict)
                and type(m.get("id")) is type(request_id) and m.get("id") == request_id]
    if len(matching) != 1 or matching[0].get("jsonrpc") != "2.0":
        raise VerificationFailure("MCP response lacks one matching JSON-RPC 2.0 response ID")
    message = matching[0]
    if "error" in message or "method" in message or not isinstance(message.get("result"), dict):
        raise VerificationFailure("MCP returned a protocol error or invalid result (provider details suppressed)")
    return message


def tool_payload(response, descriptor):
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        raise VerificationFailure("MCP tools/call did not return a CallToolResult")
    if type(result.get("isError", False)) is not bool or result.get("isError", False):
        raise VerificationFailure("MCP tools/call reported an error (provider details suppressed)")
    content = result.get("content")
    if not isinstance(content, list) or any(
            not isinstance(block, dict) or block.get("type") != "text"
            or not isinstance(block.get("text"), str) for block in content):
        raise VerificationFailure("Unsupported MCP content: extended assertions require text and/or structuredContent")
    if "structuredContent" in result:
        payload = result["structuredContent"]
        if not isinstance(payload, dict):
            raise VerificationFailure("MCP structuredContent must be an object")
        for block in content:
            try:
                text_payload = decode_json(block["text"])
            except json.JSONDecodeError:
                continue  # Structured content is authoritative; human-readable summaries are allowed.
            if not exact_json(text_payload, payload):
                raise VerificationFailure("MCP structuredContent and JSON text disagree")
    else:
        if "outputSchema" in descriptor:
            raise VerificationFailure("MCP tool advertised outputSchema but omitted structuredContent")
        if len(content) != 1:
            raise VerificationFailure("MCP result requires exactly one JSON text block when structuredContent is absent")
        try:
            payload = decode_json(content[0]["text"])
        except json.JSONDecodeError:
            raise VerificationFailure("MCP text result is not JSON; unsupported wrapper (details suppressed)") from None
    if "outputSchema" in descriptor:
        validate_value(payload, descriptor["outputSchema"], mcp=True)
    return payload


@dataclass(frozen=True)
class Case:
    api: str
    operation: str
    label: str
    method: str
    path: str
    parameters: tuple
    body: object
    has_body: bool
    status: int
    media: str
    expected: object
    schema: dict
    mock: bool = False

    def assert_payload(self, payload):
        validate_value(payload, self.schema)
        if not exact_json(payload, self.expected):
            raise VerificationFailure("Payload differs from the selected contract example (details suppressed)")

    def request(self):
        path, query, headers = self.path, {}, {}
        for location, name, value in self.parameters:
            if isinstance(value, (dict, list)) or value is None:
                raise VerificationFailure("Extended HTTP fixtures support non-null scalar parameters only")
            value = str(value).lower() if isinstance(value, bool) else str(value)
            if location == "path":
                path = path.replace("{" + name + "}", urllib.parse.quote(value, safe=""))
            elif location == "query":
                query[name] = value
            elif location == "header":
                if name.lower() in ("content-type", "accept"):
                    raise VerificationFailure("Fixture content negotiation comes from the contract media, not header examples")
                headers[name] = value
            else:
                raise VerificationFailure("Extended fixtures support path, query and header parameters only")
        if "{" in path or "}" in path or not path.startswith("/") or path.startswith("//"):
            raise VerificationFailure("Contract path could not be rendered safely")
        if self.has_body:
            headers["Content-Type"] = "application/json"
        Credentials("validation-only").headers(headers)
        if query:
            path += "?" + urllib.parse.urlencode(query)
        return path, headers


def contracts(root, manifest):
    result = {}
    for api in manifest["apis"]:
        name = api.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in result:
            raise VerificationFailure("Manifest API names must be unique validated slugs")
        path = root / "apis" / name / "openapi.yaml"
        if not path.resolve().is_relative_to(root.resolve()):
            raise VerificationFailure("Contract path must remain inside the customer workspace")
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3.0."):
            raise VerificationFailure("Extended verification requires OpenAPI 3.0 contracts")
        result[name] = (api, spec)
    return result


def operation_parameters(spec, path_item, operation):
    parameters = {}
    for raw in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
        parameter = local_ref(spec, raw)
        key = (parameter["in"], parameter["name"])
        parameters[key] = parameter
    return parameters


def json_media(content):
    choices = [(name, value) for name, value in content.items()
               if name == "application/json" or name.startswith("application/") and name.endswith("+json")]
    if len(choices) != 1:
        raise VerificationFailure("Extended fixtures require exactly one JSON media representation")
    return choices[0]


def named_example(spec, media, name):
    example = local_ref(spec, (media.get("examples") or {}).get(name))
    if not isinstance(example, dict) or "value" not in example or "externalValue" in example:
        raise VerificationFailure("Fixture requires a local named example.value; no defaults or externalValue are used")
    return example["value"]


def fixture_cases(root, manifest, selectors, *, mcp=False):
    if not selectors or len(set(selectors)) != len(selectors):
        raise VerificationFailure("Select a nonempty, duplicate-free fixture allowlist")
    specs = contracts(root, manifest)
    cases = []
    for selector in sorted(selectors):
        parts = selector.split("/")
        if (len(parts) != 4 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", p) for p in parts)
                or not re.fullmatch(r"[1-5][0-9]{2}", parts[2])):
            raise VerificationFailure("--fixture must be API/OPERATION/STATUS/EXAMPLE using simple identifiers")
        api_name, operation_id, status, example = parts
        if api_name not in specs:
            raise VerificationFailure("Fixture API is not selected in the manifest")
        api, spec = specs[api_name]
        matches = [(path, item, method, op) for path, item in spec.get("paths", {}).items()
                   for method, op in item.items() if method in ("get", "post", "put", "patch", "delete", "head", "options")
                   and isinstance(op, dict) and op.get("operationId") == operation_id]
        if len(matches) != 1 or mcp and operation_id not in api.get("mcpTools", []):
            raise VerificationFailure("Fixture operation must resolve uniquely and be selected for this transport")
        if mcp and not 200 <= int(status) < 300:
            raise VerificationFailure("Native MCP fixtures support successful 2xx examples only; use REST to assert HTTP errors")
        path, item, method, operation = matches[0]
        response = next((local_ref(spec, value) for key, value in operation.get("responses", {}).items()
                         if str(key) == status), None)
        if not response:
            raise VerificationFailure("Fixture response status is not declared in the contract")
        media_type, media = json_media(response.get("content") or {})
        if "schema" not in media:
            raise VerificationFailure("Fixture response requires an explicit contract schema")
        expected = named_example(spec, media, example)
        schema = contract_schema(spec, media["schema"])
        validate_value(expected, schema)
        parameters = []
        for (location, name), parameter in operation_parameters(spec, item, operation).items():
            if example not in (parameter.get("examples") or {}):
                if parameter.get("required") or location == "path":
                    raise VerificationFailure("Every required fixture parameter needs the selected named example")
                continue
            style = {"path": "simple", "query": "form", "header": "simple"}.get(location)
            if style is None or parameter.get("style", style) != style or parameter.get("allowReserved"):
                raise VerificationFailure("Extended fixtures do not support this parameter serialization")
            value = named_example(spec, parameter, example)
            validate_value(value, contract_schema(spec, parameter.get("schema", {}), request=True))
            parameters.append((location, name, value))
        body, has_body = None, False
        request_body = local_ref(spec, operation.get("requestBody"))
        if request_body:
            request_media_type, request_media = json_media(request_body.get("content") or {})
            if request_media_type != "application/json":
                raise VerificationFailure("Extended fixture request bodies require application/json")
            if example in (request_media.get("examples") or {}) or request_body.get("required"):
                body = named_example(spec, request_media, example)
                validate_value(body, contract_schema(spec, request_media.get("schema", {}), request=True))
                has_body = True
        case = Case(api_name, operation_id, selector, method, path, tuple(parameters), body,
                    has_body, int(status), media_type, expected, schema)
        case.request()
        cases.append(case)
    return cases


def fixture_base(manifest, case):
    exposure = manifest.get("mcpExposure") or {}
    mode = exposure.get("mode", "perApi")
    if mode not in ("perApi", "facade", "both"):
        raise VerificationFailure("Unsupported manifest exposure mode")
    name = case.api if mode == "perApi" else exposure.get("facadeName", "agent")
    return f"{manifest['client']}/{name}"


def fixture_endpoint(manifest, case, *, mcp=False):
    base = fixture_base(manifest, case)
    if mcp:
        return f"{base}-mcp/mcp"
    path, _ = case.request()
    return f"{base}{path}"


def review_fixtures(root, manifest, gateway, requested_auth, cases, confirmation, *, mcp=False):
    from pathlib import Path

    commands = Path(__file__).resolve().parent
    implementation = hashlib.sha256()
    for name in ("verification.py", "verify-mcp.py" if mcp else "verify-rest.py"):
        implementation.update((commands / name).read_bytes())
    scope = {"version": 1, "transport": "native-mcp" if mcp else "rest",
             "implementation": implementation.hexdigest(),
             "gateway": gateway, "authMode": auth_mode(manifest, requested_auth), "manifest": manifest,
             "contracts": {name: spec for name, (_, spec) in contracts(root, manifest).items()},
             "compiledCases": [asdict(case) for case in cases],
             "cases": [{"fixture": case.label, "endpoint": endpoint_url(
                 gateway, fixture_endpoint(manifest, case, mcp=mcp))} for case in cases]}
    token = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":"), allow_nan=False)
                           .encode("utf-8")).hexdigest()
    if confirmation is not None:
        if confirmation != token:
            raise VerificationFailure("Fixture review token does not match this origin, auth mode, manifest and contract scope")
        return True
    print(json.dumps({"mode": "fixture-preview", "networkCalls": 0, "gateway": gateway,
                      "authMode": scope["authMode"], "transport": scope["transport"],
                      "fixtures": [{"id": case.label, "method": case.method.upper(),
                                    "responseStatus": case.status,
                                    "route": (fixture_endpoint(manifest, case, mcp=True) if mcp else
                                              fixture_base(manifest, case) + case.path)}
                                   for case in cases],
                      "reviewToken": token}, indent=2))
    return False


def native_arguments(case, descriptor):
    schema = descriptor.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise VerificationFailure("Native tool requires an explicit object inputSchema")
    validator = schema_validator(schema, mcp=True)
    if "outputSchema" in descriptor:
        if not isinstance(descriptor["outputSchema"], dict):
            raise VerificationFailure("MCP outputSchema must be a schema object")
        schema_validator(descriptor["outputSchema"], mcp=True)
        validate_value(case.expected, descriptor["outputSchema"], mcp=True)
    properties = schema.get("properties", {})
    arguments = {}
    for location, name, value in case.parameters:
        if case.mock and location == "header" and name.lower() == "idempotency-key" and name not in properties:
            continue
        if name not in properties or name in arguments:
            raise VerificationFailure("Native parameter layout differs from the contract; no binding is guessed")
        arguments[name] = value
    candidates = [arguments] if not case.has_body else []
    if case.has_body:
        for name in ("body", "requestBody"):
            if name in properties and name not in arguments:
                candidates.append({**arguments, name: case.body})
        if (isinstance(case.body, dict) and not set(arguments).intersection(case.body)
                and set(case.body).issubset(properties)):
            candidates.append({**arguments, **case.body})
    accepted = []
    try:
        for candidate in candidates:
            if validator.is_valid(candidate) and not any(exact_json(candidate, prior) for prior in accepted):
                accepted.append(candidate)
    except (Unresolvable, RecursionError):
        raise VerificationFailure("Native input schema cannot be resolved safely") from None
    if len(accepted) != 1:
        raise VerificationFailure("Native input/body binding is unsupported, invalid or ambiguous; no layout is guessed")
    return accepted[0]


def mock_native_cases(root, manifest):
    from mcp_openapi_creator_kit.policy import (
        ToolDefinition, hidden_parameters, mock_rules, sample_tool_calls, tool_input_schema,
    )
    cases, selected_names, found_names = [], [], set()
    for api_name, (api, spec) in contracts(root, manifest).items():
        if api["backend"]["mode"] != "mock":
            raise VerificationFailure("--exercise-mock requires every backend to be mock; external calls need reviewed fixtures")
        selected_names.extend(api.get("mcpTools", []))
        for path, item in spec.get("paths", {}).items():
            for method, operation in item.items():
                if (method not in ("get", "post", "put", "patch", "delete", "head", "options")
                        or not isinstance(operation, dict)
                        or operation.get("operationId") not in api.get("mcpTools", [])):
                    continue
                name = operation["operationId"]
                if name in found_names:
                    raise VerificationFailure("Selected MCP operation IDs must be unique")
                found_names.add(name)
                tool = ToolDefinition(api_name, path, method, operation, item.get("parameters", []), spec)
                tool_input_schema(tool)  # Reuse the generator's collision checks before flattening examples.
                hidden = {p["name"] for p in hidden_parameters(tool)}
                rules = [rule for rule in mock_rules(tool) if not rule.get("when")
                         or rule["when"]["param"] not in hidden]
                successful = 0
                for index, (rule, (arguments, expected, _)) in enumerate(
                        zip(rules, sample_tool_calls(tool), strict=True), start=1):
                    status = rule["respond"]["status"]
                    if not 200 <= status < 300:
                        continue
                    response = next(local_ref(spec, value) for key, value in operation["responses"].items()
                                    if str(key) == str(status))
                    media_type, media = json_media(response.get("content") or {})
                    if "schema" not in media:
                        raise VerificationFailure("Native example assertions require a response schema")
                    schema = contract_schema(spec, media["schema"])
                    validate_value(expected, schema)
                    parameters = []
                    definitions = operation_parameters(spec, item, operation)
                    for (location, parameter_name), parameter in definitions.items():
                        if parameter_name in arguments:
                            value = arguments[parameter_name]
                        elif parameter_name in hidden and "example" in parameter:
                            value = parameter["example"]
                        else:
                            continue
                        validate_value(value, contract_schema(spec, parameter.get("schema", {}), request=True))
                        parameters.append((location, parameter_name, value))
                    request_body = local_ref(spec, operation.get("requestBody"))
                    body, has_body = None, request_body is not None
                    if has_body:
                        request_media_type, request_media = json_media(request_body.get("content") or {})
                        if request_media_type != "application/json":
                            raise VerificationFailure("Native request examples require application/json")
                        body_schema = contract_schema(spec, request_media.get("schema", {}), request=True)
                        parameter_names = {parameter_name for _, parameter_name in definitions}
                        if body_schema.get("type") == "object" or body_schema.get("properties"):
                            body = {key: value for key, value in arguments.items() if key not in parameter_names}
                        else:
                            body = arguments.get("body")
                        validate_value(body, body_schema)
                    case = Case(api_name, name, f"{api_name}/{name}/mock-{index}", method, path,
                                tuple(parameters), body, has_body, status, media_type, expected, schema, mock=True)
                    case.request()
                    cases.append(case)
                    successful += 1
                if not successful:
                    raise VerificationFailure("Each selected native tool requires a model-reachable successful JSON example")
    if not cases or len(selected_names) != len(found_names) or set(selected_names) != found_names:
        raise VerificationFailure("Native example coverage does not match the selected MCP tools")
    return cases
