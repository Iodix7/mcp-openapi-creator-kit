"""Synthetic interface agreements; no customer fixtures or live gateway calls."""
import base64
import copy
import json
import re
from xml.etree import ElementTree as ET

import pytest
import yaml

from mcp_openapi_creator_kit import policy
from mcp_openapi_creator_kit.handoff import consumer_handoff
from mcp_openapi_creator_kit.rest_runtime import (
    ApiRuntime, ContractRuntime, TargetError, UUID_PATTERN, build_runtime_policy,
    contract_runtime, response_data, safe_pattern, validate_profile_runtime)
from test_build_facade import bf, dp, vr, vm
from test_deployment_recovery import recovery
from verification import Credentials, VerificationFailure


CORRELATION = "00112233-4455-6677-8899-AABBCCDDEEFF"
PATH = "/v1/things/{thingId}/status"


def agreement():
    headers = {
        "X-Correlation-Id": {"schema": {"type": "string", "format": "uuid"}},
        "Cache-Control": {"schema": {"type": "string", "enum": ["no-store"]}},
    }
    responses = {}
    for status, code in ((400, "BAD_REQUEST"), (401, "UNAUTHORIZED"), (403, "FORBIDDEN"),
                         (404, "NOT_FOUND"), (429, "RATE_LIMITED"), (503, "SOURCE_UNAVAILABLE")):
        body = {"type": "about:blank", "title": code, "status": status,
                "detail": code, "code": code, "message": code, "correlationId": CORRELATION}
        properties = {name: {"type": "integer" if name == "status" else "string"} for name in body}
        properties["correlationId"]["format"] = "uuid"
        responses[str(status)] = {
            "description": code, "headers": copy.deepcopy(headers),
            "content": {"application/problem+json": {
                "schema": {"type": "object", "additionalProperties": False,
                           "required": list(body), "properties": properties}, "example": body}}}
    responses["429"]["headers"]["Retry-After"] = {
        "schema": {"type": "integer", "minimum": 1}, "example": 5}
    responses["200"] = {
        "description": "Found", "headers": copy.deepcopy(headers),
        "content": {"application/json": {
            "schema": {"type": "object", "additionalProperties": False,
                       "required": ["thingId", "updatedAt", "correlationId"],
                       "properties": {"thingId": {"type": "string"}, "updatedAt": {"type": "string", "format": "date-time"},
                                      "correlationId": {"type": "string", "format": "uuid"}}},
            "examples": {name: {"value": {"thingId": name, "updatedAt": "2025-01-02T12:34:56+02:00",
                                         "correlationId": CORRELATION}}
                         for name in ("T-1", "T-2", "T-3")}}}}
    op = {
        "operationId": "get-thing-status", "summary": "Get status",
        "parameters": [
            {"name": "thingId", "in": "path", "required": True,
             "description": "Case-sensitive thing identifier.",
             "schema": {"type": "string", "minLength": 1, "maxLength": 64,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"}},
            {"name": "X-Correlation-Id", "in": "header", "required": True,
             "description": "Canonical UUID echoed exactly.",
             "schema": {"type": "string", "format": "uuid"}, "example": CORRELATION}],
        "responses": {"200": responses.pop("200"), **responses},
        "x-mock": [
            *[{"when": {"param": "thingId", "equals": name, "caseSensitive": True},
               "respond": {"status": 200, "example": name}} for name in ("T-1", "T-2", "T-3")],
            {"respond": {"status": 404}}]}
    spec = {
        "openapi": "3.0.3", "info": {"title": "Synthetic things", "version": "1.0.0"},
        "paths": {PATH: {"get": op}},
        "security": [{"clientKey": []}],
        "components": {"securitySchemes": {"clientKey": {"type": "apiKey", "in": "header", "name": "x-api-key"}}},
        "x-kit-runtime": {"version": 1, "securityScheme": "clientKey",
                          "correlation": {"header": "X-Correlation-Id", "bodyProperty": "correlationId"},
                          "errors": {name: {"status": status} for name, status in (
                              ("badRequest", 400), ("unauthorized", 401), ("forbidden", 403), ("rateLimited", 429))}}}
    manifest = {
        "client": "demo", "displayName": "Demo", "targets": {"consumer": "rest"},
        "mcpExposure": {"mode": "perApi"},
        "inboundAuth": {"mode": "subscriptionKey", "subscriptionKeyHeader": "x-api-key"},
        "apis": [{"name": "things", "displayName": "Things", "backend": {"mode": "mock"},
                  "mcpTools": ["get-thing-status"],
                  "runtime": {"allowedSubscriptionIds": ["demo-pilot"], "rateLimit": {"calls": 60, "renewalPeriod": 60}}}]}
    return spec, manifest


def compile_policy(spec=None, manifest=None):
    original, config = agreement()
    spec, manifest = spec or original, manifest or config
    return build_runtime_policy(spec, manifest["apis"][0], manifest, bf)


def write_agreement(root, spec, manifest):
    api = root / "apis" / "things"
    client = root / "clients" / "demo"
    api.mkdir(parents=True)
    client.mkdir(parents=True)
    (api / "openapi.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    (client / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return client


def test_runtime_order_and_total_response_headers():
    text = compile_policy()
    root = ET.fromstring(text)
    inbound = root.find("inbound")
    assert [node.tag for node in inbound] == [
        "set-variable", "set-variable", "choose", "choose", "base", "choose", "rate-limit", "choose"]
    branches = inbound.findall("choose")
    assert branches[0].find("when/return-response/set-status").get("code") == "401"
    assert 'Headers.GetValueOrDefault("x-api-key"' in branches[0].find("when").get("condition")
    assert 'Subscription.Id == "demo-pilot"' in branches[1].find("when").get("condition")
    assert branches[1].find("when/return-response/set-status").get("code") == "403"
    assert branches[2].find("when/return-response/set-status").get("code") == "400"
    validation = branches[2].find("when").get("condition")
    assert ".Length < 1" in validation and ".Length > 64" in validation
    assert "\\\\z" in validation and "Guid.Parse" not in text and "TryParse" not in text
    assert len(branches[3].findall("when")) == 3
    assert branches[3].find("otherwise/return-response/set-status").get("code") == "404"
    assert ".ToLower()" not in text
    for rr in root.findall(".//return-response"):
        status = int(rr.find("set-status").get("code"))
        headers = {h.get("name"): h.find("value").text for h in rr.findall("set-header")}
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Correlation-Id"] == '@((string)context.Variables["kitCorrelation"])'
        assert headers["Content-Type"] == ("application/json" if status == 200 else "application/problem+json")
        source = rr.find("set-body").text
        encoded = re.search(r'FromBase64String\("([^"]+)"\)', source)[1]
        body = json.loads(base64.b64decode(encoded))
        assert 'body["correlationId"] = (string)context.Variables["kitCorrelation"]' in source
        if status == 200:
            assert body["updatedAt"] == "2025-01-02T12:34:56+02:00"
        else:
            assert set(body) == {"type", "title", "status", "detail", "code", "message", "correlationId"}
            assert body["detail"] == body["message"]
            assert body["status"] == status


def test_early_errors_recompute_correlation_and_do_not_expose_provider_messages():
    text = compile_policy()
    error = ET.fromstring(text).find("on-error")
    assert [e.tag for e in error][:2] == ["set-variable", "set-variable"]
    assert error.find("set-variable").get("name") == "kitCorrelationValid"
    assert "Guid.NewGuid().ToString()" in error.findall("set-variable")[1].get("value")
    assert "SubscriptionKeyNotFound" in text and "SubscriptionKeyInvalid" in text
    assert "LastError.Message" not in text
    assert 'LastError.PolicyId == "kit-runtime-rate"' in error.findall("choose")[1].find("when").get("condition")
    assert error[-1].tag == "base"  # Unknown gateway failures are not disguised as domain errors.


@pytest.mark.parametrize("status", [429, 503])
def test_simulation_is_source_only_after_rate_limit(status):
    spec, manifest = agreement()
    manifest["apis"][0]["runtime"]["simulate"] = {"status": status}
    root = ET.fromstring(compile_policy(spec, manifest))
    inbound = root.find("inbound")
    assert inbound[-2].tag == "rate-limit"
    assert inbound[-1].find("set-status").get("code") == str(status)
    assert not inbound[-1].findall("choose")
    if status == 429:
        assert inbound[-1].find("set-header[@name='Retry-After']/value").text == "5"
    real = root.find("on-error/choose[2]/when/return-response")
    assert real.find("set-header[@name='Retry-After']/value").text == (
        '@(System.Convert.ToInt32(context.Variables["kitRetryAfter"]).ToString())')


def test_runtime_variable_methods_require_concrete_types():
    root = ET.fromstring(compile_policy())
    expressions = []
    for node in root.iter():
        expressions.extend(value for value in node.attrib.values() if value.startswith(("@(", "@{")))
        if node.text and node.text.startswith(("@(", "@{")):
            expressions.append(node.text)
    assert expressions
    assert not any(re.search(r'context\.Variables\["[^"]+"\]\s*\??\.', value) for value in expressions)
    retry = root.find("on-error/choose[2]/when/return-response/set-header[@name='Retry-After']/value").text
    assert retry == '@(System.Convert.ToInt32(context.Variables["kitRetryAfter"]).ToString())'
    condition = root.find("on-error/choose[2]/when").get("condition")
    assert 'context.Variables.ContainsKey("kitRetryAfter")' in condition
    assert "GetValueOrDefault" not in retry  # Do not invent a remaining interval.


@pytest.mark.parametrize("value,valid", [
    (CORRELATION, True), (CORRELATION.lower(), True),
    ("00000000-0000-0000-0000-000000000000", True),
    ("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF", True),
    ("00112233445566778899AABBCCDDEEFF", False), ("{" + CORRELATION + "}", False),
    (" " + CORRELATION, False), (CORRELATION + "\n", False), ("", False), ("not-a-uuid", False)])
def test_uuid_grammar_is_canonical_not_v4_or_case_restricted(value, valid):
    pattern = safe_pattern(UUID_PATTERN).removesuffix(r"\z")
    assert (re.fullmatch(pattern, value) is not None) is valid


@pytest.mark.parametrize("pattern", [
    ".*", "^(a+)+$", "^[A-Z]+$", "^[A-Z]{1,999}$", "^[Z-A]{1,3}$",
    "^[a-z]{10,1}$", r"^\w{1,64}$", r"^[A-Z]\n$", "^hello|bye$", "^$", "^.*(?=a)$"])
def test_unbounded_or_nonportable_regex_rejected(pattern):
    with pytest.raises(TargetError, match="pattern"):
        safe_pattern(pattern)


@pytest.mark.parametrize("value,valid", [
    ("A", True), ("A" * 64, True), ("A" * 65, False), ("A_b-9", True),
    ("_A", False), ("-A", False), ("", False), ("A\n", False), ("é", False), ("A B", False)])
def test_identifier_validation_grammar(value, valid):
    pattern = safe_pattern("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$").removesuffix(r"\z")
    assert (re.fullmatch(pattern, value) is not None) is valid


@pytest.mark.parametrize("operator", ["equals", "contains", "startsWith"])
def test_case_sensitive_rules_have_consistent_rest_and_mcp_semantics(operator):
    spec, _ = agreement()
    op = spec["paths"][PATH]["get"]
    for sensitive in (False, True):
        when = {"param": "thingId", operator: "T-1", "caseSensitive": sensitive}
        expected = not sensitive
        assert vr.condition_matches(when, {"thingId": "t-1"}) is expected
        assert policy._condition_matches(when, {"thingId": "t-1"}) is expected
        compiled = bf.xmock_condition(spec, PATH, op, when, "test")
        assert (".ToLower()" in compiled) is (not sensitive)
        mcp = policy.condition_expression(when)
        assert ("OrdinalIgnoreCase" in mcp) is (not sensitive)
    legacy = {"param": "thingId", operator: "T-1"}
    assert vr.condition_matches(legacy, {"thingId": "t-1"})
    assert ".ToLower()" in bf.xmock_condition(spec, PATH, op, legacy, "test")


@pytest.mark.parametrize("update", [
    {"caseSensitive": "true"}, {"caseSensitive": 1}, {"caseSensitve": True},
    {"missing": True, "caseSensitive": True, "equals": "x"},
])
def test_bad_rule_options_fail_in_both_compilers(update):
    spec, _ = agreement()
    op = spec["paths"][PATH]["get"]
    op["x-mock"][0]["when"].update(update)
    spec.pop("x-kit-runtime")
    with pytest.raises(SystemExit):
        bf.compile_xmock_blocks("things", spec)
    tool = policy.ToolDefinition("things", PATH, "get", op, [], spec)
    with pytest.raises(policy.PolicyBuildError):
        policy.mock_rules(tool)


@pytest.mark.parametrize("mutate", [
    lambda s, m: m["apis"][0].pop("runtime"),
    lambda s, m: s.pop("x-kit-runtime"),
    lambda s, m: m["apis"][0]["runtime"].update(allowedSubscriptionIds=[]),
    lambda s, m: m["apis"][0]["runtime"].update(allowedSubscriptionIds=["demo-pilot", "demo-pilot"]),
    lambda s, m: m["apis"][0]["runtime"].pop("allowedSubscriptionIds"),
    lambda s, m: m["apis"][0]["runtime"].update(allowedSubscriptionIds=["{{secret}}"]),
    lambda s, m: m["apis"][0]["runtime"].update(allowAll=True),
    lambda s, m: m["apis"][0]["runtime"].pop("rateLimit"),
    lambda s, m: m["apis"][0]["runtime"]["rateLimit"].update(calls=0),
    lambda s, m: m["apis"][0]["runtime"]["rateLimit"].update(calls=True),
    lambda s, m: m["apis"][0]["runtime"]["rateLimit"].update(renewalPeriod=301),
    lambda s, m: m["apis"][0]["runtime"].update(simulate={"status": 400}),
    lambda s, m: m["apis"][0]["runtime"].update(simulate={"status": 429.0}),
    lambda s, m: m["inboundAuth"].pop("subscriptionKeyHeader"),
    lambda s, m: m["inboundAuth"].update(subscriptionKeyHeader="Authorization"),
    lambda s, m: m["inboundAuth"].update(subscriptionKeyHeader="bad\r\nheader"),
    lambda s, m: m["inboundAuth"].update(mode="entraJwt"),
    lambda s, m: m["targets"].update(consumer="copilot-studio"),
    lambda s, m: m["targets"].update(gateway="ai-gateway-preview"),
    lambda s, m: m["mcpExposure"].update(mode="facade"),
    lambda s, m: m["apis"][0]["backend"].update(mode="external", url="https://example.invalid"),
    lambda s, m: s["x-kit-runtime"].update(version=2),
    lambda s, m: s["x-kit-runtime"].update(version=True),
    lambda s, m: s["x-kit-runtime"].update(policy="<base/>"),
    lambda s, m: s["x-kit-runtime"]["errors"]["unauthorized"].update(status=403),
    lambda s, m: s["security"].append({}),
    lambda s, m: s["components"]["securitySchemes"]["clientKey"].update(name="wrong-header"),
    lambda s, m: s["paths"][PATH].update(post=copy.deepcopy(s["paths"][PATH]["get"])),
    lambda s, m: s["paths"][PATH].update(trace=copy.deepcopy(s["paths"][PATH]["get"])),
    lambda s, m: s["paths"][PATH]["get"]["parameters"][0]["schema"].update(type="integer"),
    lambda s, m: s["paths"][PATH]["get"]["parameters"][0]["schema"].update(nullable=True),
    lambda s, m: s["paths"][PATH]["get"]["parameters"][0]["schema"].update(format="custom"),
    lambda s, m: s["paths"][PATH]["get"]["parameters"][1].update(required=False),
    lambda s, m: s["paths"][PATH]["get"]["parameters"][1]["schema"].update(pattern="^[A-F]{4}$"),
    lambda s, m: s["paths"][PATH]["get"]["x-mock"].pop(),
    lambda s, m: s["paths"][PATH]["get"]["x-mock"][0]["respond"].update(headers={"X-Code": "yes"}),
    lambda s, m: s["paths"][PATH]["get"]["x-mock"][0]["when"].update(equals='{{named-value}}'),
])
def test_unsupported_or_missing_semantics_fail_closed(mutate):
    spec, manifest = agreement()
    mutate(spec, manifest)
    with pytest.raises((TargetError, SystemExit)):
        compile_policy(spec, manifest)


@pytest.mark.parametrize("mutate", [
    lambda r: r["headers"]["Cache-Control"].update(schema={"type": "string"}, description="no-store"),
    lambda r: r["headers"]["Cache-Control"].update(example="public"),
    lambda r: r["headers"]["Cache-Control"]["schema"].update(enum=["no-store", "public"]),
    lambda r: r["headers"]["Cache-Control"]["schema"].update(enum=["@{return \"unsafe\";}"]),
    lambda r: r["headers"]["Cache-Control"]["schema"].update(enum=["{{secret}}"]),
    lambda r: r["headers"].pop("X-Correlation-Id"),
    lambda r: r["headers"].update({"Content-Length": {"example": 10}}),
    lambda r: r["content"]["application/problem+json"]["schema"]["properties"]["correlationId"].update(enum=[CORRELATION]),
])
def test_ambiguous_headers_and_fixed_correlation_fail(mutate):
    spec, manifest = agreement()
    mutate(spec["paths"][PATH]["get"]["responses"]["400"])
    with pytest.raises(TargetError):
        compile_policy(spec, manifest)


@pytest.mark.parametrize("value", [0, -1, True, "0", "1.5", "tomorrow", " 5", 999999])
def test_retry_after_must_be_positive_integer_seconds(value):
    spec, manifest = agreement()
    spec["paths"][PATH]["get"]["responses"]["429"]["headers"]["Retry-After"]["example"] = value
    with pytest.raises(TargetError):
        compile_policy(spec, manifest)


def test_arbitrary_example_text_is_encoded_data_never_executable():
    spec, manifest = agreement()
    body = spec["paths"][PATH]["get"]["responses"]["400"]["content"]["application/problem+json"]["example"]
    body["detail"] = body["message"] = '@{return "x";} {{not-a-named-value}} <base/> \n "quotes"'
    text = compile_policy(spec, manifest)
    assert "not-a-named-value" not in text
    rr = ET.fromstring(text).find("inbound/choose[3]/when/return-response")
    encoded = re.search(r'FromBase64String\("([^"]+)"\)', rr.find("set-body").text)[1]
    assert json.loads(base64.b64decode(encoded)) == body


def test_full_build_profile_and_handoff_are_consistent(tmp_path, monkeypatch):
    spec, manifest = agreement()
    client = write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    before = {p: p.read_bytes() for p in (client / "mcp-manifest.yaml", tmp_path / "apis/things/openapi.yaml")}
    outputs = bf.build_client(client, write=False)
    assert outputs == bf.build_client(client, write=False)
    assert all(path.read_bytes() == data for path, data in before.items())
    bicep = outputs[client / "generated/client.bicep"]
    assert "subscriptionKeyHeader: 'x-api-key'" in bicep
    assert "apiRateLimitNames: ['demo-things']" in bicep
    assert "param enableNativeMcp bool = false" in bicep
    assert "exposeMcp: enableNativeMcp && false" in bicep
    assert "'demo-things-mcp'" not in bicep
    assert dp.validate("rest-consumption", [client / "mcp-manifest.yaml"]) == []
    handoff = consumer_handoff(tmp_path, "demo", "rest-consumption")
    assert handoff["authentication"]["requiredHeaders"] == ["x-api-key"]
    assert handoff["restRuntime"][0]["correlationHeader"] == "X-Correlation-Id"
    assert handoff["status"] == "derived-not-verified"
    for profile in ("native-mcp", "policy-mcp-consumption"):
        with pytest.raises(SystemExit):
            dp.validate(profile, [client / "mcp-manifest.yaml"])
        with pytest.raises(ValueError, match="REST-only"):
            consumer_handoff(tmp_path, "demo", profile)
    with pytest.raises(policy.PolicyBuildError, match="REST-only"):
        policy.build_client_plan(tmp_path, client)


def test_direct_mcp_tool_compiler_cannot_ignore_runtime_extension():
    spec, _ = agreement()
    tool = policy.ToolDefinition("things", PATH, "get", spec["paths"][PATH]["get"], [], spec)
    with pytest.raises(policy.PolicyBuildError, match="REST-only"):
        policy.build_policy("demo", [tool])


def test_pydantic_schemas_forbid_unknown_runtime_fields():
    for model in (ContractRuntime, ApiRuntime):
        schema = model.model_json_schema()
        assert schema["additionalProperties"] is False
        for definition in schema["$defs"].values():
            if definition.get("type") == "object":
                assert definition["additionalProperties"] is False


def test_verifier_uses_header_and_dynamic_binding_without_changing_fixture(tmp_path, monkeypatch):
    spec, manifest = agreement()
    original = copy.deepcopy(spec)
    op = spec["paths"][PATH]["get"]
    op["parameters"][1]["example"] = CORRELATION.lower()
    client = write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(vr, "REPO_ROOT", tmp_path)
    cases = list(vr.iter_cases(manifest))
    assert len(cases) == 4
    assert [case[-1][3] for case in cases] == [200, 200, 200, 404]
    for _, _, _, _, case in cases:
        _, headers, _, status, _, expected = case
        assert expected["correlationId"] == CORRELATION.lower()
        response_headers = vr.runtime_expected_headers(spec, status, headers)
        assert response_headers["X-Correlation-Id"] == CORRELATION.lower()
        assert response_headers["Cache-Control"] == "no-store"
        auth = Credentials("fictional-value", header_name="x-api-key").headers(headers)
        assert auth["x-api-key"] == "fictional-value"
        assert "Ocp-Apim-Subscription-Key" not in auth
    assert op["responses"] == original["paths"][PATH]["get"]["responses"]
    with pytest.raises(VerificationFailure, match="override"):
        Credentials("fictional-value", header_name="x-api-key").headers({"X-API-KEY": "override"})
    manifest["apis"][0]["runtime"]["simulate"] = {"status": 429}
    simulated = list(vr.iter_cases(manifest))
    assert len(simulated) == 1
    assert simulated[0][-1][3] == 429
    assert vr.runtime_expected_headers(spec, 429, simulated[0][-1][1])["Retry-After"] == "5"


def test_runtime_policy_limit_is_measured_without_truncation():
    spec, manifest = agreement()
    examples = spec["paths"][PATH]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    examples["T-1"]["value"]["thingId"] = "x" * 16384
    with pytest.raises(TargetError, match=r"measures \d+ UTF-8 bytes.*16384"):
        compile_policy(spec, manifest)
    assert len(examples["T-1"]["value"]["thingId"]) == 16384


def test_representative_rest_policy_fits_consumption_without_dropping_fields():
    spec, manifest = agreement()
    op = spec["paths"][PATH]["get"]
    for status, response in op["responses"].items():
        media = next(iter(response["content"].values()))
        values = ([media["example"]] if "example" in media else
                  [item["value"] for item in media["examples"].values()])
        for value in values:
            target = 448 if status == "200" else 288
            padding = max(0, target - len(json.dumps(value, ensure_ascii=False).encode("utf-8")))
            value["thingId" if status == "200" else "message"] += "x" * padding
    expected = copy.deepcopy(spec)
    text = compile_policy(spec, manifest)
    assert 14000 < len(text.encode("utf-8")) <= 16384
    assert spec == expected
    bodies = re.findall(r'FromBase64String\("([^"]+)"\)', text)
    assert len(bodies) == 9
    # The 401 must work both before inbound and at its explicit header gate.
    assert len(bodies) - len(set(bodies)) == 1
    for encoded in bodies:
        payload = json.loads(base64.b64decode(encoded))
        if payload.get("status") is not None:
            assert set(payload) == {"type", "title", "status", "detail", "code", "message", "correlationId"}


def test_updated_module_signature_allows_only_unchanged_header_in_recovery(recovery):
    from test_deployment_recovery_payloads import contract_payload_failure
    from deployment_recovery_payloads import templates_match, PayloadMismatch
    contract_payload_failure(recovery)
    for template in (recovery.template, recovery.exported):
        props = template["resources"][-1]["properties"]
        props["template"]["parameters"]["subscriptionKeyHeader"] = {
            "type": "string", "defaultValue": "Ocp-Apim-Subscription-Key"}
        props["parameters"]["subscriptionKeyHeader"] = {"value": "x-api-key"}
    templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)
    recovery.exported["resources"][-1]["properties"]["parameters"]["subscriptionKeyHeader"] = {"value": "other-key"}
    with pytest.raises(PayloadMismatch):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)


def test_dynamic_response_code_is_not_accepted_as_recovery_payload_data():
    from deployment_recovery_payloads import _policy_shape, PayloadMismatch
    with pytest.raises(PayloadMismatch, match="executable"):
        _policy_shape(compile_policy())


def test_generated_runtime_bicep_compiles_offline_when_compiler_available(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from pathlib import Path
    compiler = shutil.which("bicep") or Path.home() / ".azure" / "bin" / "bicep.exe"
    if not Path(compiler).is_file():
        pytest.skip("standalone offline Bicep compiler is not installed")
    spec, manifest = agreement()
    client = write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    bf.build_client(client)
    result = subprocess.run(
        [str(compiler), "build", str(client / "generated/client.bicep"), "--stdout"],
        capture_output=True, text=True, timeout=90, check=True)
    template = json.loads(result.stdout)
    assert template["parameters"]["enableNativeMcp"]["defaultValue"] is False
    api = next(r for r in template["resources"] if r.get("name") == "api-demo-things")
    assert api["properties"]["parameters"]["subscriptionKeyHeader"]["value"] == "x-api-key"
    product = next(r for r in template["resources"] if r.get("name") == "product-demo")
    assert product["properties"]["parameters"]["apiRateLimitNames"]["value"] == ["demo-things"]


@pytest.mark.parametrize("bad_headers", [False, True])
def test_runtime_verifier_main_checks_transport_headers_offline(tmp_path, monkeypatch, capsys, bad_headers):
    import sys
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(vr, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("MCP_KEY", "fictional-value")
    monkeypatch.setattr(sys, "argv", ["verify-rest", "clients/demo", "--gateway-url", "https://gateway.example.test"])
    cases = iter(list(vr.iter_cases(manifest)))
    calls = []

    def invoke(url, method, headers, body, *, include_headers=False):
        assert include_headers
        assert headers["x-api-key"] == "fictional-value"
        case = next(cases)[-1]
        response_headers = vr.runtime_expected_headers(spec, case[3], headers)
        if bad_headers:
            response_headers["Cache-Control"] = "public"
        calls.append(url)
        return case[3], case[4], case[5], response_headers

    monkeypatch.setattr(vr, "invoke", invoke)
    if bad_headers:
        with pytest.raises(SystemExit):
            vr.main()
        assert "runtime response headers differ" in capsys.readouterr().out
    else:
        vr.main()
        assert "RESULT" in capsys.readouterr().out
    assert len(calls) == 4


@pytest.mark.parametrize("profile", ["native-mcp", "policy-mcp-consumption"])
def test_mcp_verifier_rejects_runtime_before_any_network(tmp_path, monkeypatch, capsys, profile):
    import sys
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(vm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["verify-mcp", "clients/demo",
                                     "--gateway-url", "https://gateway.example.test", "--profile", profile])
    monkeypatch.setattr(vm, "azd_env", lambda: pytest.fail("must not discover Azure"))
    monkeypatch.setattr(vm, "explicit_credentials", lambda *a: pytest.fail("must reject before credentials"))
    with pytest.raises(SystemExit):
        vm.main()
    assert "REST-only" in capsys.readouterr().err


def test_runtime_rejects_duplicate_scalar_values_before_lookup():
    spec, manifest = agreement()
    op = spec["paths"][PATH]["get"]
    op["parameters"].append({
        "name": "filter", "in": "query", "required": False, "schema": {"type": "string", "minLength": 1}})
    root = ET.fromstring(compile_policy(spec, manifest))
    for section in ("inbound", "on-error"):
        correlation = root.find(f"{section}/set-variable[@name='kitCorrelationValid']").get("value")
        assert 'Headers.ContainsKey("X-Correlation-Id")' in correlation
        assert 'Headers["X-Correlation-Id"].Length == 1' in correlation
        assert "\\\\z" in correlation
    auth = root.find("inbound/choose[1]/when").get("condition")
    assert 'Headers["x-api-key"].Length != 1' in auth
    validation = root.find("inbound/choose[3]/when").get("condition")
    assert 'Headers["X-Correlation-Id"].Length != 1' in validation
    assert 'Query["filter"].Length != 1' in validation
    assert 'Query.ContainsKey("filter")' in validation


@pytest.mark.parametrize("header", ["X-Correlation-Id", "Cache-Control"])
def test_response_header_names_are_unique_case_insensitively(header):
    spec, manifest = agreement()
    headers = spec["paths"][PATH]["get"]["responses"]["400"]["headers"]
    headers[header.lower()] = copy.deepcopy(headers[header])
    with pytest.raises(TargetError, match="duplicate.*header"):
        compile_policy(spec, manifest)


@pytest.mark.parametrize("location", ["header", "query", "path"])
def test_parameter_names_cannot_collide_by_case_or_location(location):
    spec, manifest = agreement()
    op = spec["paths"][PATH]["get"]
    op["parameters"].append({
        "name": "x-correlation-id", "in": location, "required": True,
        "schema": {"type": "string", "format": "uuid"}})
    with pytest.raises(TargetError, match="unique"):
        compile_policy(spec, manifest)


def test_generated_patterns_use_dotnet_absolute_end_anchor():
    import shutil
    import subprocess
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell/.NET is unavailable for this supplementary engine check")
    cases = []
    patterns = [
        (safe_pattern("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"),
         [("A", True), ("A" * 64, True), ("A" * 65, False),
          ("T-1\n", False), ("T-1\r\n", False), ("-bad", False)]),
        (safe_pattern(UUID_PATTERN),
         [(CORRELATION, True), (CORRELATION.lower(), True),
          ("00000000-0000-0000-0000-000000000000", True),
          (CORRELATION + "\n", False), (CORRELATION + "\r\n", False)])]
    for pattern, values in patterns:
        cases.extend({"pattern": pattern, "value": value, "expected": expected}
                     for value, expected in values)
    encoded = base64.b64encode(json.dumps(cases).encode()).decode()
    script = (
        f"$cases = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')) | ConvertFrom-Json; "
        "$actual = @($cases | ForEach-Object { [regex]::IsMatch($_.value, $_.pattern) }); "
        "ConvertTo-Json -InputObject $actual -Compress")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout) == [case["expected"] for case in cases]
