import copy
import pytest
import yaml

from deployment_recovery_payloads import PayloadMismatch, templates_match
from test_deployment_recovery import (
    assert_no_mutations, invoke, nested_failure, recovery,
)


def contract_payload_failure(harness, *, variables=False, changed_body=True):
    nested = nested_failure(harness)
    path = harness.root / "apis" / "things" / "openapi.yaml"
    current = yaml.safe_load(path.read_text(encoding="utf-8"))
    media = current["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"]["content"]["application/json"]
    media["schema"]["properties"]["size"].update(minimum=0, exclusiveMinimum=True)
    if changed_body:
        media["example"]["size"] = 4
    old = copy.deepcopy(current)
    old_media = old["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"]["content"]["application/json"]
    old_media["schema"]["properties"]["size"].pop("minimum")
    old_media["schema"]["properties"]["size"]["exclusiveMinimum"] = 0
    old_media["example"]["size"] = 3
    path.write_text(yaml.safe_dump(current), encoding="utf-8")
    current_spec, old_spec = yaml.safe_dump(current), yaml.safe_dump(old)
    policy = '<policies><inbound><base /><return-response><set-status code="200" reason="OK" /><set-body>{"size":%d}</set-body></return-response></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
    new_policy, old_policy = policy % (4 if changed_body else 3), policy % 3
    signature = {
        "apimName": {"type": "string"}, "clientId": {"type": "string"}, "apiName": {"type": "string"},
        "displayName": {"type": "string"}, "specValue": {"type": "string"}, "policyXml": {"type": "string"},
        "backendMode": {"type": "string"}, "backendUrl": {"type": "string", "defaultValue": ""},
        "toolOperations": {"type": "array"}, "exposeMcp": {"type": "bool", "defaultValue": True},
        "tagIds": {"type": "array", "defaultValue": []},
    }
    module = {
        "parameters": signature,
        "resources": [
            {"type": "Microsoft.ApiManagement/service/apis",
             "name": "[format('{0}/{1}-{2}', parameters('apimName'), parameters('clientId'), parameters('apiName'))]",
             "properties": {"format": "openapi", "value": "[parameters('specValue')]", "subscriptionRequired": True}},
            {"type": "Microsoft.ApiManagement/service/apis/policies", "name": "policy",
             "properties": {"format": "rawxml", "value": "[parameters('policyXml')]"}}],
        "metadata": {"_generator": {"name": "bicep", "version": "0.38.33", "templateHash": "333"}},
    }
    arguments = {
        "apimName": {"value": "[parameters('apimName')]"}, "clientId": {"value": "demo"},
        "apiName": {"value": "things"}, "displayName": {"value": "Things"},
        "specValue": {"value": current_spec}, "policyXml": {"value": new_policy},
        "backendMode": {"value": "mock"}, "backendUrl": {"value": ""},
        "toolOperations": {"value": ["get-thing"]}, "tagIds": {"value": ["demo", "demo-mock"]},
        "exposeMcp": {"value": "[and(parameters('enableNativeMcp'), false())]"},
    }
    resource = harness.template["resources"][-1]
    resource["properties"] = {"mode": "Incremental", "template": module, "parameters": arguments}
    harness.template["metadata"] = {"_generator": {"name": "bicep", "version": "0.38.33", "templateHash": "111"}}
    harness.exported = copy.deepcopy(harness.template)
    old_arguments = harness.exported["resources"][-1]["properties"]["parameters"]
    old_arguments["specValue"]["value"] = old_spec
    old_arguments["policyXml"]["value"] = old_policy
    harness.exported["metadata"]["_generator"]["templateHash"] = "222"
    nested["template"] = copy.deepcopy(module)
    actual_parameters = copy.deepcopy(old_arguments)
    actual_parameters["apimName"]["value"] = "fixture-apim"
    actual_parameters["exposeMcp"]["value"] = False
    nested["detail"]["properties"]["parameters"] = actual_parameters
    if variables:
        harness.template["variables"], harness.exported["variables"] = {}, {}
        for index, slot in enumerate(("specValue", "policyXml")):
            name = f"$fxv#{index}"
            harness.template["variables"][name] = arguments[slot]["value"]
            harness.exported["variables"][name] = old_arguments[slot]["value"]
            arguments[slot]["value"] = old_arguments[slot]["value"] = f"[variables('{name}')]"
    return nested


@pytest.mark.parametrize("variables", [False, True])
@pytest.mark.parametrize("changed_body", [False, True])
def test_corrected_exclusive_minimum_and_mock_data_retry_same_client(
        recovery, monkeypatch, capsys, variables, changed_body):
    contract_payload_failure(recovery, variables=variables, changed_body=changed_body)
    invoke(recovery, monkeypatch)
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    assert_no_mutations(recovery)
    invoke(recovery, monkeypatch, "--yes", "--review-token", token)
    creates = [call for call in recovery.calls if "create" in call]
    assert len(creates) == 1
    assert creates[0][creates[0].index("--name") + 1] == "client-demo"
    assert not any("DELETE" in call for call in recovery.calls)


@pytest.mark.parametrize("mutate", [
    lambda t: t["resources"][-1].update(name="other-module"),
    lambda t: t["resources"][-1].update(subscriptionId="other-subscription"),
    lambda t: t["resources"][-1].update(resourceGroup="other-group"),
    lambda t: t["resources"][-1]["properties"].update(mode="Complete"),
    lambda t: t["resources"][-1]["properties"]["template"]["resources"][0]["properties"].update(subscriptionRequired=False),
    lambda t: t["resources"][-1]["properties"]["template"]["parameters"].update(unknown={"type": "string"}),
    lambda t: t["resources"][-1]["properties"]["parameters"]["clientId"].update(value="foreign"),
    lambda t: t["resources"][-1]["properties"]["parameters"]["apiName"].update(value="foreign"),
    lambda t: t["resources"][-1]["properties"]["parameters"]["apimName"].update(value="foreign"),
    lambda t: t["resources"][-1]["properties"]["parameters"]["backendMode"].update(value="external"),
    lambda t: t["resources"][-1]["properties"]["parameters"]["backendUrl"].update(value="https://foreign.invalid"),
    lambda t: t["resources"][-1]["properties"]["parameters"]["tagIds"].update(value=["foreign"]),
    lambda t: t["resources"][-1]["properties"]["parameters"]["toolOperations"].update(value=["foreign"]),
    lambda t: t["resources"][-1]["properties"]["parameters"].update(inboundAuthMode={"value": "anonymous"}),
    lambda t: t["resources"][0]["properties"].update(displayName="foreign"),
    lambda t: t["resources"][0].update(name="fixture-apim/foreign"),
    lambda t: t["resources"].append({"type": "Microsoft.Authorization/roleAssignments", "name": "foreign"}),
    lambda t: t["metadata"]["_generator"].update(version="other"),
    lambda t: t["metadata"]["_generator"].update(name="other"),
    lambda t: t["resources"][-1]["properties"]["parameters"]["specValue"].update(value={}),
    lambda t: t["resources"][-1]["properties"]["parameters"]["policyXml"].update(value=[]),
    lambda t: t["resources"][-1]["properties"]["parameters"]["policyXml"].update(value="<malformed"),
])
def test_payload_correction_never_relaxes_resource_or_module_boundary(recovery, monkeypatch, mutate):
    contract_payload_failure(recovery)
    mutate(recovery.exported)
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("slot", ["specValue", "policyXml"])
def test_unknown_nested_parameters_are_not_wildcard_payload_slots(recovery, slot):
    contract_payload_failure(recovery)
    for template in (recovery.template, recovery.exported):
        template["resources"].append({
            "type": "Microsoft.Resources/deployments", "name": "unknown",
            "properties": {"template": {}, "parameters": {slot: {"value": "same"}}},
        })
    recovery.exported["resources"][-1]["properties"]["parameters"][slot]["value"] = "changed"
    with pytest.raises(PayloadMismatch):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)


@pytest.mark.parametrize("payload", [
    '<policies><inbound><send-request mode="new" /></inbound></policies>',
    '<policies><inbound><return-response><set-body>@{return context.Request.Body.As&lt;string&gt;();}</set-body></return-response></inbound></policies>',
    '<policies><inbound><return-response><set-body>{"secret":"{{secret-ref}}"}</set-body></return-response></inbound></policies>',
])
def test_policy_code_and_secret_references_are_not_response_data(recovery, monkeypatch, payload):
    contract_payload_failure(recovery)
    recovery.exported["resources"][-1]["properties"]["parameters"]["policyXml"]["value"] = payload
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("body", [
    '@{ return context.Request.Body.As<string>(); }',
    '{"secret":"{{secret-ref}}"}',
    '{"expression":"@(context.Subscription.Key)"}',
])
def test_unchanged_policy_structure_cannot_hide_executable_response_data(recovery, monkeypatch, body):
    contract_payload_failure(recovery)
    entry = recovery.exported["resources"][-1]["properties"]["parameters"]["policyXml"]
    entry["value"] = entry["value"].replace('{"size":3}', body.replace("<", "&lt;").replace(">", "&gt;"))
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("before,after", [
    ("<backend><base /></backend>", '<backend><set-backend-service base-url="https://foreign.invalid" /></backend>'),
    ("<inbound><base />", '<inbound><base /><validate-jwt header-name="Authorization" />'),
])
def test_policy_auth_and_backend_nodes_stay_exact(recovery, before, after):
    contract_payload_failure(recovery)
    entry = recovery.exported["resources"][-1]["properties"]["parameters"]["policyXml"]
    entry["value"] = entry["value"].replace(before, after)
    with pytest.raises(PayloadMismatch):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)


@pytest.mark.parametrize("field,value", [
    ("servers", [{"url": "https://foreign.invalid"}]),
    ("security", [{"foreignAuth": []}]),
])
def test_openapi_target_and_authentication_are_not_payload_corrections(recovery, monkeypatch, field, value):
    contract_payload_failure(recovery)
    entry = recovery.exported["resources"][-1]["properties"]["parameters"]["specValue"]
    spec = yaml.safe_load(entry["value"])
    spec[field] = value
    entry["value"] = yaml.safe_dump(spec)
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("field,value", [
    ("operationId", "foreign-operation"),
    ("x-mock", [{"respond": {"status": 404}}]),
    ("security", [{"foreign": []}]),
])
def test_openapi_operation_identity_and_mock_rules_remain_exact(recovery, field, value):
    contract_payload_failure(recovery)
    entry = recovery.exported["resources"][-1]["properties"]["parameters"]["specValue"]
    spec = yaml.safe_load(entry["value"])
    spec["paths"]["/v1/things/{thingId}"]["get"][field] = value
    entry["value"] = yaml.safe_dump(spec)
    with pytest.raises(PayloadMismatch):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)


def test_file_payload_variable_cannot_also_control_scope(recovery):
    contract_payload_failure(recovery, variables=True)
    for template in (recovery.template, recovery.exported):
        template["resources"][-1]["subscriptionId"] = "[variables('$fxv#0')]"
    with pytest.raises(PayloadMismatch, match="non-payload"):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)


def test_nested_history_payload_must_match_its_own_parent_attempt(recovery, monkeypatch):
    nested = contract_payload_failure(recovery)
    nested["detail"]["properties"]["parameters"]["specValue"]["value"] = "a different historic attempt"
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


def test_changed_historical_response_data_stales_review_token(recovery, monkeypatch, capsys):
    nested = contract_payload_failure(recovery)
    invoke(recovery, monkeypatch)
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    entry = recovery.exported["resources"][-1]["properties"]["parameters"]["policyXml"]
    entry["value"] = entry["value"].replace('{"size":3}', '{"size":2}')
    nested["detail"]["properties"]["parameters"]["policyXml"]["value"] = entry["value"]
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch, "--yes", "--review-token", token)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("backend", [
    None, {}, {"mode": "external"}, {"mode": "mock", "outboundAuth": {}},
    {"mode": "mock", "outboundAuth": {"secretRef": "demo-key"}},
    {"mode": "mock", "secretRef": "demo-key"},
])
def test_payload_exception_requires_explicit_credential_free_mock_manifest(recovery, backend):
    contract_payload_failure(recovery)
    recovery.manifest["apis"][0]["backend"] = backend
    with pytest.raises(PayloadMismatch, match="credential-free mock"):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)
    # The bounded exception adds no new restrictions to exact template comparison.
    templates_match(recovery.template, copy.deepcopy(recovery.template), recovery.manifest, root=True)


@pytest.mark.parametrize("field", ["templateHash", "name", "version"])
def test_missing_exported_compiler_metadata_is_not_broadly_ignored(recovery, field):
    contract_payload_failure(recovery)
    recovery.exported["metadata"]["_generator"].pop(field)
    with pytest.raises(PayloadMismatch):
        templates_match(recovery.template, recovery.exported, recovery.manifest, root=True)
