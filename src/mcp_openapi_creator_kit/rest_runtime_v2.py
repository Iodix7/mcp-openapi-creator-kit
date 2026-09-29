"""Multi-operation REST policies; v1 serialization and semantics remain unchanged."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .rest_inputs import MAX_BODY_BYTES, body_schema, condition_expression
from .rest_runtime import (
    ApiRuntimeV2, ContractRuntimeV2, EXTENSION, TargetError, UUID_PATTERN,
    _cs, _resolve, _set_header, _string_schema, key_header, operation_runtime,
    check_extension_locations, parse, response_data, safe_pattern, validate_manifest_runtime, validate_operation,
)


@dataclass
class Operation:
    path: str
    method: str
    definition: dict
    behavior: ContractRuntimeV2
    config: ApiRuntimeV2
    parameters: list[dict]
    body: dict | None


def validate_runtime(spec, api, manifest, bf):
    check_extension_locations(spec)
    validate_manifest_runtime(manifest)
    declared = parse(ContractRuntimeV2, spec[EXTENSION], EXTENSION)
    if type(spec[EXTENSION]["version"]) is not int:
        raise TargetError("x-kit-runtime.version must be integer 2")
    config = parse(ApiRuntimeV2, api["runtime"], "runtime")
    operations = list(bf.iter_operations(spec))
    ids = {op["operationId"] for _, _, op in operations}
    if (not 1 <= len(operations) <= 32 or len(ids) != len(operations)
            or set(declared.operations) - ids or set(config.operations) - ids):
        raise TargetError("runtime v2 requires 1..32 unique operations; overrides must name an existing operationId")
    if any(method not in {"get", "post", "put", "patch", "delete"} for _, method, _ in operations) or any(
            "trace" in item for item in spec["paths"].values() if isinstance(item, dict)):
        raise TargetError("runtime v2 supports GET, POST, PUT, PATCH and DELETE only")
    result = []
    for path, method, op in operations:
        behavior = operation_runtime(spec, op["operationId"])
        override = config.operations.get(op["operationId"])
        effective = config.model_copy(update={
            name: getattr(override, name) for name in ("simulate",)
            if override and getattr(override, name) is not None})
        body = body_schema(spec, op)
        if method == "get" and body is not None:
            raise TargetError("runtime v2 GET request bodies are unsupported")
        _, _, _, _, params = validate_operation(spec, effective, behavior, path, op, manifest, bf)
        result.append(Operation(path, method, op, behavior, effective, params, body))
    return result


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _encoded(value):
    return base64.b64encode(_json(value).encode("utf-8")).decode("ascii")


def _serialize(node):
    attributes = "".join(f" {name}={quoteattr(value)}" for name, value in node.attrib.items())
    content = escape(node.text or "") + "".join(_serialize(child) for child in node)
    return f"<{node.tag}{attributes}>{content}</{node.tag}>" if content else f"<{node.tag}{attributes}/>"


def _decoded(value):
    return f'System.Text.Encoding.UTF8.GetString(System.Convert.FromBase64String("{_encoded(value)}"))'


def _variable(parent, name, expression):
    return ET.SubElement(parent, "set-variable", {"name": name, "value": expression})


def _when(parent, condition):
    return ET.SubElement(ET.SubElement(parent, "choose"), "when", {"condition": f"@({condition})"})


CONFIG = 'JObject.Parse((string)context.Variables["kitOperation"])'
RESPONSE = 'JObject.Parse((string)context.Variables["kitResponse"])'


def _initialize(parent, metadata, responses):
    groups = {}
    for oid, data in metadata.items():
        groups.setdefault(_json(data), []).append(oid)
    if len(groups) == 1:
        expression = f"@({_decoded(next(iter(metadata.values())))})"
    else:
        branches = []
        for data, ids in list(groups.items())[:-1]:
            branches.append("if(new[]{" + ",".join(_cs(oid) for oid in ids)
                            + "}.Contains(context.Operation.Id)){return " + _decoded(json.loads(data)) + ";}")
        expression = "@{" + "".join(branches) + "return " + _decoded(json.loads(next(reversed(groups)))) + ";}"
    _variable(parent, "kitOperation", expression)
    _variable(parent, "kitResponses", f"@({_decoded(responses)})")
    _variable(parent, "kitCorrelationHeader", f'@((string){CONFIG}["header"])')
    name = '(string)context.Variables["kitCorrelationHeader"]'
    value = f'context.Request.Headers.GetValueOrDefault({name}, "")'
    _variable(parent, "kitCorrelationValid", f'@(context.Request.Headers.ContainsKey({name}) && '
              f'context.Request.Headers[{name}].Length == 1 && '
              f'System.Text.RegularExpressions.Regex.IsMatch({value}, {_cs(safe_pattern(UUID_PATTERN))}))')
    _variable(parent, "kitCorrelation", f'@((bool)context.Variables["kitCorrelationValid"] ? '
              f'{value} : Guid.NewGuid().ToString())')


def _select(parent, value):
    _variable(parent, "kitResponseId", f"@({value})")


def _emit(parent, header_names, constants, *, real_rate=False):
    _variable(parent, "kitResponse",
              '@{var table = JArray.Parse((string)context.Variables["kitResponses"]);'
              'var r = (JObject)table[(int)context.Variables["kitResponseId"]];'
              'if(r["body"].Type == JTokenType.Integer){r["body"] = table[(int)r["body"]]["body"];}'
              'return r.ToString(Newtonsoft.Json.Formatting.None);}')
    response = ET.SubElement(parent, "return-response")
    ET.SubElement(response, "set-status", {"code": f'@((int){RESPONSE}["status"])', "reason": "Response"})
    for name in header_names:
        field = f'{RESPONSE}["headers"][{_cs(name)}]'
        value = constants.get(name, f'@{{var v = (string){field};return v == null ? "" : '
                              'System.Text.Encoding.UTF8.GetString(System.Convert.FromBase64String(v));}')
        if real_rate and name.lower() == "retry-after":
            value = ('@(context.LastError.PolicyId == "kit-runtime-rate" ? '
                     'System.Convert.ToInt32(context.Variables["kitRetryAfter"]).ToString() : "")')
        header = ET.SubElement(response, "set-header", {
            "name": name, "exists-action": "override" if name in constants else
            f'@({field} != null ? "override" : "delete")'})
        ET.SubElement(header, "value").text = value
    _set_header(response, '@((string)context.Variables["kitCorrelationHeader"])',
                '@((string)context.Variables["kitCorrelation"])')
    # Keep example JSON opaque: JSON.NET's default date parsing changes source timestamps.
    ET.SubElement(response, "set-body").text = (
        f'@{{var body = (string){RESPONSE}["body"];'
        'return body.Substring(0, body.Length - 1) + (body.Length > 2 ? "," : "") + '
        f'Newtonsoft.Json.JsonConvert.SerializeObject((string){CONFIG}["property"]) + ":" + '
        'Newtonsoft.Json.JsonConvert.SerializeObject((string)context.Variables["kitCorrelation"]) + "}";}')


def _response_table(responses):
    present = [r for r in responses if r is not None]
    names = sorted({name for r in present for name in r["headers"]})
    constants = {}
    for name in names:
        values = {r["headers"].get(name) for r in present}
        if len(values) == 1 and None not in values:
            constants[name] = next(iter(values))
    compact, bodies = [], {}
    for index, response in enumerate(responses):
        if response is None:
            compact.append(None)
            continue
        first = bodies.setdefault(response["body"], index)
        compact.append({**response, "body": response["body"] if first == index else first,
                        "headers": {name: base64.b64encode(value.encode("utf-8")).decode("ascii")
                                    for name, value in response["headers"].items() if name not in constants}})
    return compact, names, constants


def _invalid_parameters(plan, spec, bf):
    invalid = ['!(bool)context.Variables["kitCorrelationValid"]']
    declarations = []
    for index, param in enumerate(plan.parameters):
        if param["in"] == "header" and param["name"].lower() == plan.behavior.correlation.header.lower():
            continue
        schema = _string_schema(spec, param["schema"])
        expression, _ = bf.xmock_param_expr(spec, plan.path, plan.definition, param["name"], "runtime v2")
        value = f"p{index}"
        declarations.append(f"var {value}={expression};")
        source = {"header": "context.Request.Headers", "query": "context.Request.Url.Query",
                  "path": "context.Request.MatchedParameters"}[param["in"]]
        present = f"{source}.ContainsKey({_cs(param['name'])})"
        checks = [f"string.IsNullOrEmpty({value})"] if param.get("required") else []
        if param["in"] in {"header", "query"}:
            checks.append(f"({present} && {source}[{_cs(param['name'])}].Length != 1)")
        for name, symbol in (("minLength", "<"), ("maxLength", ">")):
            if name in schema:
                checks.append(f"{value}.Length {symbol} {schema[name]}")
        for pattern in ([schema["pattern"]] if "pattern" in schema else []) + (
                [UUID_PATTERN] if schema.get("format") == "uuid" else []):
            checks.append(f"!System.Text.RegularExpressions.Regex.IsMatch({value}, {_cs(safe_pattern(pattern))})")
        if "enum" in schema:
            checks.append("!(" + " || ".join(f"{value} == {_cs(v)}" for v in schema["enum"]) + ")")
        if checks:
            check = "(" + " || ".join(checks) + ")"
            invalid.append(check if param.get("required") else f"({present} && {check})")
    if plan.body is not None:
        required = _resolve(spec, plan.definition["requestBody"]).get("required", False)
        declarations.append('var absent = context.Request.Body == null || '
                            'context.Request.Body.As<string>(preserveContent: true).Length == 0;')
        if required:
            invalid.append("absent")
        invalid.append('(!absent && (!context.Request.Headers.ContainsKey("Content-Type") || '
                       'context.Request.Headers["Content-Type"].Length != 1 || '
                       '!string.Equals(context.Request.Headers.GetValueOrDefault("Content-Type", "").Split(\';\')[0].Trim(), '
                       '"application/json", StringComparison.OrdinalIgnoreCase)))')
    return "@{" + "".join(declarations) + "return " + " || ".join(invalid) + ";}"


def _policy_text(root, label="REST runtime v2"):
    text = _serialize(root) + "\n"
    size = len(text.encode("utf-8"))
    if size > 16384:
        raise TargetError(f"{label} policy measures {size} UTF-8 bytes; Consumption limit is 16384 bytes. "
                          "No examples were shortened. Reduce payloads with approval, split whole operations "
                          "into separate API contracts, or use a separately implemented runtime.")
    return text


def build_operation_policies(spec, api, manifest, bf):
    outputs = {}
    build_policy(spec, api, manifest, bf, operation_policies=outputs)
    return outputs


def build_policy(spec, api, manifest, bf, *, operation_policies=None):
    plans = validate_runtime(spec, api, manifest, bf)
    config = parse(ApiRuntimeV2, api["runtime"], "runtime")
    responses, metadata = [], {}

    def response_id(plan, selection):
        media, body, headers = response_data(spec, plan.definition, selection, plan.behavior)
        headers = {"Content-Type": media, **headers}
        body = {key: value for key, value in body.items() if key != plan.behavior.correlation.bodyProperty}
        data = {"status": selection["status"], "body": _json(body), "headers": headers}
        if data not in responses:
            responses.append(data)
        return responses.index(data)

    for plan in plans:
        oid = plan.definition["operationId"]
        metadata[oid] = {
            "header": plan.behavior.correlation.header,
            "property": plan.behavior.correlation.bodyProperty,
            **{name: response_id(plan, value.model_dump(exclude_none=True))
               for name, value in ((name, getattr(plan.behavior.errors, name)) for name in (
                   "unauthorized", "forbidden", "badRequest", "rateLimited"))},
        }
        for rule in plan.definition["x-mock"]:
            response_id(plan, rule["respond"])
        if plan.config.simulate:
            response_id(plan, plan.config.simulate.model_dump(exclude_none=True))

    compact_responses, header_names, constants = _response_table(responses)
    root = ET.Element("policies")
    inbound = ET.SubElement(root, "inbound")
    ET.SubElement(inbound, "base")
    known = ("context.Operation != null && new[]{"
             + ",".join(_cs(p.definition["operationId"]) for p in plans)
             + "}.Contains(context.Operation.Id)")
    active = _when(inbound, known)
    _initialize(active, metadata, compact_responses)
    gates = ET.SubElement(active, "choose")
    header = _cs(key_header(manifest))
    auth = ET.SubElement(gates, "when", {"condition": '@(context.Subscription == null || '
        f'!context.Request.Headers.ContainsKey({header}) || context.Request.Headers[{header}].Length != 1 || '
        f'string.IsNullOrEmpty(context.Request.Headers.GetValueOrDefault({header}, "")))'})
    _select(auth, f'(int){CONFIG}["unauthorized"]')
    allowed = " || ".join(f"context.Subscription.Id == {_cs(v)}" for v in config.allowedSubscriptionIds)
    forbidden = ET.SubElement(gates, "when", {"condition": f"@(!({allowed}))"})
    _select(forbidden, f'(int){CONFIG}["forbidden"]')
    authorized = ET.SubElement(gates, "otherwise")
    validations = ET.SubElement(authorized, "choose")
    groups = {}
    for plan in plans:
        groups.setdefault(_invalid_parameters(plan, spec, bf), []).append(plan.definition["operationId"])
    for expression, ids in groups.items():
        branch = ET.SubElement(validations, "when", {"condition":
            '@(new[]{' + ",".join(_cs(oid) for oid in ids) + '}.Contains(context.Operation.Id))'})
        _variable(branch, "kitInvalidInput", expression)
    inputs = ET.SubElement(authorized, "choose")
    bad = ET.SubElement(inputs, "when", {"condition": '@((bool)context.Variables["kitInvalidInput"])'})
    _select(bad, f'(int){CONFIG}["badRequest"]')
    valid = ET.SubElement(inputs, "otherwise")
    body_ops = [p.definition["operationId"] for p in plans if p.body is not None]
    if body_ops:
        body = _when(valid, 'context.Request.Body != null && '
                     'context.Request.Body.As<string>(preserveContent: true).Length > 0 && new[]{'
                     + ",".join(_cs(oid) for oid in body_ops) + "}.Contains(context.Operation.Id)")
        validator = ET.SubElement(body, "validate-content", {
            "id": "kit-runtime-body", "unspecified-content-type-action": "prevent",
            "max-size": str(MAX_BODY_BYTES), "size-exceeded-action": "prevent",
            "errors-variable-name": "kitBodyErrors"})
        ET.SubElement(validator, "content", {"type": "application/json", "validate-as": "json",
                                           "action": "prevent", "case-insensitive-property-names": "false"})
    ET.SubElement(valid, "rate-limit", {
        "id": "kit-runtime-rate", "calls": str(config.rateLimit.calls),
        "renewal-period": str(config.rateLimit.renewalPeriod), "retry-after-variable-name": "kitRetryAfter"})
    choices = ET.SubElement(valid, "choose")
    groups = {}
    for plan in plans:
        branch = ET.Element("when")
        if plan.config.simulate:
            _select(branch, str(response_id(plan, plan.config.simulate.model_dump(exclude_none=True))))
        elif len(plan.definition["x-mock"]) == 1:
            _select(branch, str(response_id(plan, plan.definition["x-mock"][0]["respond"])))
        else:
            rules = ET.SubElement(branch, "choose")
            for rule in plan.definition["x-mock"]:
                node = (ET.SubElement(rules, "when", {"condition": condition_expression(
                    spec, plan.path, plan.definition, rule["when"], bf)})
                    if "when" in rule else ET.SubElement(rules, "otherwise"))
                _select(node, str(response_id(plan, rule["respond"])))
        key = _serialize(branch)
        if key not in groups:
            groups[key] = (branch, [])
        groups[key][1].append(plan.definition["operationId"])
    for branch, ids in groups.values():
        branch.set("condition", "@(new[]{" + ",".join(_cs(oid) for oid in ids) + "}.Contains(context.Operation.Id))")
        choices.append(branch)
    limited = [oid for oid, override in sorted(config.operations.items()) if override.rateLimit]
    if limited:
        # Operation counters require operation scope; base must finish validation first.
        _variable(valid, "kitReady", "@(true)")
    _emit(_when(active, '!context.Variables.ContainsKey("kitReady") || !new[]{'
                + ",".join(_cs(oid) for oid in limited) + '}.Contains(context.Operation.Id)') if limited else active,
          header_names, constants)
    ET.SubElement(ET.SubElement(root, "backend"), "base")
    ET.SubElement(ET.SubElement(root, "outbound"), "base")
    error = ET.SubElement(root, "on-error")
    failures = [
        ("unauthorized", 'context.LastError.Source == "authorization" && '
         '(context.LastError.Reason == "SubscriptionKeyNotFound" || context.LastError.Reason == "SubscriptionKeyInvalid")'),
        ("rateLimited", 'context.LastError.PolicyId == "kit-runtime-rate" && context.LastError.Reason == "RateLimitExceeded" && '
         'context.Variables.ContainsKey("kitRetryAfter")'),
        ("badRequest", 'context.LastError.PolicyId == "kit-runtime-body" && context.Variables.ContainsKey("kitBodyErrors")'),
    ]
    failure = _when(error, f"({known}) && (" + " || ".join(f"({c})" for _, c in failures) + ")")
    error_ids = {data[name] for data in metadata.values() for name, _ in failures}
    error_table, error_headers, error_constants = _response_table(
        [r if index in error_ids else None for index, r in enumerate(responses)])
    error_metadata = {oid: {name: value for name, value in data.items() if name != "forbidden"}
                      for oid, data in metadata.items()}
    _initialize(failure, error_metadata, error_table)
    _select(failure, '(int)' + CONFIG + '[context.LastError.PolicyId == "kit-runtime-rate" ? "rateLimited" : '
            'context.LastError.PolicyId == "kit-runtime-body" ? "badRequest" : "unauthorized"]')
    _emit(failure, error_headers, error_constants, real_rate=True)
    ET.SubElement(error, "base")
    text = _policy_text(root)
    if operation_policies is not None:
        for plan in plans:
            oid = plan.definition["operationId"]
            policy = ET.Element("policies")
            operation_inbound = ET.SubElement(policy, "inbound")
            ET.SubElement(operation_inbound, "base")
            if oid in limited:
                ready = _when(operation_inbound, 'context.Variables.ContainsKey("kitReady")')
                limit = config.operations[oid].rateLimit
                ET.SubElement(ready, "rate-limit", {
                    "id": "kit-runtime-rate", "calls": str(limit.calls),
                    "renewal-period": str(limit.renewalPeriod), "retry-after-variable-name": "kitRetryAfter"})
                _emit(ready, header_names, constants)
            for section in ("backend", "outbound", "on-error"):
                ET.SubElement(ET.SubElement(policy, section), "base")
            operation_policies[oid] = _policy_text(policy, f"REST runtime v2 operation '{oid}'")
    return text
