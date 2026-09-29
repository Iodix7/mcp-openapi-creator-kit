"""Synthetic stateless order-like CRUD contract; no Azure calls or persistence."""
import copy
import base64
import json
import re
from xml.etree import ElementTree as ET

import pytest

from mcp_openapi_creator_kit.rest_inputs import body_schema, condition_expression, condition_matches
from mcp_openapi_creator_kit.rest_runtime import TargetError, build_runtime_policy, contract_runtime
from mcp_openapi_creator_kit.rest_runtime_v2 import build_operation_policies
from test_rest_runtime import CORRELATION, PATH, agreement, bf, write_agreement


def agreement_v2():
    spec, manifest = agreement()
    spec["x-kit-runtime"]["version"] = 2
    lookup = spec["paths"][PATH]["get"]
    schema = {"type": "object", "required": ["customer", "lines", "urgent"],
              "additionalProperties": False, "properties": {
                  "customer": {"type": "object", "required": ["id"], "additionalProperties": False,
                               "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}}},
                  "lines": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
                      "type": "object", "required": ["sku", "quantity", "price"], "additionalProperties": False,
                      "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1},
                                     "price": {"type": "number", "minimum": 0, "maximum": 10000}}}},
                  "urgent": {"type": "boolean"}}}
    for method, path in (("post", "/v1/things"), ("put", "/v1/things/{thingId}"),
                         ("patch", "/v1/things/{thingId}"), ("delete", "/v1/things/{thingId}")):
        op = copy.deepcopy(lookup)
        op["operationId"] = f"{method}-thing"
        if method == "post":
            op["parameters"] = [p for p in op["parameters"] if p["in"] != "path"]
        op["parameters"].append({"name": "Idempotency-Key", "in": "header", "required": True,
                                  "description": "Write retry token; this stateless demo does not deduplicate requests.",
                                  "schema": {"type": "string"}, "example": CORRELATION})
        op["x-mock"] = [{"respond": {"status": 200, "example": "T-1"}}]
        if method != "delete":
            op["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": copy.deepcopy(schema), "example": {
                    "customer": {"id": "C-1"}, "lines": [{"sku": "A-1", "quantity": 2, "price": 9.5}],
                    "urgent": False}}}}
        spec["paths"].setdefault(path, {})[method] = op
    manifest["apis"][0]["mcpTools"] = [op["operationId"] for _, _, op in bf.iter_operations(spec)]
    return spec, manifest


def compile_v2(spec=None, manifest=None):
    original, config = agreement_v2()
    return build_runtime_policy(spec or original, (manifest or config)["apis"][0], manifest or config, bf)


def test_multi_operation_compile_and_deterministic_budget():
    spec, manifest = agreement_v2()
    result = contract_runtime(spec, manifest["apis"][0], manifest, bf)
    assert len(result) == 5
    text = compile_v2(spec, manifest)
    assert text == compile_v2(spec, manifest)
    assert len(text.encode("utf-8")) <= 16384
    root = ET.fromstring(text)
    assert len(root.findall(".//rate-limit")) == 1
    content = root.find(".//validate-content")
    assert content.get("max-size") == "102400"
    assert content.find("content").get("action") == "prevent"
    assert content.find("content").get("schema-id") is None  # Imported operation schema, not a copied schema.
    assert "LastError.Message" not in text
    assert 'kit-runtime-body' in text
    assert "Guid.NewGuid()" in text
    response = root.find(".//set-variable[@name='kitResponse']").get("value")
    assert 'if(r["body"].Type == JTokenType.Integer){' in response


def test_real_generator_v2(tmp_path, monkeypatch):
    spec, manifest = agreement_v2()
    client = write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    outputs = bf.build_client(client, write=False)
    assert client / "generated" / "api-things.policy.xml" in outputs
    policies = [path for path in outputs if ".operation-" in path.name]
    assert len(policies) == 5
    assert "resource operationPolicies_api_things " in outputs[client / "generated" / "client.bicep"]
    assert all(len(outputs[path].encode("utf-8")) <= 16384 for path in policies)
    assert not (client / "generated").exists()


def test_operation_overrides_are_explicit_and_rate_limits_are_additional():
    spec, manifest = agreement_v2()
    manifest["apis"][0]["runtime"]["operations"] = {
        "post-thing": {"rateLimit": {"calls": 3, "renewalPeriod": 60}, "simulate": {"status": 503}}}
    tree = ET.fromstring(compile_v2(spec, manifest))
    rate = tree.find(".//rate-limit")
    assert rate.get("calls") == "60"
    assert not list(rate)
    policies = build_operation_policies(spec, manifest["apis"][0], manifest, bf)
    operation = ET.fromstring(policies["post-thing"])
    assert operation.find("inbound")[0].tag == "base"
    assert operation.find(".//rate-limit").attrib == {
        "id": "kit-runtime-rate", "calls": "3", "renewal-period": "60",
        "retry-after-variable-name": "kitRetryAfter"}
    assert operation.find("on-error/base") is not None
    assert len(operation.findall(".//rate-limit")) == 1
    assert tree.find(".//set-variable[@name='kitReady']") is not None
    assert all(ET.fromstring(text).find(".//rate-limit") is None
               for oid, text in policies.items() if oid != "post-thing")
    pool = tree.find(".//set-variable[@name='kitResponses']").get("value")
    records = json.loads(base64.b64decode(re.search(r'FromBase64String\("([^"]+)"\)', pool)[1]))
    assert any(record["status"] == 503 for record in records)


def test_removed_operation_limit_replaces_policy_with_inheritance(tmp_path, monkeypatch):
    spec, manifest = agreement_v2()
    manifest["apis"][0]["runtime"]["operations"] = {
        "post-thing": {"rateLimit": {"calls": 3, "renewalPeriod": 10}}}
    client = write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    bf.build_client(client)
    path = client / "generated" / "api-things.operation-post-thing.policy.xml"
    assert ET.fromstring(path.read_text("utf-8")).find(".//rate-limit") is not None
    manifest["apis"][0]["runtime"].pop("operations")
    import yaml
    (client / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest), "utf-8")
    bf.build_client(client)
    root = ET.fromstring(path.read_text("utf-8"))
    assert all([child.tag for child in section] == ["base"] for section in root)
    assert "kitReady" not in (client / "generated" / "api-things.policy.xml").read_text("utf-8")


def test_review_inventory_requires_exact_v2_operation_policies(tmp_path):
    from deployment import summarize_what_if, template_inventory
    from lifecycle import ReconcileError
    from types import SimpleNamespace
    spec, manifest = agreement_v2()
    manifest["apis"][0]["mcpTools"] = ["post-thing"]
    client = write_agreement(tmp_path, spec, manifest)
    base = "/subscriptions/fixture/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/test"
    _, allowed, required = template_inventory(
        SimpleNamespace(base=base), manifest, "rest-consumption", client)[0]
    expected = {f"{base}/apis/demo-things/operations/{op['operationId']}/policies/policy"
                for _, _, op in bf.iter_operations(spec)}
    assert len(expected) == 5 and expected <= required <= allowed
    with pytest.raises(ReconcileError):
        summarize_what_if({"changes": [{"resourceId": f"{base}/apis/demo-things/operations/foreign/policies/policy",
                                       "changeType": "Create"}]}, allowed, set())


def test_v2_operation_resources_compile_offline(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from pathlib import Path
    compiler = shutil.which("bicep") or Path.home() / ".azure" / "bin" / "bicep.exe"
    if not Path(compiler).is_file():
        pytest.skip("Existing offline Bicep compiler required")
    spec, manifest = agreement_v2()
    manifest["apis"][0]["runtime"]["operations"] = {
        "post-thing": {"rateLimit": {"calls": 3, "renewalPeriod": 10}}}
    client = write_agreement(tmp_path, spec, manifest)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    bf.build_client(client)
    result = subprocess.run([str(compiler), "build", str(client / "generated" / "client.bicep"), "--stdout"],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    template = json.loads(result.stdout)
    api_module = next(resource for resource in template["resources"] if resource["name"] == "api-demo-things")
    assert "operationPolicies" not in api_module["properties"]["parameters"]
    assert len(template["variables"]["operationPolicies_api_things_values"]) == 5
    operation = next(resource for resource in template["resources"] if resource["type"].endswith("/operations/policies"))
    assert "dependsOn" in operation and "copy" in operation
    product = next(resource for resource in template["resources"] if resource["name"] == "product-demo")
    assert "operationPolicies_api_things" in product["dependsOn"]


@pytest.mark.parametrize("mutate", [
    lambda s, m: s["x-kit-runtime"].update(operations={"typo": {}}),
    lambda s, m: m["apis"][0]["runtime"].update(operations={"typo": {}}),
    lambda s, m: s["paths"][PATH].update(head=copy.deepcopy(s["paths"][PATH]["get"])),
    lambda s, m: s["paths"]["/v1/things"]["post"]["requestBody"]["content"]["application/json"]["schema"].update(
        oneOf=[{"type": "string"}]),
    lambda s, m: s["paths"]["/v1/things"]["post"]["requestBody"]["content"]["application/json"]["schema"].update(nullable=True),
])
def test_unsupported_runtime_is_not_silently_ignored(mutate):
    spec, manifest = agreement_v2()
    mutate(spec, manifest)
    with pytest.raises((TargetError, SystemExit)):
        compile_v2(spec, manifest)


def test_v1_still_rejects_multi_operation_and_overrides():
    spec, manifest = agreement_v2()
    spec["x-kit-runtime"]["version"] = 1
    with pytest.raises(TargetError, match="exactly one GET"):
        compile_v2(spec, manifest)
    spec, manifest = agreement()
    manifest["apis"][0]["runtime"]["operations"] = {}
    with pytest.raises(TargetError, match="unsupported"):
        compile_v2(spec, manifest)


def test_composite_typed_body_rules_and_case_sensitive_pointer():
    spec, _ = agreement_v2()
    op = spec["paths"]["/v1/things"]["post"]
    rule = {"all": [
        {"body": "/customer/id", "equals": "C-1", "caseSensitive": True},
        {"any": [{"body": "/urgent", "equals": True}, {"body": "/lines/0/quantity", "equals": 2}]},
        {"param": "Idempotency-Key", "startsWith": "0011"}]}
    expression = condition_expression(spec, "/v1/things", op, rule, bf)
    assert "Ordinal" in expression and "&&" in expression and "||" in expression
    body = op["requestBody"]["content"]["application/json"]["example"]
    assert condition_matches(rule, {"Idempotency-Key": CORRELATION}, body)
    wrong = copy.deepcopy(body)
    wrong["customer"]["id"] = "c-1"
    assert not condition_matches(rule, {"Idempotency-Key": CORRELATION}, wrong)
    wrong["customer"]["id"] = "C-1"
    wrong["lines"][0]["quantity"] = 1
    assert not condition_matches(rule, {"Idempotency-Key": CORRELATION}, wrong)
    wrong["urgent"] = True
    assert condition_matches(rule, {"Idempotency-Key": CORRELATION}, wrong)


@pytest.mark.parametrize("condition", [
    {"all": []}, {"any": [{"body": "/urgent", "equals": True}]},
    {"body": "/unknown", "equals": "x"}, {"body": "/lines/01/sku", "equals": "A"},
    {"body": "/urgent", "equals": "true"}, {"body": "/urgent", "equals": True, "caseSensitive": False},
    {"body": "/lines/0/quantity", "equals": True}, {"body": "/customer/id", "equals": "{{secret}}"},
    {"body": "$.customer.id", "equals": "x"}, {"body": "/customer/id", "missing": False},
])
def test_invalid_conditions_fail_at_build_time(condition):
    spec, _ = agreement_v2()
    with pytest.raises((TargetError, SystemExit)):
        condition_expression(spec, "/v1/things", spec["paths"]["/v1/things"]["post"], condition, bf)


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(urgent="false"), lambda b: b.update(urgent=None),
    lambda b: b.update(lines=[]), lambda b: b["lines"][0].update(quantity=True),
    lambda b: b["lines"][0].update(quantity=0), lambda b: b["lines"][0].update(price=-1),
    lambda b: b["customer"].update(id=""), lambda b: b.update(unknown=1),
    lambda b: b["lines"][0].update(extra="not allowed"), lambda b: b.pop("customer"),
])
def test_nested_input_acceptance_matches_declared_schema(mutate):
    from jsonschema import Draft4Validator
    spec, _ = agreement_v2()
    op = spec["paths"]["/v1/things"]["post"]
    validator = Draft4Validator(body_schema(spec, op))
    value = copy.deepcopy(op["requestBody"]["content"]["application/json"]["example"])
    assert validator.is_valid(value)
    mutate(value)
    assert not validator.is_valid(value)


def test_verifier_uses_composite_body_witnesses_and_operation_headers():
    from test_rest_runtime import vr
    spec, _ = agreement_v2()
    op = spec["paths"]["/v1/things"]["post"]
    rule = {"all": [{"body": "/urgent", "equals": True},
                    {"body": "/lines/0/quantity", "equals": 3}]}
    op["x-mock"] = [
        {"when": rule, "respond": {"status": 200, "example": "T-2"}},
        {"respond": {"status": 200, "example": "T-1"}}]
    for index, selected in enumerate(op["x-mock"]):
        route, headers, body, status, media, payload = vr.build_case(
            spec, "/v1/things", "post", op, selected, op["x-mock"][:index])
        assert route == "/v1/things"
        assert condition_matches(rule, headers, body) is (index == 0)
        assert payload["correlationId"] == headers["X-Correlation-Id"]
        assert vr.runtime_expected_headers(spec, status, headers, "post-thing")["Cache-Control"] == "no-store"


def test_operation_correlation_override_and_verifier():
    from test_rest_runtime import vr
    spec, manifest = agreement_v2()
    # Keep a small, independent two-operation fixture so this tests semantics, not a size bypass.
    spec["paths"].pop("/v1/things/{thingId}")
    manifest["apis"][0]["mcpTools"] = ["get-thing-status", "post-thing"]
    spec["x-kit-runtime"]["operations"] = {
        "post-thing": {"correlation": {"header": "X-Trace-Id", "bodyProperty": "correlationId"}}}
    op = spec["paths"]["/v1/things"]["post"]
    next(p for p in op["parameters"] if p["name"] == "X-Correlation-Id")["name"] = "X-Trace-Id"
    for response in op["responses"].values():
        response["headers"]["X-Trace-Id"] = response["headers"].pop("X-Correlation-Id")
    text = compile_v2(spec, manifest)
    tree = ET.fromstring(text)
    initialization = tree.find(".//set-variable[@name='kitOperation']").get("value")
    assert "}.Contains(context.Operation.Id)){return " in initialization
    assert "}.Contains(context.Operation.Id))return " not in initialization
    metadata = [json.loads(base64.b64decode(value)) for value in
                re.findall(r'FromBase64String\("([^"]+)"\)', initialization)]
    assert {value["header"] for value in metadata} == {"X-Trace-Id", "X-Correlation-Id"}
    assert len(text.encode("utf-8")) <= 16384
    assert any(h.get("name") == '@((string)context.Variables["kitCorrelationHeader"])'
               for h in tree.findall(".//set-header"))
    _, headers, _, status, _, expected = vr.build_case(spec, "/v1/things", "post", op, op["x-mock"][0], [])
    assert expected["correlationId"] == headers["X-Trace-Id"]
    assert "X-Trace-Id" in vr.runtime_expected_headers(spec, status, headers, "post-thing")


def test_composite_policy_compiles_and_oversized_policy_is_never_truncated():
    spec, manifest = agreement_v2()
    spec["paths"].pop("/v1/things/{thingId}")
    manifest["apis"][0]["mcpTools"] = ["get-thing-status", "post-thing"]
    op = spec["paths"]["/v1/things"]["post"]
    op["x-mock"].insert(0, {"when": {"all": [
        {"body": "/urgent", "equals": True}, {"body": "/customer/id", "equals": "C-1"}]},
        "respond": {"status": 200, "example": "T-2"}})
    assert len(compile_v2(spec, manifest).encode("utf-8")) <= 16384
    before = copy.deepcopy(spec)
    op["responses"]["200"]["content"]["application/json"]["examples"]["T-2"]["value"]["thingId"] = "X" * 20000
    large = copy.deepcopy(spec)
    with pytest.raises(TargetError, match="16384"):
        compile_v2(spec, manifest)
    assert spec == large
    assert before != spec
