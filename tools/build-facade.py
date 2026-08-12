#!/usr/bin/env python3
"""
build-facade.py - generate ALL per-client artifacts: policy + Bicep.

Usage:
    python tools/build-facade.py                     # all clients in /clients
    python tools/build-facade.py clients/<clientId>  # one client (clients.gen.bicep
                                                                                                     # is still regenerated for all)
    python tools/build-facade.py --catalog           # tool/contract catalog
    python tools/build-facade.py --catalog schemas   # data-structure catalog

The ONLY file a client edits manually is mcp-manifest.yaml. From that, the
generator produces in clients/<id>/generated/ (do not edit):

    facade.openapi.yaml      single contract (merge of manifest APIs)
    facade.policy.xml        per-operation API routing (compiled mock or
                                                     set-backend-service + outbound auth)
    api-<name>.policy.xml    single REST API policy: mock compiled from
                                                     examples + x-mock rules in the contract, or
                                                     forward + outbound auth (apiKey/oauth2-cc)
    client.bicep             fully resolved Bicep composition (API modules,
                                                     facade, named values, product): no manual
                                                     duplicated per-client logic

and also:

    infra/clients.gen.bicep  index of ALL clients: infra/main.bicep stays
                                                     static and client-agnostic

Supported outbound auth: none | apiKey | oauth2-cc. Secrets are NOT stored
here: only APIM named value names (secretRef) linked to Key Vault, created
by modules/kv-named-values.bicep.

Validations (fail at BUILD time, never at runtime in front of clients):
    - operationId unique per client (they become MCP tool names)
    - unique paths across APIs
    - components schemas with same name but different definitions
    - every manifest mcpTools exists as operationId in its API
    - standards.errorModel=rfc7807: every 4xx/5xx response has
        application/problem+json with schema
    - standards.writeIdempotency=required: every POST/PUT/PATCH has required
        Idempotency-Key header
    - every body response has at least one example (mock data source)
    - every example conforms to schema (type/enum/required): in mock mode,
        example becomes live response, so mismatch is a contract violation
    - backend.mode 'hosted' and outboundAuth 'mtls': not yet supported (v1.2)
    - client id: slug [a-z0-9-], same as folder name, unique across clients
"""
import sys
import re
import copy
import json
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

EMPTY_POLICY = ("<policies><inbound><base /></inbound><backend><base /></backend>"
                "<outbound><base /></outbound><on-error><base /></on-error></policies>")

WRITE_VERBS = {"post", "put", "patch"}
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


def die(msg: str):
    print(f"[build-facade] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str):
    print(f"[build-facade] WARNING: {msg}")


def write_text(path: Path, content: str):
    """Deterministic write: UTF-8 and LF line endings on every platform."""
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"file not found: {path.relative_to(REPO_ROOT).as_posix()} - "
            "check the name in the manifest")
    except yaml.YAMLError as e:
        die(f"invalid YAML in {path.relative_to(REPO_ROOT).as_posix()}: {e}")


SLUG_RE = r"[a-z][a-z0-9-]*"          # APIM resource names / Bicep identifiers
OPID_RE = r"[A-Za-z][A-Za-z0-9-]*"    # APIM operation: NO underscore (see below)
SECRET_RE = r"[a-zA-Z0-9-]{1,127}"    # Key Vault secret / named value names


def validate_manifest(manifest, folder_name: str) -> dict:
    """Validate manifest structure and values: common edit errors must raise
    actionable die() messages, never Python tracebacks."""
    where = f"clients/{folder_name}/mcp-manifest.yaml"
    if not isinstance(manifest, dict):
        die(f"{where}: empty or invalid manifest")

    client = manifest.get("client")
    if not isinstance(client, str) or not re.fullmatch(SLUG_RE, client):
        die(f"{where}: missing or invalid 'client' field - expected slug "
            f"/{SLUG_RE}/ starting with a letter (used in resource names)")
    if client != folder_name:
        die(f"{where}: client field '{client}' must match folder name")
    if not isinstance(manifest.get("displayName"), str) or not manifest["displayName"]:
        die(f"{where}: missing 'displayName' field")

    exposure = manifest.get("mcpExposure") or {}
    if not isinstance(exposure, dict):
        die(f"{where}: mcpExposure must be an object")
    mode = exposure.setdefault("mode", "perApi")
    if mode not in ("facade", "perApi", "both"):
        die(f"{where}: invalid mcpExposure.mode '{mode}' - "
            "use facade | perApi | both")
    facade_name = exposure.setdefault("facadeName", "agent")
    if not isinstance(facade_name, str) or not re.fullmatch(SLUG_RE, facade_name):
        die(f"{where}: invalid mcpExposure.facadeName '{facade_name}' (slug /{SLUG_RE}/)")
    manifest["mcpExposure"] = exposure

    apis = manifest.get("apis")
    if not isinstance(apis, list) or not apis:
        die(f"{where}: 'apis' must be a list with at least one API")
    seen_names = set()
    for api in apis:
        if not isinstance(api, dict) or not isinstance(api.get("name"), str):
            die(f"{where}: each 'apis' entry must have a 'name' field")
        name = api["name"]
        if not re.fullmatch(SLUG_RE, name):
            die(f"{where}: invalid API name '{name}' - expected slug /{SLUG_RE}/ "
                "(used as path, APIM resource name, and Bicep identifier)")
        if name in seen_names:
            die(f"{where}: API '{name}' declared twice")
        seen_names.add(name)
        if not isinstance(api.get("displayName"), str) or not api["displayName"]:
            die(f"{where}: API '{name}': missing displayName")
        if not isinstance(api.get("backend"), dict) or "mode" not in api["backend"]:
            die(f"{where}: API '{name}': backend.mode is required (mock | external)")
        tools = api.get("mcpTools")
        if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
            die(f"{where}: API '{name}': mcpTools must be a non-empty operationId list")
    if mode != "perApi" and facade_name in seen_names:
        die(f"{where}: mcpExposure.facadeName '{facade_name}' collides with same-name API: "
            f"APIM resources '{client}-{facade_name}' would be duplicated")

    inbound = manifest.get("inboundAuth") or {"mode": "subscriptionKey"}
    if not isinstance(inbound, dict):
        die(f"{where}: inboundAuth must be an object")
    imode = inbound.setdefault("mode", "subscriptionKey")
    if imode not in ("subscriptionKey", "entraJwt"):
        die(f"{where}: invalid inboundAuth.mode '{imode}' - "
            "use subscriptionKey | entraJwt")
    if imode == "entraJwt":
        jwt = inbound.get("entraJwt") or {}
        if not jwt.get("tenantId") or not jwt.get("audience"):
            die(f"{where}: inboundAuth entraJwt requires entraJwt.tenantId and "
                "entraJwt.audience (without them, validate-jwt fails at runtime)")
    manifest["inboundAuth"] = inbound

    # Descriptive fields with constrained values: typos must never pass
    # silently (mcpProfile 'full' is roadmap-only and must FAIL today).
    profile = manifest.get("mcpProfile", "toolsOnly")
    if profile == "full":
        die(f"{where}: mcpProfile 'full' (resources adapter) is on roadmap "
            "v1.2 - use 'toolsOnly' for now")
    if profile != "toolsOnly":
        die(f"{where}: unknown mcpProfile '{profile}' - "
            "use toolsOnly ('full' in roadmap v1.2)")
    net = manifest.get("networkProfile", "public")
    if net not in ("public", "hybrid", "isolated"):
        die(f"{where}: invalid networkProfile '{net}' - public | hybrid | "
            "isolated. Note: the EFFECTIVE profile is platform-wide and set "
            "in infra/main.bicepparam; this is requirement documentation.")

    standards = manifest.get("standards") or {}
    if not isinstance(standards, dict):
        die(f"{where}: standards must be an object")
    allowed_std = {"errorModel", "writeIdempotency", "pagination", "rateLimit"}
    unknown_std = sorted(set(standards) - allowed_std)
    if unknown_std:
        die(f"{where}: standards contains unknown keys {unknown_std} - "
            f"allowed: {sorted(allowed_std)} (a typo here would silently "
            "disable validations)")
    em = standards.get("errorModel", "rfc7807")
    if em not in ("rfc7807", "none"):
        die(f"{where}: invalid standards.errorModel '{em}' - rfc7807 | none "
            "(unknown value would silently disable error validation)")
    wi = standards.get("writeIdempotency", "required")
    if wi not in ("required", "none"):
        die(f"{where}: invalid standards.writeIdempotency '{wi}' - required | none "
            "(unknown value would silently disable validation)")
    pag = standards.get("pagination")
    if pag is not None:
        if not isinstance(pag, dict):
            die(f"{where}: standards.pagination must be an object")
        for k, v in pag.items():
            if k not in ("maxPageSize", "defaultPageSize"):
                die(f"{where}: standards.pagination: unknown key '{k}' "
                    "(maxPageSize | defaultPageSize)")
            if not isinstance(v, int) or v <= 0:
                die(f"{where}: standards.pagination.{k} must be a positive integer")
    calls = (standards.get("rateLimit") or {}).get("callsPerMinutePerSubscription", 60)
    if not isinstance(calls, int) or calls <= 0:
        die(f"{where}: rateLimit.callsPerMinutePerSubscription must be a positive integer")
    manifest["standards"] = standards
    return manifest


# --- validations ---------------------------------------------------------------

def iter_operations(spec: dict):
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if verb in HTTP_VERBS and isinstance(op, dict):
                yield path, verb, op


def validate_openapi_version(api_name: str, spec: dict):
    version = spec.get("openapi")
    if not isinstance(version, str) or not re.fullmatch(r"3\.0\.\d+", version):
        die(f"apis/{api_name}/openapi.yaml: unsupported OpenAPI version "
            f"'{version}'; version 3.0.x is required")


def resolve_ref(spec: dict, obj):
    """Resolve local $ref ('#/components/...') within the same contract."""
    while isinstance(obj, dict) and isinstance(obj.get("$ref"), str) \
            and obj["$ref"].startswith("#/"):
        node = spec
        for part in obj["$ref"][2:].split("/"):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            die(f"unresolvable $ref: {obj['$ref']}")
        obj = node
    return obj


def validate_standards(api_name: str, spec: dict, standards: dict):
    rfc7807 = standards.get("errorModel") == "rfc7807"
    idem = standards.get("writeIdempotency") == "required"
    for path, verb, op in iter_operations(spec):
        where = f"{api_name}: {verb.upper()} {path}"
        if idem and verb in WRITE_VERBS:
            params = [resolve_ref(spec, p) for p in op.get("parameters", [])]
            ok = any(p.get("name") == "Idempotency-Key" and p.get("in") == "header"
                     and p.get("required") for p in params)
            if not ok:
                die(f"{where}: missing required Idempotency-Key header "
                    "(standards.writeIdempotency=required)")
        for status, resp in op.get("responses", {}).items():
            resp = resolve_ref(spec, resp)
            content = resp.get("content", {})
            for media, mdef in content.items():
                if "example" not in mdef and "examples" not in mdef:
                    die(f"{where} -> {status} ({media}): missing example "
                        "(examples are also mock data)")
            if rfc7807 and str(status).startswith(("4", "5")):
                problem = content.get("application/problem+json")
                if problem is None:
                    die(f"{where} -> {status}: errors must be "
                        "application/problem+json (standards.errorModel=rfc7807)")
                if "schema" not in problem:
                    die(f"{where} -> {status}: missing schema on "
                        "problem+json")


def type_label(value) -> str:
    return {bool: "boolean", int: "integer", float: "number", str: "string",
            list: "array", dict: "object", type(None): "null"}.get(type(value), "?")


def check_example(spec: dict, schema, value, where: str):
    """Recursively verify that example matches schema type, enum, and required.
    An out-of-schema example is not just bad docs: in mock mode it becomes the
    live RESPONSE and breaks contract guarantees (for example number where the
    contract promises string because YAML value was not quoted)."""
    schema = resolve_ref(spec, schema)
    if not isinstance(schema, dict):
        return
    if value is None and schema.get("nullable"):
        return
    if "enum" in schema and value not in schema["enum"]:
        die(f"{where}: value {json.dumps(value, ensure_ascii=False)} is not "
            f"in schema enum {schema['enum']}")
    t = schema.get("type")
    if t == "string" and not isinstance(value, str):
        die(f"{where}: example is {type_label(value)} but schema declares "
            "string - quote the YAML value (mock output would break contract)")
    elif t == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        die(f"{where}: example is {type_label(value)} but schema declares integer")
    elif t == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        die(f"{where}: example is {type_label(value)} but schema declares number")
    elif t == "boolean" and not isinstance(value, bool):
        die(f"{where}: example is {type_label(value)} but schema declares boolean")
    elif t == "array":
        if not isinstance(value, list):
            die(f"{where}: example is {type_label(value)} but schema declares array")
        for i, item in enumerate(value):
            check_example(spec, schema.get("items", {}), item, f"{where}[{i}]")
    elif t == "object" or "properties" in schema:
        if not isinstance(value, dict):
            die(f"{where}: example is {type_label(value)} but schema declares object")
        for req in schema.get("required", []):
            if req not in value:
                die(f"{where}: missing required field '{req}' in example")
        props = schema.get("properties") or {}
        for k, v in value.items():
            if k in props:
                check_example(spec, props[k], v, f"{where}.{k}")


def _check_media_examples(spec: dict, mdef: dict, where: str):
    schema = mdef.get("schema")
    if not schema:
        return
    if "example" in mdef:
        check_example(spec, schema, mdef["example"], f"{where} example")
    for ename, ex in (mdef.get("examples") or {}).items():
        if isinstance(ex, dict) and "value" in ex:
            check_example(spec, schema, ex["value"], f"{where} examples.{ename}")


def validate_examples(api_name: str, spec: dict):
    """Every example (request and response) must comply with its schema:
    examples are runtime-served mock data."""
    for path, verb, op in iter_operations(spec):
        where = f"{api_name}: {verb.upper()} {path}"
        rb = resolve_ref(spec, op.get("requestBody") or {})
        for media, mdef in (rb.get("content") or {}).items():
            _check_media_examples(spec, mdef, f"{where} requestBody ({media})")
        for status, resp in (op.get("responses") or {}).items():
            resp = resolve_ref(spec, resp)
            for media, mdef in (resp.get("content") or {}).items():
                _check_media_examples(spec, mdef, f"{where} -> {status} ({media})")


def validate_backend(api: dict):
    mode = api["backend"]["mode"]
    if mode == "hosted":
        die(f"API '{api['name']}': backend.mode 'hosted' is on the roadmap. "
            "Use 'mock' or 'external'.")
    if mode not in ("mock", "external"):
        die(f"API '{api['name']}': unknown backend.mode '{mode}' (mock | external)")
    oa = api["backend"].get("outboundAuth") or {}
    if not isinstance(oa, dict):
        die(f"API '{api['name']}': outboundAuth must be an object")
    oa_type = oa.get("type", "none")
    if oa_type == "mtls":
        die(f"API '{api['name']}': outboundAuth 'mtls' is on the roadmap.")
    if oa_type not in ("none", "apiKey", "oauth2-cc"):
        die(f"API '{api['name']}': unknown outboundAuth type '{oa_type}' "
            "(none | apiKey | oauth2-cc)")
    if oa_type == "apiKey" and not oa.get("secretRef"):
        die(f"API '{api['name']}': outboundAuth apiKey requires secretRef")
    if oa_type == "oauth2-cc":
        for field in ("tokenUrl", "clientId", "secretRef"):
            if not oa.get(field):
                die(f"API '{api['name']}': outboundAuth oauth2-cc requires '{field}' "
                    "(client secret is in Key Vault; only secretRef goes here)")
    ref = oa.get("secretRef")
    if ref and not re.fullmatch(SECRET_RE, ref):
        die(f"API '{api['name']}': invalid secretRef '{ref}' - "
            f"Key Vault/named value secret names allow only /{SECRET_RE}/")
    if mode == "external" and not api["backend"].get("url"):
        die(f"API '{api['name']}': backend.mode external requires url")


# --- policy construction --------------------------------------------------------

# --- x-mock engine --------------------------------------------------------------
# Single source for mock behavior: DATA lives in contract examples, optional
# DYNAMIC behavior lives in declarative `x-mock` rules per operation:
#
#   x-mock:
#     - when:    { param: address, contains: "napoli" }   # query/path/header
#       respond: { status: 200, example: ftth }
#     - when:    { param: address, missing: true }
#       respond: { status: 400 }
#     - respond: { status: 200, example: fttc }           # default (last, without when)
#
# Operators: equals | contains | startsWith (case-insensitive) | missing.
# Without x-mock (or if no rule matches): explicit return-response from first
# example. This avoids date-time reinterpretation in Consumption.
# Generator validates everything at build time.

STATUS_REASONS = {200: "OK", 201: "Created", 202: "Accepted", 204: "No Content",
                  400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
                  404: "Not Found", 409: "Conflict", 422: "Unprocessable Entity",
                  429: "Too Many Requests", 500: "Internal Server Error"}


def cs_str(s: str) -> str:
    """Escape per literal stringa C# dentro le policy expression."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def xmock_param_expr(spec: dict, op: dict, pname: str, where: str):
    """C# expression that reads a parameter from contract metadata (query,
    path, or header). Returns (expr, supports_missing)."""
    params = [resolve_ref(spec, p) for p in op.get("parameters", [])]
    p = next((x for x in params if x.get("name") == pname), None)
    if p is None:
        die(f"{where}: x-mock references parameter '{pname}' that does not exist "
            f"in operation (available: {[x.get('name') for x in params]})")
    loc = p.get("in")
    if loc == "query":
        return f'context.Request.Url.Query.GetValueOrDefault("{cs_str(pname)}", "")', True
    if loc == "path":
        return f'context.Request.MatchedParameters["{cs_str(pname)}"]', False
    if loc == "header":
        return f'context.Request.Headers.GetValueOrDefault("{cs_str(pname)}", "")', True
    die(f"{where}: x-mock on parameter '{pname}' with in='{loc}' is unsupported "
        "(query, path, header only)")


def xmock_condition(spec: dict, op: dict, rule_when, where: str) -> str:
    if not isinstance(rule_when, dict) or not isinstance(rule_when.get("param"), str):
        die(f"{where}: 'when' must be an object with 'param' and one operator")
    expr, supports_missing = xmock_param_expr(spec, op, rule_when["param"], where)
    ops = [k for k in ("equals", "contains", "startsWith", "missing") if k in rule_when]
    if len(ops) != 1:
        die(f"{where}: 'when' requires exactly ONE operator among "
            "equals | contains | startsWith | missing")
    o = ops[0]
    if o == "missing":
        if not supports_missing:
            die(f"{where}: 'missing' is invalid on path parameters "
                "(if operation matches, parameter is always present)")
        return f"@(string.IsNullOrEmpty({expr}))"
    val = rule_when[o]
    if not isinstance(val, str) or not val:
        die(f"{where}: operator '{o}' requires a non-empty string")
    v = cs_str(val.lower())
    if o == "equals":
        return f'@({expr}.ToLower() == "{v}")'
    if o == "contains":
        return f'@({expr}.ToLower().Contains("{v}"))'
    return f'@({expr}.ToLower().StartsWith("{v}"))'


def xmock_response(spec: dict, op: dict, respond, where: str) -> ET.Element:
    """<return-response> built from the contract example selected by the rule."""
    if not isinstance(respond, dict) or not isinstance(respond.get("status"), int):
        die(f"{where}: 'respond' must be an object with integer 'status'")
    status = respond["status"]
    resp = resolve_ref(spec, op.get("responses", {}).get(str(status)))
    if resp is None:
        die(f"{where}: respond.status {status} is not a declared response "
            f"in this operation (declared: {sorted(op.get('responses', {}))})")
    rr = ET.Element("return-response")
    ET.SubElement(rr, "set-status", {
        "code": str(status), "reason": STATUS_REASONS.get(status, "Response")})
    content = resp.get("content") or {}
    if content:
        media_name = "application/json" if "application/json" in content \
            else next(iter(content))
        media = content[media_name]
        ex_name = respond.get("example")
        if ex_name is not None:
            examples = media.get("examples") or {}
            if ex_name not in examples:
                die(f"{where}: respond.example '{ex_name}' not found among "
                    f"response {status} examples (available: {sorted(examples)})")
            value = examples[ex_name].get("value")
        elif "example" in media:
            value = media["example"]
        elif media.get("examples"):
            value = next(iter(media["examples"].values())).get("value")
        else:
            die(f"{where}: response {status} has no example in the contract")
        hdr = ET.SubElement(rr, "set-header", {
            "name": "Content-Type", "exists-action": "override"})
        ET.SubElement(hdr, "value").text = media_name
        ET.SubElement(rr, "set-body").text = json.dumps(value, ensure_ascii=False)
    return rr


def compile_xmock_blocks(api_name: str, spec: dict) -> list:
    """For each operation with x-mock: (operation-condition, compiled <choose>)."""
    blocks = []
    for path, verb, op in iter_operations(spec):
        rules = op.get("x-mock")
        if rules is None:
            continue
        where = f"{api_name}: {verb.upper()} {path} x-mock"
        if not isinstance(rules, list) or not rules:
            die(f"{where}: must be a non-empty rules list")
        inner = ET.Element("choose")
        default_el = None
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict) or "respond" not in rule:
                die(f"{where}: each rule must contain 'respond'")
            resp_el = xmock_response(spec, op, rule["respond"], f"{where} rule {i + 1}")
            if "when" in rule:
                if default_el is not None:
                    die(f"{where}: default rule (without when) must be last")
                cond = xmock_condition(spec, op, rule["when"], f"{where} rule {i + 1}")
                when = ET.SubElement(inner, "when", {"condition": cond})
                when.append(resp_el)
            else:
                if i != len(rules) - 1:
                    die(f"{where}: default rule (without when) must be last")
                default_el = resp_el
        if default_el is not None:
            oth = ET.SubElement(inner, "otherwise")
            oth.append(default_el)
        op_cond = (f'@(context.Operation.Method == "{verb.upper()}" && '
                   f'context.Operation.UrlTemplate == "{cs_str(path)}")')
        blocks.append((op_cond, inner))
    return blocks


def mock_inbound_elements(api_name: str, spec: dict) -> list:
    """Mock inbound elements: x-mock blocks (if present) + explicit fallback
    to first example. Do not use mock-response: in Consumption it may
    reinterpret date-time values and alter declared payload."""
    elems = []
    blocks = compile_xmock_blocks(api_name, spec)
    if blocks:
        outer = ET.Element("choose")
        for cond, inner in blocks:
            when = ET.SubElement(outer, "when", {"condition": cond})
            when.append(inner)
        elems.append(outer)

    fallback = ET.Element("choose")
    for path, verb, op in iter_operations(spec):
        responses = op.get("responses") or {}
        status_text = next((status for status in responses if str(status).isdigit()), None)
        if status_text is None:
            die(f"{api_name}: {verb.upper()} {path}: requires at least one "
                "numeric status response to generate mock")
        condition = (f'@(context.Operation.Method == "{verb.upper()}" && '
                     f'context.Operation.UrlTemplate == "{cs_str(path)}")')
        when = ET.SubElement(fallback, "when", {"condition": condition})
        when.append(xmock_response(
            spec, op, {"status": int(status_text)},
            f"{api_name}: {verb.upper()} {path} mock fallback"))
    elems.append(fallback)
    return elems


def auth_elements(client_id: str, api: dict) -> list:
    """Policy elements for outbound auth (same in facade and per-API mode)."""
    oa = api["backend"].get("outboundAuth", {})
    oa_type = oa.get("type", "none")
    if oa_type == "none" or not oa:
        return []

    if oa_type == "apiKey":
        h = ET.Element("set-header", {
            "name": oa.get("headerName", "X-Api-Key"),
            "exists-action": "override"})
        ET.SubElement(h, "value").text = "{{" + oa["secretRef"] + "}}"
        return [h]

    if oa_type == "oauth2-cc":
        cache_key = f"oauth-{client_id}-{api['name']}"
        elems = []
        elems.append(ET.Element("cache-lookup-value", {
            "key": cache_key, "variable-name": "bearerToken"}))
        choose = ET.Element("choose")
        when = ET.SubElement(choose, "when", {
            "condition": '@(!context.Variables.ContainsKey("bearerToken"))'})
        req = ET.SubElement(when, "send-request", {
            "mode": "new", "response-variable-name": "tokenResponse",
            "timeout": "20", "ignore-error": "false"})
        ET.SubElement(req, "set-url").text = oa["tokenUrl"]
        ET.SubElement(req, "set-method").text = "POST"
        hdr = ET.SubElement(req, "set-header", {
            "name": "Content-Type", "exists-action": "override"})
        ET.SubElement(hdr, "value").text = "application/x-www-form-urlencoded"
        # il client secret viene URL-encodato a runtime: puo' contenere &, +, =.
        # {{named value}} viene sostituita anche dentro le expression.
        body = ('@("grant_type=client_credentials'
                f"&client_id={urllib.parse.quote(oa['clientId'], safe='')}"
                '&client_secret=" + System.Net.WebUtility.UrlEncode("{{'
                + oa["secretRef"] + '}}")')
        if oa.get("scope"):
            body += f' + "&scope={urllib.parse.quote(oa["scope"], safe="")}"'
        body += ")"
        ET.SubElement(req, "set-body").text = body
        ET.SubElement(when, "set-variable", {
            "name": "tokenJson",
            "value": '@(((IResponse)context.Variables["tokenResponse"])'
                     '.Body.As<JObject>())'})
        ET.SubElement(when, "set-variable", {
            "name": "bearerToken",
            "value": '@(((JObject)context.Variables["tokenJson"])'
                     '["access_token"].ToString())'})
        # durata cache da expires_in (fallback 360s), con margine di 60s
        ET.SubElement(when, "cache-store-value", {
            "key": cache_key,
            "value": '@((string)context.Variables["bearerToken"])',
            "duration": '@(Math.Max(60, ((((JObject)context.Variables["tokenJson"])'
                        '["expires_in"]?.Value<int>()) ?? 360) - 60))'})
        elems.append(choose)
        auth = ET.Element("set-header", {
            "name": "Authorization", "exists-action": "override"})
        ET.SubElement(auth, "value").text = \
            '@("Bearer " + (string)context.Variables["bearerToken"])'
        elems.append(auth)
        return elems

    die(f"API '{api['name']}': unsupported outboundAuth type '{oa_type}'")


def mapping_sections(api_name: str) -> dict:
    """Read apis/<name>/policies/mapping.xml when present: enables request/
    response mapping to backends that diverge from the contract (HANDOVER
    promise). Returns {'inbound': [...], 'outbound': [...]} without <base/>."""
    path = REPO_ROOT / "apis" / api_name / "policies" / "mapping.xml"
    if not path.exists():
        return {"inbound": [], "outbound": []}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        die(f"{path.relative_to(REPO_ROOT).as_posix()}: invalid XML: {e}")
    if root.tag != "policies":
        die(f"{path.relative_to(REPO_ROOT).as_posix()}: expected <policies> document")
    out = {}
    for section in ("inbound", "outbound"):
        el = root.find(section)
        out[section] = [child for child in el] if el is not None else []
        out[section] = [c for c in out[section] if c.tag != "base"]
    return out


def serialize(policies: ET.Element) -> str:
    ET.indent(policies, space="  ")
    return ET.tostring(policies, encoding="unicode")


def build_api_policy(client_id: str, api: dict, spec: dict) -> str:
    """Single REST API policy (used as source in every mode)."""
    if api["backend"]["mode"] == "mock":
        # Single mock path: data in contract examples, optional dynamic behavior
        # in x-mock rules, explicit fallback to contract example.
        policies = ET.Element("policies")
        inbound = ET.SubElement(policies, "inbound")
        ET.SubElement(inbound, "base")
        for el in mock_inbound_elements(api["name"], spec):
            inbound.append(el)
        ET.SubElement(ET.SubElement(policies, "backend"), "base")
        ET.SubElement(ET.SubElement(policies, "outbound"), "base")
        ET.SubElement(ET.SubElement(policies, "on-error"), "base")
        return serialize(policies)
    # external: backend is API serviceUrl; add outbound auth plus optional
    # request/response mapping (apis/<name>/policies/mapping.xml)
    mapping = mapping_sections(api["name"])
    policies = ET.Element("policies")
    inbound = ET.SubElement(policies, "inbound")
    ET.SubElement(inbound, "base")
    for el in auth_elements(client_id, api):
        inbound.append(el)
    for el in mapping["inbound"]:
        inbound.append(el)
    ET.SubElement(ET.SubElement(policies, "backend"), "base")
    outbound = ET.SubElement(policies, "outbound")
    ET.SubElement(outbound, "base")
    for el in mapping["outbound"]:
        outbound.append(el)
    ET.SubElement(ET.SubElement(policies, "on-error"), "base")
    return serialize(policies)


def url_template_condition(spec: dict) -> str:
    # Exact match on contract paths: robust with multiple API prefixes and
    # resilient to prefix collisions between APIs.
    # (requires translateRequiredQueryParametersConduct: 'query' on import,
    # set by modules/api-with-mcp.bicep, otherwise UrlTemplate may diverge)
    templates = sorted(spec.get("paths", {}).keys())
    tlist = ", ".join(f'"{t}"' for t in templates)
    return f"@(new [] {{ {tlist} }}.Contains(context.Operation.UrlTemplate))"


def build_facade_policy(client_id: str, manifest: dict, specs: dict) -> str:
    """Facade policy: EXACT routing by operation UrlTemplate."""
    policies = ET.Element("policies")
    inbound = ET.SubElement(policies, "inbound")
    ET.SubElement(inbound, "base")
    choose = ET.SubElement(inbound, "choose")
    outbound_mappings = []  # (condition, [elementi outbound del mapping])

    for api in manifest["apis"]:
        name = api["name"]
        condition = url_template_condition(specs[name])
        when = ET.SubElement(choose, "when", {"condition": condition})
        if api["backend"]["mode"] == "mock":
            for el in mock_inbound_elements(name, specs[name]):
                when.append(el)
        else:
            ET.SubElement(when, "set-backend-service",
                          {"base-url": api["backend"]["url"]})
            for el in auth_elements(client_id, api):
                when.append(el)
            mapping = mapping_sections(name)
            for el in mapping["inbound"]:
                when.append(el)
            if mapping["outbound"]:
                outbound_mappings.append((condition, mapping["outbound"]))

    otherwise = ET.SubElement(choose, "otherwise")
    rr = ET.SubElement(otherwise, "return-response")
    ET.SubElement(rr, "set-status", {"code": "404", "reason": "Not Found"})
    hdr = ET.SubElement(rr, "set-header", {"name": "Content-Type", "exists-action": "override"})
    ET.SubElement(hdr, "value").text = "application/problem+json"
    body = ET.SubElement(rr, "set-body")
    body.text = ('{"type":"urn:problem:route-not-found","title":"Operation not routed",'
                 '"status":404,"detail":"Path does not match any facade API."}')

    ET.SubElement(ET.SubElement(policies, "backend"), "base")
    outbound = ET.SubElement(policies, "outbound")
    ET.SubElement(outbound, "base")
    if outbound_mappings:
        ochoose = ET.SubElement(outbound, "choose")
        for condition, elems in outbound_mappings:
            owhen = ET.SubElement(ochoose, "when", {"condition": condition})
            for el in elems:
                owhen.append(el)
    ET.SubElement(ET.SubElement(policies, "on-error"), "base")
    return serialize(policies)


# --- generazione Bicep ----------------------------------------------------------

def bq(s: str) -> str:
    """Escape helper for single-quoted Bicep strings."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("$", "\\$")


def bident(name: str) -> str:
    """Build a valid Bicep identifier from an API name (line-status -> line_status)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def emit_client_bicep(manifest: dict) -> str:
    """Resolved client.bicep: no manually written per-client logic."""
    client = manifest["client"]
    exposure = manifest.get("mcpExposure", {"mode": "perApi"})
    mode = exposure.get("mode", "perApi")
    per_api_mcp = mode != "facade"
    facade_mcp = mode != "perApi"
    facade_name = exposure.get("facadeName", "agent")

    secret_refs = sorted({api["backend"]["outboundAuth"]["secretRef"]
                          for api in manifest["apis"]
                          if api["backend"].get("outboundAuth", {}).get("secretRef")})

    inbound = manifest.get("inboundAuth", {"mode": "subscriptionKey"})
    jwt = inbound.get("entraJwt", {}) or {}

    lines = [
        "// =============================================================================",
        f"// GENERATED by tools/build-facade.py for client '{client}'. DO NOT EDIT.",
        "// Single source: ../mcp-manifest.yaml. Regenerated on every build (azd hook).",
        "// =============================================================================",
        "",
        "param apimName string",
        *([] if secret_refs else ["#disable-next-line no-unused-params"]),
        "param keyVaultName string",
        "param enableNativeMcp bool = true",
        "",
        "resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {",
        "  name: apimName",
        "}",
        "",
    ]

    if secret_refs:
        lines += [
            f"module namedValues '../../../modules/kv-named-values.bicep' = {{",
            f"  name: 'namedvalues-{client}'",
            "  params: {",
            "    apimName: apimName",
            "    keyVaultName: keyVaultName",
            "    secretRefs: [" + ", ".join(f"'{bq(r)}'" for r in secret_refs) + "]",
            "  }",
            "}",
            "",
        ]

    # APIM tags: one per client + one per backend mode used (mock/external).
    # They allow filtering and grouping in portal API list.
    modes = sorted({api["backend"]["mode"] for api in manifest["apis"]})
    tag_ids = [client] + modes
    tag_idents = []
    for tag in tag_ids:
        ident = f"tag_{bident(tag)}"
        tag_idents.append(ident)
        lines += [
            f"resource {ident} 'Microsoft.ApiManagement/service/tags@2024-06-01-preview' = {{",
            "  parent: apim",
            f"  name: '{bq(tag)}'",
            f"  properties: {{ displayName: '{bq(tag)}' }}",
            "}",
            "",
        ]

    module_idents = []
    for api in manifest["apis"]:
        name = api["name"]
        ident = f"api_{bident(name)}"
        module_idents.append(ident)
        tools = ", ".join(f"'{bq(t)}'" for t in api.get("mcpTools", []))
        backend_url = api["backend"].get("url", "")
        lines += [
            f"module {ident} '../../../modules/api-with-mcp.bicep' = {{",
            f"  name: 'api-{client}-{name}'",
            "  params: {",
            "    apimName: apimName",
            f"    clientId: '{bq(client)}'",
            f"    apiName: '{bq(name)}'",
            f"    displayName: '{bq(api['displayName'])}'",
            f"    specValue: loadTextContent('../../../apis/{name}/openapi.yaml')",
            f"    policyXml: loadTextContent('api-{name}.policy.xml')",
            f"    backendMode: '{api['backend']['mode']}'",
            f"    backendUrl: '{bq(backend_url)}'",
            f"    toolOperations: [{tools}]",
            f"    exposeMcp: enableNativeMcp && {'true' if per_api_mcp else 'false'}",
            f"    tagIds: ['{bq(client)}', '{api['backend']['mode']}']",
            "  }",
            f"  dependsOn: [{('namedValues, ' if secret_refs else '')}{', '.join(tag_idents)}]",
            "}",
            "",
        ]

    if facade_mcp:
        all_tools = ", ".join(f"'{bq(t)}'"
                              for api in manifest["apis"]
                              for api_tools in [api.get("mcpTools", [])]
                              for t in api_tools)
        module_idents.append("facade")
        lines += [
            "module facade '../../../modules/api-with-mcp.bicep' = {",
            f"  name: 'facade-{client}'",
            "  params: {",
            "    apimName: apimName",
            f"    clientId: '{bq(client)}'",
            f"    apiName: '{bq(facade_name)}'",
            f"    displayName: '{bq(exposure.get('displayName', client + ' agent tools'))}'",
            "    specValue: loadTextContent('facade.openapi.yaml')",
            "    policyXml: loadTextContent('facade.policy.xml')",
            "    backendMode: 'mock' // actual routing is in generated policy",
            f"    toolOperations: [{all_tools}]",
            "    exposeMcp: enableNativeMcp",
            f"    tagIds: ['{bq(client)}']",
            "  }",
            f"  dependsOn: [{('namedValues, ' if secret_refs else '')}{', '.join(tag_idents)}]",
            "}",
            "",
        ]

    # Product: REST APIs always exist; MCP servers exist only on tiers that
    # support native MCP resources.
    rest_product_names = [f"'{client}-{api['name']}'" for api in manifest["apis"]]
    mcp_product_names = []
    if per_api_mcp:
        mcp_product_names += [f"'{client}-{api['name']}-mcp'" for api in manifest["apis"]]
    if facade_mcp:
        rest_product_names.append(f"'{client}-{facade_name}'")
        mcp_product_names.append(f"'{client}-{facade_name}-mcp'")

    calls = manifest.get("standards", {}).get("rateLimit", {}) \
        .get("callsPerMinutePerSubscription", 60)
    lines += [
        "module product '../../../modules/client-product.bicep' = {",
        f"  name: 'product-{client}'",
        "  params: {",
        "    apimName: apimName",
        f"    clientId: '{bq(client)}'",
        f"    displayName: '{bq(manifest['displayName'])}'",
        f"    callsPerMinute: {calls}",
        "    apiResourceNames: concat([" + ", ".join(rest_product_names) + "], enableNativeMcp ? [" + ", ".join(mcp_product_names) + "] : [])",
        f"    inboundAuthMode: '{inbound.get('mode', 'subscriptionKey')}'",
        f"    jwtTenantId: '{bq(jwt.get('tenantId', ''))}'",
        f"    jwtAudience: '{bq(jwt.get('audience', ''))}'",
        f"    tagIds: ['{bq(client)}']",
        "  }",
        f"  dependsOn: [{', '.join(module_idents)}]",
        "}",
        "",
    ]

    urls = []
    if per_api_mcp:
        urls += [f"'${{apim.properties.gatewayUrl}}/{client}/{api['name']}-mcp/mcp'"
                 for api in manifest["apis"]]
    if facade_mcp:
        urls.append(f"'${{apim.properties.gatewayUrl}}/{client}/{facade_name}-mcp/mcp'")
    rest_urls = []
    if per_api_mcp:
        rest_urls += [f"'${{apim.properties.gatewayUrl}}/{client}/{api['name']}'"
                      for api in manifest["apis"]]
    if facade_mcp:
        rest_urls.append(f"'${{apim.properties.gatewayUrl}}/{client}/{facade_name}'")
    lines += [
        "output mcpServerUrls array = enableNativeMcp ? [",
        *[f"  {u}" for u in urls],
        "] : []",
        "output restApiUrls array = [",
        *[f"  {u}" for u in rest_urls],
        "]",
        "",
    ]
    return "\n".join(lines)


def emit_clients_index(client_ids: list) -> str:
    """infra/clients.gen.bicep: one module per client; main.bicep stays static."""
    lines = [
        "// =============================================================================",
        "// GENERATED by tools/build-facade.py: one module for each client in /clients.",
        "// DO NOT EDIT. New client = new folder with mcp-manifest.yaml + build.",
        "// =============================================================================",
        "",
        "param apimName string",
        "param keyVaultName string",
        "param enableNativeMcp bool = true",
        "",
    ]
    prev = None
    for cid in client_ids:
        lines += [
            f"module client_{bident(cid)} '../clients/{cid}/generated/client.bicep' = {{",
            f"  name: 'client-{cid}'",
            "  params: {",
            "    apimName: apimName",
            "    keyVaultName: keyVaultName",
            "    enableNativeMcp: enableNativeMcp",
            "  }",
        ]
        if prev is not None:
            # Serialized deploy: named values with same secretRef across
            # clients are the SAME APIM resource - concurrent PUTs can return 409.
            lines.append(f"  dependsOn: [client_{bident(prev)}]")
        lines += ["}", ""]
        prev = cid
    lines += ["output mcpServerUrls object = {"]
    for cid in client_ids:
        lines.append(f"  {bident(cid)}: client_{bident(cid)}.outputs.mcpServerUrls")
    lines += ["}", "", "output restApiUrls object = {"]
    for cid in client_ids:
        lines.append(f"  {bident(cid)}: client_{bident(cid)}.outputs.restApiUrls")
    lines += ["}", ""]
    return "\n".join(lines)


# --- build one client -----------------------------------------------------------

def build_client(client_dir: Path):
    manifest = validate_manifest(read_yaml(client_dir / "mcp-manifest.yaml"),
                                 client_dir.name)
    client_id = manifest["client"]
    standards = manifest["standards"]
    exposure = manifest["mcpExposure"]
    mode = exposure["mode"]
    if mode == "both":
           warn(f"{client_id}: mode 'both' exposes same tools on two MCP servers. "
               "Connect Copilot Studio to only one to avoid duplicate tools.")
    out_dir = client_dir / "generated"
    out_dir.mkdir(exist_ok=True)

    # ---- load specs and validate ----------------------------------------------
    facade_needed = mode != "perApi"
    specs, seen_ops, seen_paths = {}, {}, {}
    components = {}  # sezione components -> nome -> definizione
    for api in manifest["apis"]:
        name = api["name"]
        validate_backend(api)
        spec = read_yaml(REPO_ROOT / "apis" / name / "openapi.yaml")
        if not isinstance(spec, dict) or not spec.get("paths"):
            die(f"apis/{name}/openapi.yaml: empty contract or missing paths")
        validate_openapi_version(name, spec)
        specs[name] = spec
        validate_standards(name, spec, standards)
        validate_examples(name, spec)
        op_ids = set()
        for p, item in spec.get("paths", {}).items():
            # In facade merge, paths are combined into one contract: they must
            # be unique. In perApi mode each API has its own prefix.
            if facade_needed:
                if p in seen_paths:
                    die(f"path '{p}' is duplicated in '{name}' and '{seen_paths[p]}'")
                seen_paths[p] = name
            for verb, op in item.items():
                if verb not in HTTP_VERBS or not isinstance(op, dict) or "operationId" not in op:
                    continue
                oid = op["operationId"]
                if not re.fullmatch(OPID_RE, oid):
                    die(f"API '{name}': invalid operationId '{oid}' - use only "
                        f"letters, digits, and hyphens (/{OPID_RE}/). APIM import "
                        "normalizes other characters (for example underscore -> hyphen) "
                        "and MCP tool references would break at deploy time.")
                op_ids.add(oid)
                if oid in seen_ops:
                    die(f"operationId '{oid}' is duplicated in '{name}' and '{seen_ops[oid]}' "
                        f"(operationId becomes tool name and must be unique per client)")
                seen_ops[oid] = name
        for tool in api["mcpTools"]:
            if tool not in op_ids:
                die(f"API '{name}': mcpTools '{tool}' does not exist as operationId "
                    f"in contract (available: {sorted(op_ids)})")
        for section, defs in (spec.get("components") or {}).items():
            if not isinstance(defs, dict):
                continue
            bucket = components.setdefault(section, {})
            for cname, cdef in defs.items():
                if cname in bucket and bucket[cname] != cdef:
                    die(f"components.{section} '{cname}' has conflicting definitions "
                        "across APIs; rename it")
                bucket[cname] = cdef

    print(f"[build-facade] {client_id}: validations OK "
          f"({len(seen_ops)} tool)")

    # ---- per-API policy (REST source, used in every mode) ----------------------
    for api in manifest["apis"]:
        write_text(out_dir / f"api-{api['name']}.policy.xml",
                   build_api_policy(client_id, api, specs[api["name"]]))

    # ---- Bicep composition resolved from manifest -------------------------------
    write_text(out_dir / "client.bicep", emit_client_bicep(manifest))

    if mode == "perApi":
        # Empty stubs so loadTextContent compiles even without facade
        write_text(out_dir / "facade.openapi.yaml",
                   "# not used (mcpExposure.mode: perApi)\n")
        write_text(out_dir / "facade.policy.xml", EMPTY_POLICY)
        print(f"[build-facade] {client_id}: mode=perApi, generated per-API policies + client.bicep")
        return

    # ---- merge contract ---------------------------------------------------------
    facade = {
        "openapi": "3.0.3",
        "info": {
            "title": exposure.get("displayName", f"{client_id} agent tools"),
            "version": "1.0.0",
            "description": "GENERATED contract: merge of client APIs. Do not edit; modify sources in /apis.",
        },
        "servers": [{"url": "https://placeholder.invalid"}],
        "paths": {},
        # ALL components sections (schemas, parameters, responses, ...):
        # copying only some would leave dangling $ref in merged contract
        "components": copy.deepcopy(components),
    }
    for name, spec in specs.items():
        facade["paths"].update(copy.deepcopy(spec.get("paths", {})))
    write_text(out_dir / "facade.openapi.yaml",
               yaml.safe_dump(facade, sort_keys=False, allow_unicode=True))

    write_text(out_dir / "facade.policy.xml",
               build_facade_policy(client_id, manifest, specs))
    print(f"[build-facade] {client_id}: generated facade + per-API policies + client.bicep (mode={mode})")


# --- schema catalog and governance ----------------------------------------------
# 80/20 principle: the index is DERIVED from contracts on every build
# (never manually documented); reuse happens via controlled duplication
# (no cross-file $ref); governance is intentionally minimal:
# - CANONICAL schemas (apis/canonical-schemas.yaml): same name => same
#   structure across the whole library, otherwise BUILD error;
# - non-canonical same-name divergent schemas across contracts: WARNING;
# - same structure with different names: reuse suggestion.

def load_canonical_names() -> set:
    path = REPO_ROOT / "apis" / "canonical-schemas.yaml"
    if not path.exists():
        return {"Problem"}
    data = read_yaml(path)
    names = (data or {}).get("schemas")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        die("apis/canonical-schemas.yaml: expected 'schemas' as list of names")
    return set(names)


def schema_fingerprint(definition) -> str:
    """Structural fingerprint: same definition (regardless of key order)
    => same fingerprint. Used to detect duplicates."""
    return json.dumps(definition, sort_keys=True, ensure_ascii=False)


def scan_schema_library() -> dict:
    """schema name -> list of (contract, definition) across the whole library."""
    library = {}
    for spec_path in sorted((REPO_ROOT / "apis").glob("*/openapi.yaml")):
        spec = read_yaml(spec_path)
        schemas = ((spec or {}).get("components") or {}).get("schemas") or {}
        for name, definition in schemas.items():
            library.setdefault(name, []).append((spec_path.parent.name, definition))
    return library


def check_schema_library():
    """Cross-library governance: enforce canonical schemas, warn for divergent
    same-name schemas, and suggest structural deduplication."""
    canonical = load_canonical_names()
    library = scan_schema_library()
    for name, entries in sorted(library.items()):
        variants = {}
        for contract, definition in entries:
            variants.setdefault(schema_fingerprint(definition), []).append(contract)
        if len(variants) > 1:
            where = "; ".join(
                f"{', '.join(cs)}" for cs in variants.values())
            if name in canonical:
                die(f"CANONICAL schema '{name}' has DIFFERENT structures "
                    f"across contracts ({where}): canonical schemas must be "
                    "identical across the library (apis/canonical-schemas.yaml)")
            warn(f"schema '{name}' has different structures across "
                 f"contracts ({where}): intentional variant or drift? "
                 "If shared, align definitions.")
    # Structural duplicates with different names (object schemas only).
    by_fp = {}
    for name, entries in library.items():
        for contract, definition in entries:
            if isinstance(definition, dict) and definition.get("properties"):
                by_fp.setdefault(schema_fingerprint(definition), set()).add(name)
    for names in by_fp.values():
        if len(names) > 1:
            ordered = sorted(names)
            warn(f"schemas {ordered} have IDENTICAL structure: likely the "
                 "same concept - reuse one name")


def print_schema_catalog():
    """Data structure catalog: what exists, where, and who uses it.
    Check this BEFORE defining new schemas (discovery skill)."""
    canonical = load_canonical_names()
    users = {}
    for mpath in sorted((REPO_ROOT / "clients").glob("*/mcp-manifest.yaml")):
        m = read_yaml(mpath)
        for api in (m.get("apis") or []):
            if isinstance(api, dict) and isinstance(api.get("name"), str):
                users.setdefault(api["name"], []).append(mpath.parent.name)
    library = scan_schema_library()
    for name, entries in sorted(library.items()):
        contracts = sorted({c for c, _ in entries})
        clients = sorted({u for c in contracts for u in users.get(c, [])})
        tag = "  [CANONICAL]" if name in canonical else ""
        divergent = len({schema_fingerprint(d) for _, d in entries}) > 1
        if divergent:
                        tag += "  [DIVERGENT STRUCTURES]"
        print(f"\n{name}{tag}")
        print(f"  contracts: {', '.join(contracts)}"
                            f"  |  clients: {', '.join(clients) or 'none'}")
        definition = entries[0][1]
        props = definition.get("properties") if isinstance(definition, dict) else None
        if props:
            for pname, pdef in props.items():
                ptype = pdef.get("type", "?") if isinstance(pdef, dict) else "?"
                if isinstance(pdef, dict) and "enum" in pdef:
                    ptype += f" enum[{', '.join(map(str, pdef['enum']))}]"
                print(f"    {pname}: {ptype}")


def print_catalog():
    """Contract library catalog: what exists, what it does, and who uses it.
    Check this BEFORE writing a new contract for a client."""
    users = {}
    for mpath in sorted((REPO_ROOT / "clients").glob("*/mcp-manifest.yaml")):
        m = read_yaml(mpath)
        for api in (m.get("apis") or []):
            if isinstance(api, dict) and isinstance(api.get("name"), str):
                users.setdefault(api["name"], []).append(mpath.parent.name)
    for spec_path in sorted((REPO_ROOT / "apis").glob("*/openapi.yaml")):
        name = spec_path.parent.name
        spec = read_yaml(spec_path)
        info = spec.get("info", {})
        rules = sum(len(op.get("x-mock", []))
                    for _, _, op in iter_operations(spec))
        mock = f"dynamic mock: {rules} rules" if rules else "static mock"
        used = ", ".join(users.get(name, [])) or "none"
        print(f"\napis/{name}  [{mock}]  usato da: {used}")
        print(f"  {info.get('title', '?')} - "
              f"{' '.join(str(info.get('description', '')).split())}")
        for path, verb, op in iter_operations(spec):
            print(f"    {op.get('operationId', '?'):24} {verb.upper():6} {path}  "
                  f"- {op.get('summary', '')}")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--catalog":
        if len(sys.argv) == 3 and sys.argv[2] == "schemas":
            print_schema_catalog()
        elif len(sys.argv) == 2:
            print_catalog()
        else:
            die("usage: build-facade.py --catalog [schemas]")
        return
    if len(sys.argv) > 2:
        die("usage: build-facade.py [clients/<clientId> | --catalog [schemas]]")
    if len(sys.argv) == 2:
        targets = [REPO_ROOT / sys.argv[1]]
    else:
        targets = sorted(p.parent for p in (REPO_ROOT / "clients").glob("*/mcp-manifest.yaml"))
        if not targets:
            die("no clients/*/mcp-manifest.yaml found")
    for client_dir in targets:
        if not (client_dir / "mcp-manifest.yaml").exists():
            die(f"{client_dir}: mcp-manifest.yaml not found")
        build_client(client_dir)

    # ---- client index: ALWAYS across all clients, even in single-client build ----
    all_dirs = sorted(p.parent for p in (REPO_ROOT / "clients").glob("*/mcp-manifest.yaml"))
    all_ids, secret_owners, contract_users, exposed_tools = [], {}, {}, []
    for d in all_dirs:
        m = read_yaml(d / "mcp-manifest.yaml")
        cid = m.get("client") if isinstance(m, dict) else None
        if cid != d.name:
            die(f"clients/{d.name}: client field '{cid}' must match "
                "folder name")
        if cid in all_ids:
            die(f"client id '{cid}' is duplicated in /clients")
        all_ids.append(cid)
        if not (d / "generated" / "client.bicep").exists():
            # Clean clone or deleted generated/: index references client.bicep
            # for ALL clients, so generate missing one instead of failing.
            print(f"[build-facade] {d.name}: missing client.bicep - generating it")
            build_client(d)
        for api in (m.get("apis") or []):
            if not isinstance(api, dict):
                continue
            if isinstance(api.get("name"), str):
                contract_users.setdefault(api["name"], []).append(cid)
                for tool in api.get("mcpTools", []):
                    exposed_tools.append((tool, cid, api["name"]))
            ref = ((api.get("backend") or {}).get("outboundAuth") or {}).get("secretRef")
            if ref:
                if ref in secret_owners and secret_owners[ref] != cid:
                    warn(f"secretRef '{ref}' used by both '{secret_owners[ref]}' and "
                        f"'{cid}': APIM named values are service-wide, clients share "
                        "the SAME secret")
                secret_owners.setdefault(ref, cid)
    for contract, users in sorted(contract_users.items()):
        if len(users) > 1:
              warn(f"contract 'apis/{contract}' shared by: {', '.join(users)} - "
                  "treat as READ-ONLY: for a variant create a new folder with "
                  "a new name (editing it changes ALL clients)")

    # ---- GLOBAL operationId uniqueness (= MCP tool names) ----------------------
    # PLATFORM CONSTRAINT (observed in practice, 2026-08-04): in APIM MCP export
    # (2025-09-01-preview), tool names are effectively unique across the WHOLE
    # service, not per MCP server. A cross-client duplicate fails deployment
    # with a non-actionable 502. Better fail here.
    global_ops = {}
    for oid, cid, contract in sorted(exposed_tools):
        prev = global_ops.get(oid)
        if prev and prev[0] != cid:
            die(f"operationId '{oid}' exposed as mcpTool by both '{prev[0]}' "
                f"(apis/{prev[1]}) che da '{cid}' (apis/{contract}): i nomi "
                "MCP tool names must be unique across the WHOLE APIM - deployment "
                "would fail with 502. Rename operationId in one of the two "
                "contracts (if contract is shared across clients on the same "
                "APIM, create a variant).")
        global_ops.setdefault(oid, (cid, contract))

    # ---- schema governance across whole library (canonical + divergence) --------
    check_schema_library()

    write_text(REPO_ROOT / "infra" / "clients.gen.bicep", emit_clients_index(all_ids))
    print(f"[build-facade] infra/clients.gen.bicep: {len(all_ids)} clients ({', '.join(all_ids)})")


if __name__ == "__main__":
    main()
