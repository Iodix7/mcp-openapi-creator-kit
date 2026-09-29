"""Bounded, opt-in REST mock semantics. Contracts supply data, never policy code."""
from __future__ import annotations

import base64
import json
import re
from http import HTTPStatus
from typing import Annotated, Literal
from xml.etree import ElementTree as ET

from pydantic import Field, StringConstraints, ValidationError

from .targets import StrictModel, TargetError

EXTENSION = "x-kit-runtime"
STANDARD_KEY_HEADER = "Ocp-Apim-Subscription-Key"
UUID_PATTERN = r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Header = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,127}$")]


class Correlation(StrictModel):
    header: Header
    bodyProperty: Identifier


class ResponseSelection(StrictModel):
    status: Annotated[int, Field(ge=200, le=599)]
    example: Identifier | None = None


class ErrorResponses(StrictModel):
    badRequest: ResponseSelection
    unauthorized: ResponseSelection
    forbidden: ResponseSelection
    rateLimited: ResponseSelection


class ContractRuntime(StrictModel):
    version: Literal[1]
    securityScheme: Identifier
    correlation: Correlation
    errors: ErrorResponses


class OperationBehavior(StrictModel):
    correlation: Correlation | None = None
    errors: ErrorResponses | None = None


class ContractRuntimeV2(ContractRuntime):
    version: Literal[2]
    operations: dict[Identifier, OperationBehavior] = Field(default_factory=dict)


class RuntimeRateLimit(StrictModel):
    calls: Annotated[int, Field(gt=0, le=1000000)]
    renewalPeriod: Annotated[int, Field(gt=0, le=300)]


class RuntimeSimulation(ResponseSelection):
    status: Literal[429, 503]


class OperationRuntime(StrictModel):
    rateLimit: RuntimeRateLimit | None = None
    simulate: RuntimeSimulation | None = None


class ApiRuntime(StrictModel):
    allowedSubscriptionIds: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    rateLimit: RuntimeRateLimit
    simulate: RuntimeSimulation | None = None


class ApiRuntimeV2(ApiRuntime):
    operations: dict[Identifier, OperationRuntime] = Field(default_factory=dict)


def operation_runtime(spec, operation_id):
    value = spec[EXTENSION]
    if value.get("version") == 2:
        runtime = parse(ContractRuntimeV2, value, EXTENSION)
        override = runtime.operations.get(operation_id)
        return runtime.model_copy(update={name: getattr(override, name)
                                          for name in ("correlation", "errors")
                                          if override and getattr(override, name) is not None})
    return parse(ContractRuntime, value, EXTENSION)


def parse(model, value, where):
    try:
        return model.model_validate(value)
    except ValidationError as error:
        fields = ", ".join(".".join(map(str, item["loc"])) or "(object)"
                           for item in error.errors(include_input=False))
        raise TargetError(f"{where}: invalid or unsupported fields: {fields}") from None


def key_header(manifest):
    return manifest.get("inboundAuth", {}).get("subscriptionKeyHeader", STANDARD_KEY_HEADER)


def validate_manifest_runtime(manifest):
    inbound = manifest.get("inboundAuth") or {}
    header = key_header(manifest)
    if (not isinstance(header, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", header)
            or header.lower() in {"authorization", "host", "content-length", "content-type",
                                  "connection", "cookie", "transfer-encoding"}):
        raise TargetError("inboundAuth.subscriptionKeyHeader must be a safe subscription header name")
    opted = [api for api in manifest.get("apis", []) if "runtime" in api]
    if not opted and "subscriptionKeyHeader" not in inbound:
        return
    if (manifest.get("targets", {}).get("consumer") != "rest"
            or manifest.get("targets", {}).get("gateway", "existing-apim") != "existing-apim"
            or manifest.get("mcpExposure", {}).get("mode", "perApi") != "perApi"
            or inbound.get("mode", "subscriptionKey") != "subscriptionKey"):
        raise TargetError("REST runtime/custom subscription headers require targets.consumer=rest, "
                          "existing-apim, mcpExposure.mode=perApi and subscriptionKey authentication")
    for api in opted:
        config = parse(ApiRuntimeV2, api["runtime"], f"{api['name']}.runtime")
        if api.get("backend") != {"mode": "mock"}:
            raise TargetError("REST runtime requires an explicit credential-free mock backend")
        if len(set(config.allowedSubscriptionIds)) != len(config.allowedSubscriptionIds):
            raise TargetError("runtime.allowedSubscriptionIds must be unique subscription IDs, never keys")
        if config.simulate and type(api["runtime"]["simulate"]["status"]) is not int:
            raise TargetError("runtime.simulate.status must be an integer")
        if "subscriptionKeyHeader" not in inbound:
            raise TargetError("runtime requires explicit inboundAuth.subscriptionKeyHeader")


def validate_profile_runtime(manifest, specs, profile):
    validate_manifest_runtime(manifest)
    for spec in specs.values():
        check_extension_locations(spec)
    present = any(isinstance(spec, dict) and EXTENSION in spec for spec in specs.values())
    configured = any("runtime" in api for api in manifest.get("apis", []))
    if profile != "rest-consumption" and (
            present or configured or "subscriptionKeyHeader" in manifest.get("inboundAuth", {})):
        raise TargetError("x-kit-runtime/custom subscription headers are REST-only: use "
                          "rest-consumption; MCP transport headers are not tool arguments")


def check_extension_locations(spec):
    if not isinstance(spec, dict):
        return
    if any(str(key).startswith("x-kit-") and key != EXTENSION for key in spec):
        raise TargetError("unsupported x-kit extension at document root")
    for item in spec.get("paths", {}).values():
        if not isinstance(item, dict):
            continue
        for node in [item, *(v for k, v in item.items() if k in {
                "get", "put", "post", "patch", "delete", "options", "head", "trace"})]:
            if isinstance(node, dict) and any(str(key).startswith("x-kit-") for key in node):
                raise TargetError("x-kit-runtime belongs at the OpenAPI document root; other x-kit extensions are unsupported")


def _literal(value):
    # Encoding prevents both C# expression and APIM named-value substitution.
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def _cs(value):
    return json.dumps(value, ensure_ascii=True)


def _resolve(spec, value):
    seen = set()
    while isinstance(value, dict) and "$ref" in value:
        if set(value) != {"$ref"}:
            raise TargetError("runtime does not accept OpenAPI 3.0 reference siblings")
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise TargetError("runtime requires non-cyclic local references")
        seen.add(ref)
        try:
            value = spec
            for part in ref[2:].split("/"):
                value = value[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise TargetError("runtime reference cannot be resolved") from None
    return value


def safe_pattern(pattern):
    """A bounded ASCII concatenation grammar, not arbitrary .NET regular expressions."""
    if not isinstance(pattern, str) or len(pattern) > 256 or not pattern.startswith("^") or not pattern.endswith("$"):
        raise TargetError("runtime pattern must be anchored, bounded ASCII character classes/literals")
    body = pattern[1:-1]
    token = re.compile(r"(?:\[[A-Za-z0-9_-]+\]|[A-Za-z0-9_-])(?:\{(\d{1,3})(?:,(\d{1,3}))?\})?")
    end = 0
    bound = 0
    for match in token.finditer(body):
        if match.start() != end:
            break
        minimum = int(match[1] or 1)
        maximum = int(match[2] or match[1] or 1)
        if maximum < minimum:
            raise TargetError("runtime pattern has reversed quantifier bounds")
        bound += maximum
        end = match.end()
    if end != len(body) or not body or bound > 512:
        raise TargetError("runtime pattern supports only bounded ASCII classes/literals (maximum 512 characters)")
    try:
        re.compile(pattern)
    except re.error:
        raise TargetError("runtime pattern has an invalid character class") from None
    return pattern[:-1] + r"\z"


def _string_schema(spec, schema):
    schema = _resolve(spec, schema)
    allowed = {"type", "minLength", "maxLength", "pattern", "format", "enum", "example",
               "description", "title", "default"}
    if not isinstance(schema, dict) or schema.get("type") != "string" or set(schema) - allowed:
        raise TargetError("runtime inputs support string schemas only; unsupported constraints cannot be ignored")
    if schema.get("format") not in (None, "uuid"):
        raise TargetError("runtime input format supports uuid only")
    for name in ("minLength", "maxLength"):
        if name in schema and (type(schema[name]) is not int or not 0 <= schema[name] <= 4096):
            raise TargetError("runtime input length bounds must be integers in 0..4096")
    if schema.get("minLength", 0) > schema.get("maxLength", 4096):
        raise TargetError("runtime input length bounds are reversed")
    if "pattern" in schema:
        safe_pattern(schema["pattern"])
    if "enum" in schema and (not schema["enum"] or
                             any(not isinstance(v, str) or len(v) > 512 or
                                 any(x in v for x in ("@(", "@{", "{{"))
                                 for v in schema["enum"])):
        raise TargetError("runtime input enum must contain bounded literal strings")
    return schema


def _header_literal(spec, header):
    header = _resolve(spec, header)
    schema = _resolve(spec, header.get("schema", {}))
    candidates = []
    for source in (header, schema):
        if "example" in source:
            candidates.append(source["example"])
    if "enum" in schema:
        if len(schema["enum"]) != 1:
            raise TargetError("runtime response header enum must contain a single literal")
        candidates.extend(schema["enum"])
    if (not candidates or any(type(v) not in (str, int) for v in candidates)
            or len({str(v) for v in candidates}) != 1):
        raise TargetError("runtime response headers need an unambiguous literal example or singleton enum")
    value = str(candidates[0])
    if (not value or len(value) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in value)
            or any(marker in value for marker in ("@(", "@{", "{{"))):
        raise TargetError("runtime response headers must be safe printable literals, never policy code")
    return value


def contract_runtime(spec, api, manifest, bf):
    """Validate all supported semantics before compiling, including unselected responses."""
    # Extension placement mistakes are errors; example payload keys remain ordinary data.
    check_extension_locations(spec)
    enabled = EXTENSION in spec
    if enabled != ("runtime" in api):
        raise TargetError(f"{api['name']}: x-kit-runtime and manifest apis[].runtime must be configured together")
    if not enabled:
        return None
    validate_manifest_runtime(manifest)
    if isinstance(spec[EXTENSION], dict) and spec[EXTENSION].get("version") == 2:
        from .rest_runtime_v2 import validate_runtime
        return validate_runtime(spec, api, manifest, bf)
    runtime = parse(ContractRuntime, spec[EXTENSION], EXTENSION)
    if type(spec[EXTENSION]["version"]) is not int:
        raise TargetError("x-kit-runtime.version must be integer 1")
    config = parse(ApiRuntime, api["runtime"], f"{api['name']}.runtime")
    operations = list(bf.iter_operations(spec))
    if (len(operations) != 1 or operations[0][1] != "get"
            or any("trace" in item for item in spec["paths"].values() if isinstance(item, dict))):
        raise TargetError("runtime v1 supports exactly one GET operation per API (unambiguous early errors)")
    path, _, op = operations[0]
    if "requestBody" in op:
        raise TargetError("runtime v1 GET request bodies are unsupported")
    return validate_operation(spec, config, runtime, path, op, manifest, bf)


def validate_operation(spec, config, runtime, path, op, manifest, bf):
    scheme = _resolve(spec, spec.get("components", {}).get("securitySchemes", {}).get(runtime.securityScheme))
    if not isinstance(scheme, dict) or (scheme.get("type"), scheme.get("in"), scheme.get("name")) != (
            "apiKey", "header", key_header(manifest)):
        raise TargetError("runtime securityScheme must declare the configured subscription key header")
    if op.get("security", spec.get("security")) != [{runtime.securityScheme: []}]:
        raise TargetError("runtime security must require exactly the configured apiKey scheme, no alternatives")
    parameters = bf.operation_parameters(spec, path, op)
    names = set()
    correlation = None
    for param in parameters:
        name, location = param["name"], param["in"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", name) or location not in ("path", "query", "header"):
            raise TargetError("runtime parameters require safe names and path/query/header locations")
        if name.lower() in names:
            raise TargetError("runtime parameter names must be unique across locations/casing")
        names.add(name.lower())
        if location == "header" and name.lower() == key_header(manifest).lower():
            raise TargetError("declare authentication in securitySchemes, not input parameters/examples")
        if set(param) - {"name", "in", "required", "schema", "description", "example", "deprecated"}:
            raise TargetError("runtime parameter serialization/extensions are unsupported")
        schema = _string_schema(spec, param.get("schema"))
        if location == "header" and name.lower() == runtime.correlation.header.lower():
            correlation = param
            if not param.get("required") or schema != {"type": "string", "format": "uuid"}:
                # Non-semantic annotation fields may accompany the canonical UUID type.
                semantic = {k: v for k, v in schema.items() if k not in {"example", "description", "title"}}
                if not param.get("required") or semantic != {"type": "string", "format": "uuid"}:
                    raise TargetError("correlation header requires type string/format uuid, without extra restrictions")
    if correlation is None:
        raise TargetError("runtime correlation must name a declared required UUID request header")
    if runtime.correlation.header.lower() == key_header(manifest).lower():
        raise TargetError("correlation header cannot be the subscription key header")
    for name, status in (("badRequest", 400), ("unauthorized", 401), ("forbidden", 403), ("rateLimited", 429)):
        selected = getattr(runtime.errors, name)
        if selected.status != status:
            raise TargetError(f"runtime errors.{name}.status must be {status}")
        response_data(spec, op, selected.model_dump(exclude_none=True), runtime)
    rules = op.get("x-mock")
    if not isinstance(rules, list) or not rules or "when" in rules[-1]:
        raise TargetError("runtime requires explicit x-mock rules ending with a default response")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"when", "respond"} or "respond" not in rule:
            raise TargetError("runtime x-mock rules support when/respond only")
        selected = parse(ResponseSelection, rule["respond"], "x-mock.respond")
        response_data(spec, op, selected.model_dump(exclude_none=True), runtime)
        if runtime.version == 2 and "when" not in rule and rule is not rules[-1]:
            raise TargetError("runtime x-mock default response must be last")
        if "when" in rule:
            if not isinstance(rule["when"], dict):
                raise TargetError("runtime x-mock.when must be an object")
            if any(isinstance(v, str) and (any(ord(c) < 32 for c in v) or
                                           any(m in v for m in ("@(", "@{", "{{")))
                   for v in rule["when"].values()):
                raise TargetError("runtime x-mock comparisons must be literal strings, not policy expressions")
            if runtime.version == 2:
                from .rest_inputs import condition_expression
                condition_expression(spec, path, op, rule["when"], bf)
            else:
                bf.xmock_condition(spec, path, op, rule["when"], "runtime x-mock.when")
    for status, response in op["responses"].items():
        if not str(status).isdigit():
            raise TargetError("runtime responses require explicit numeric status codes")
        response = _resolve(spec, response)
        for media in response.get("content", {}).values():
            selections = [{"status": int(status), "example": name}
                          for name in media.get("examples", {})] or [{"status": int(status)}]
            for selected in selections:
                response_data(spec, op, selected, runtime)
    if config.simulate:
        response_data(spec, op, config.simulate.model_dump(exclude_none=True), runtime)
    return runtime, config, path, op, parameters


def response_data(spec, op, selected, runtime):
    response = _resolve(spec, next((r for s, r in op["responses"].items()
                                   if str(s) == str(selected["status"])), None))
    media_type = "application/problem+json" if selected["status"] >= 400 else "application/json"
    if not response or set(response.get("content", {})) != {media_type}:
        raise TargetError(f"runtime response {selected['status']} requires exactly {media_type}")
    media = response["content"][media_type]
    if selected.get("example") is not None:
        example = _resolve(spec, media.get("examples", {}).get(selected["example"]))
        if not isinstance(example, dict) or "value" not in example:
            raise TargetError("runtime response example selection does not exist")
        value = example["value"]
    elif "example" in media:
        value = media["example"]
    elif len(media.get("examples", {})) == 1:
        value = _resolve(spec, next(iter(media["examples"].values()))).get("value")
    else:
        raise TargetError("runtime response needs one example or an explicit example selection")
    schema = _resolve(spec, media.get("schema"))
    prop = runtime.correlation.bodyProperty
    body_schema = _resolve(spec, (schema or {}).get("properties", {}).get(prop))
    if (not isinstance(value, dict) or not isinstance(value.get(prop), str)
            or not schema or schema.get("type") != "object" or prop not in schema.get("required", [])
            or not body_schema or body_schema.get("type") != "string"
            or set(body_schema) - {"type", "format", "description", "example", "title"}
            or body_schema.get("format") not in (None, "uuid")):
        raise TargetError("runtime response requires an unconstrained top-level string correlation property")
    headers = {}
    header_names = set()
    correlation_seen = False
    for name, header in response.get("headers", {}).items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", name) or name.lower() in {
                "content-length", "transfer-encoding", "connection", "content-type", "server", "set-cookie"}:
            raise TargetError("unsupported runtime response header name")
        if name.lower() in header_names:
            raise TargetError("duplicate runtime response header casing")
        header_names.add(name.lower())
        if name.lower() == runtime.correlation.header.lower():
            hs = _resolve(spec, _resolve(spec, header).get("schema", {}))
            if hs.get("type") != "string" or hs.get("format") != "uuid" or set(hs) - {
                    "type", "format", "description", "example", "title"}:
                raise TargetError("runtime response correlation header requires string/uuid without extra restrictions")
            correlation_seen = True
            continue
        headers[name] = _header_literal(spec, header)
        if name.lower() == "retry-after" and (not re.fullmatch(r"[1-9][0-9]*", headers[name])
                                             or int(headers[name]) > 86400):
            raise TargetError("Retry-After requires a positive integer seconds literal")
        if name.lower() == "retry-after" and selected["status"] == 429:
            hs = _resolve(spec, _resolve(spec, header).get("schema", {}))
            if (hs.get("type") != "integer" or hs.get("minimum") != 1
                    or set(hs) - {"type", "minimum", "description", "example", "title"}):
                raise TargetError("real 429 Retry-After requires integer/minimum 1 without fixed enum or upper bound")
    if not correlation_seen:
        raise TargetError("every runtime response must declare the correlation UUID header")
    if selected["status"] == 429 and not any(n.lower() == "retry-after" for n in headers):
        raise TargetError("runtime 429 response must declare a positive integer Retry-After")
    return media_type, value, headers


def _set_header(parent, name, value):
    header = ET.SubElement(parent, "set-header", {"name": name, "exists-action": "override"})
    ET.SubElement(header, "value").text = value


def runtime_response(spec, op, selected, runtime, *, real_rate=False):
    media, value, headers = response_data(spec, op, selected, runtime)
    result = ET.Element("return-response")
    status = selected["status"]
    reason = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else "Response"
    ET.SubElement(result, "set-status", {"code": str(status), "reason": reason})
    _set_header(result, "Content-Type", media)
    for name, literal in headers.items():
        if real_rate and name.lower() == "retry-after":
            literal = '@(System.Convert.ToInt32(context.Variables["kitRetryAfter"]).ToString())'
        _set_header(result, name, literal)
    _set_header(result, runtime.correlation.header, '@((string)context.Variables["kitCorrelation"])')
    ET.SubElement(result, "set-body").text = (
        '@{var body = JObject.Parse(System.Text.Encoding.UTF8.GetString('
        f'System.Convert.FromBase64String("{_literal(value)}")));'
        f'body[{_cs(runtime.correlation.bodyProperty)}] = (string)context.Variables["kitCorrelation"];'
        'return body.ToString(Newtonsoft.Json.Formatting.None);}')
    return result


def _correlation(parent, runtime):
    name = _cs(runtime.correlation.header)
    expr = f'context.Request.Headers.GetValueOrDefault({name}, "")'
    ET.SubElement(parent, "set-variable", {
        "name": "kitCorrelationValid",
        "value": f'@(context.Request.Headers.ContainsKey({name}) && '
                 f'context.Request.Headers[{name}].Length == 1 && '
                 f'System.Text.RegularExpressions.Regex.IsMatch({expr}, {_cs(safe_pattern(UUID_PATTERN))}))'})
    ET.SubElement(parent, "set-variable", {
        "name": "kitCorrelation",
        "value": f'@((bool)context.Variables["kitCorrelationValid"] ? {expr} : Guid.NewGuid().ToString())'})


def operation_policy_ids(spec):
    if not isinstance(spec.get(EXTENSION), dict) or spec[EXTENSION].get("version") != 2:
        return []
    return sorted(op["operationId"] for item in spec["paths"].values() for method, op in item.items()
                  if method in {"get", "post", "put", "patch", "delete"})


def build_runtime_policy(spec, api, manifest, bf):
    if isinstance(spec.get(EXTENSION), dict) and spec[EXTENSION].get("version") == 2:
        from .rest_runtime_v2 import build_policy
        return build_policy(spec, api, manifest, bf)
    runtime, config, path, op, parameters = contract_runtime(spec, api, manifest, bf)
    root = ET.Element("policies")
    inbound = ET.SubElement(root, "inbound")
    _correlation(inbound, runtime)

    def reject(parent, condition, name):
        branch = ET.SubElement(ET.SubElement(parent, "choose"), "when", {"condition": f"@({condition})"})
        branch.append(runtime_response(spec, op, getattr(runtime.errors, name).model_dump(exclude_none=True), runtime))

    subscription_header = _cs(key_header(manifest))
    reject(inbound, 'context.Subscription == null || '
           f'!context.Request.Headers.ContainsKey({subscription_header}) || '
           f'context.Request.Headers[{subscription_header}].Length != 1 || string.IsNullOrEmpty('
           f'context.Request.Headers.GetValueOrDefault({subscription_header}, ""))', "unauthorized")
    permitted = " || ".join(f'context.Subscription.Id == {_cs(s)}' for s in config.allowedSubscriptionIds)
    reject(inbound, f"!({permitted})", "forbidden")
    ET.SubElement(inbound, "base")
    invalid = ['!(bool)context.Variables["kitCorrelationValid"]']
    for param in parameters:
        schema = _string_schema(spec, param["schema"])
        expr, _ = bf.xmock_param_expr(spec, path, op, param["name"], "runtime validation")
        checks = []
        if param.get("required"):
            checks.append(f"string.IsNullOrEmpty({expr})")
        if param["in"] in {"header", "query"}:
            source = {"header": "context.Request.Headers", "query": "context.Request.Url.Query"}[param["in"]]
            checks.append(f"({source}.ContainsKey({_cs(param['name'])}) && "
                          f"{source}[{_cs(param['name'])}].Length != 1)")
        for key, symbol in (("minLength", "<"), ("maxLength", ">")):
            if key in schema:
                checks.append(f"{expr}.Length {symbol} {schema[key]}")
        pattern = schema.get("pattern")
        for pattern in ([pattern] if pattern else []) + ([UUID_PATTERN] if schema.get("format") == "uuid" else []):
            checks.append(f"!System.Text.RegularExpressions.Regex.IsMatch({expr}, {_cs(safe_pattern(pattern))})")
        if "enum" in schema:
            checks.append("!(" + " || ".join(f"{expr} == {_cs(v)}" for v in schema["enum"]) + ")")
        if checks:
            constraint = "(" + " || ".join(checks) + ")"
            if not param.get("required"):
                source = {"header": "context.Request.Headers", "query": "context.Request.Url.Query",
                          "path": "context.Request.MatchedParameters"}[param["in"]]
                constraint = f"({source}.ContainsKey({_cs(param['name'])}) && {constraint})"
            invalid.append(constraint)
    reject(inbound, " || ".join(invalid), "badRequest")
    ET.SubElement(inbound, "rate-limit", {
        "id": "kit-runtime-rate", "calls": str(config.rateLimit.calls),
        "renewal-period": str(config.rateLimit.renewalPeriod),
        "retry-after-variable-name": "kitRetryAfter"})
    if config.simulate:
        inbound.append(runtime_response(spec, op, config.simulate.model_dump(exclude_none=True), runtime))
    else:
        choose = ET.SubElement(inbound, "choose")
        for rule in op["x-mock"]:
            branch = (ET.SubElement(choose, "when", {"condition": bf.xmock_condition(
                spec, path, op, rule["when"], "runtime x-mock")}) if "when" in rule
                else ET.SubElement(choose, "otherwise"))
            branch.append(runtime_response(spec, op, rule["respond"], runtime))
    ET.SubElement(ET.SubElement(root, "backend"), "base")
    ET.SubElement(ET.SubElement(root, "outbound"), "base")
    error = ET.SubElement(root, "on-error")
    _correlation(error, runtime)  # Subscription validation can fail before inbound.
    reject(error, 'context.LastError.Source == "authorization" && '
           '(context.LastError.Reason == "SubscriptionKeyNotFound" || '
           'context.LastError.Reason == "SubscriptionKeyInvalid")', "unauthorized")
    branch = ET.SubElement(ET.SubElement(error, "choose"), "when", {
        "condition": '@(context.LastError.Reason == "RateLimitExceeded" && '
                     'context.LastError.PolicyId == "kit-runtime-rate" && '
                     'context.Variables.ContainsKey("kitRetryAfter"))'})
    branch.append(runtime_response(spec, op, runtime.errors.rateLimited.model_dump(exclude_none=True),
                                   runtime, real_rate=True))
    ET.SubElement(error, "base")
    text = bf.serialize(root)
    size = len(text.encode("utf-8"))
    if size > 16 * 1024:
        raise TargetError(f"REST runtime policy measures {size} UTF-8 bytes; Consumption limit is "
                          "16384 bytes. No payload was truncated. Reduce examples only with "
                          "approval or use a separately implemented runtime; native MCP does not "
                          "support this REST-only extension.")
    return text
