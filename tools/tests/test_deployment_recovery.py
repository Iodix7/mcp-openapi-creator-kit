"""Recovery authorization uses mocked live ARM evidence, never a local receipt."""
import copy
import json
import sys

import pytest

from test_deployment import DeploymentHarness, SUB, dc
from deployment_recovery import EvidenceReader
from lifecycle import API_VERSION, ReconcileError


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    harness = DeploymentHarness(tmp_path, monkeypatch)
    base = harness.client.base
    group = base.split("/providers/", 1)[0]
    harness.history_base = group + "/providers/Microsoft.Resources/deployments"
    harness.history_id = harness.history_base + "/client-demo"
    harness.template = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"apimName": {"type": "string"},
                       "keyVaultName": {"type": "string", "defaultValue": ""},
                       "enableNativeMcp": {"type": "bool", "defaultValue": True}},
        "resources": [
            {"type": "Microsoft.ApiManagement/service/tags", "name": "fixture-apim/" + name,
             "properties": {"displayName": name}} for name in ("demo", "demo-mock")
        ],
    }
    harness.exported = copy.deepcopy(harness.template)
    harness.inventory["/tags"] = [
        {"name": name, "id": base + "/tags/" + name, "properties": {"displayName": name}}
        for name in ("demo", "demo-mock")
    ]
    harness.detail = {
        "id": harness.history_id, "name": "client-demo",
        "properties": {
            "provisioningState": "Failed", "mode": "Incremental",
            "timestamp": "2026-09-18T12:00:00Z", "correlationId": "test-correlation",
            "parameters": {"apimName": {"value": "fixture-apim"}, "keyVaultName": {"value": ""},
                           "enableNativeMcp": {"value": True}},
        },
    }
    harness.operations = [
        {"operationId": str(index), "properties": {
            "provisioningOperation": "Create", "provisioningState": "Succeeded",
            "statusCode": "Created", "targetResource": {"id": base + "/tags/" + name},
        }} for index, name in enumerate(("demo", "demo-mock"))
    ]
    harness.deployment_records = [{"name": "client-demo"}]
    harness.live_histories = [{"name": "client-demo", "id": harness.history_id}]
    harness.pages = {}
    harness.detail_reads = 0
    harness.before_detail = None
    harness.nested = {}

    def run(args, capture=False):
        if args[:3] == ["az", "bicep", "build"]:
            harness.calls.append(args)
            assert "--stdout" in args
            return json.dumps(harness.template)
        if args[:4] == ["az", "deployment", "group", "export"]:
            harness.calls.append(args)
            assert args[args.index("--subscription") + 1] == SUB
            name = args[args.index("--name") + 1]
            if name in harness.nested:
                return json.dumps(harness.nested[name]["template"])
            return json.dumps(harness.exported)
        if args[:2] == ["az", "rest"]:
            uri = args[args.index("--uri") + 1]
            path = uri.split("?", 1)[0]
            if uri in harness.pages:
                harness.calls.append(args)
                return json.dumps(harness.pages[uri])
            if path.startswith(harness.history_base):
                harness.calls.append(args)
                assert args[args.index("--subscription") + 1] == SUB
                assert args[args.index("--method") + 1] == "GET"
                if path == harness.history_base:
                    return json.dumps({"value": harness.live_histories})
                if path == harness.history_id + "/operations":
                    return json.dumps({"value": harness.operations})
                if path == harness.history_id:
                    harness.detail_reads += 1
                    if harness.before_detail:
                        harness.before_detail(harness.detail_reads)
                    return json.dumps(harness.detail)
                name = path.removeprefix(harness.history_base + "/").split("/")[0]
                if name in harness.nested:
                    nested = harness.nested[name]
                    return json.dumps({"value": nested["operations"]} if path.endswith("/operations") else nested["detail"])
                pytest.fail("Unexpected deployment evidence read")
        return harness.run(args, capture=capture)

    monkeypatch.setattr(dc, "run", run)
    harness.recovery_run = run
    return harness


def invoke(harness, monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", harness.arguments("--recover-detached-tags", *extra))
    dc.main()


def assert_no_mutations(harness):
    assert not any("create" in call or "DELETE" in call or "PUT" in call for call in harness.calls)


@pytest.mark.parametrize("profile", ["native-mcp", "rest-consumption", "policy-mcp-consumption"])
def test_interrupted_mock_import_preview_and_same_client_retry(recovery, monkeypatch, capsys, profile):
    recovery.profile = profile
    if profile != "native-mcp":
        recovery.gateway["sku"]["name"] = "Consumption"
        recovery.detail["properties"]["parameters"]["enableNativeMcp"]["value"] = False
    invoke(recovery, monkeypatch)
    output = capsys.readouterr().out
    token = output.split("Review token: ")[1].splitlines()[0]
    assert "Detached-tag recovery" in output
    assert "reuse " + recovery.client.base + "/tags/demo" in output
    assert_no_mutations(recovery)
    invoke(recovery, monkeypatch, "--yes", "--review-token", token)
    assert recovery.detail_reads == 3  # preview, apply preview, immediate revalidation
    creates = [call for call in recovery.calls if "create" in call]
    assert len(creates) == (2 if profile == "policy-mcp-consumption" else 1)
    assert creates[0][creates[0].index("--name") + 1] == "client-demo"
    assert not any("DELETE" in call for call in recovery.calls)


def nested_failure(harness):
    name = "api-demo-things"
    rid = harness.history_base + "/" + name
    template = {"parameters": {"apimName": {"type": "string"}, "clientTag": {"type": "string"}},
                "resources": []}
    harness.template["resources"].append({
        "type": "Microsoft.Resources/deployments", "name": name,
        "properties": {"template": template, "parameters": {
            "apimName": {"value": "[parameters('apimName')]"}, "clientTag": {"value": "demo"},
        }},
    })
    harness.exported = copy.deepcopy(harness.template)
    detail = copy.deepcopy(harness.detail)
    detail["id"], detail["name"] = rid, name
    detail["properties"]["parameters"] = {"apimName": {"value": "fixture-apim"}, "clientTag": {"value": "demo"}}
    harness.nested[name] = {"template": copy.deepcopy(template), "detail": detail, "operations": [
        {"operationId": "import", "properties": {
            "provisioningOperation": "Create", "provisioningState": "Failed", "statusCode": "BadRequest",
            "targetResource": {"id": harness.client.base + "/apis/demo-things"},
        }},
    ]}
    harness.live_histories.append({"id": rid, "name": name})
    harness.deployment_records.append({"name": name})
    harness.operations.append({"operationId": "nested", "properties": {
        "provisioningOperation": "Create", "provisioningState": "Failed", "statusCode": "BadRequest",
        "targetResource": {"id": rid},
    }})
    return harness.nested[name]


def test_linked_nested_failed_api_import_retry(recovery, monkeypatch, capsys):
    nested_failure(recovery)
    invoke(recovery, monkeypatch)
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    invoke(recovery, monkeypatch, "--yes", "--review-token", token)
    assert len([call for call in recovery.calls if "create" in call]) == 1
    assert not any("DELETE" in call for call in recovery.calls)


@pytest.mark.parametrize("mutation", [
    lambda nested: nested["template"].update(contentVersion="foreign"),
    lambda nested: nested["detail"]["properties"]["parameters"]["clientTag"].update(value="foreign"),
    lambda nested: nested["detail"]["properties"].update(provisioningState="Running"),
    lambda nested: nested["operations"][0]["properties"].update(provisioningState="Succeeded"),
    lambda nested: nested["operations"][0]["properties"]["targetResource"].update(id="/foreign"),
])
def test_nested_history_must_prove_exact_failed_import(recovery, monkeypatch, mutation):
    nested = nested_failure(recovery)
    mutation(nested)
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


def test_recovery_flag_never_bypasses_review(recovery, monkeypatch):
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch, "--yes")
    assert_no_mutations(recovery)


@pytest.mark.parametrize("status", ["OK", "200", 200, "Accepted", None])
def test_preexisting_put_is_not_creation_evidence(recovery, monkeypatch, capsys, status):
    recovery.operations[0]["properties"]["statusCode"] = status
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert "creation is unproven" in capsys.readouterr().err
    assert_no_mutations(recovery)


@pytest.mark.parametrize("mutation", [
    lambda h: h.operations.pop(),
    lambda h: h.operations.append(copy.deepcopy(h.operations[0])),
    lambda h: h.operations[0]["properties"].update(provisioningState="Running"),
    lambda h: h.operations[0]["properties"].update(provisioningOperation="Delete"),
    lambda h: h.operations[0]["properties"]["targetResource"].update(id=h.client.base + "-foreign/tags/demo"),
    lambda h: h.detail["properties"].update(provisioningState="Succeeded"),
    lambda h: h.detail["properties"].update(provisioningState="Running"),
    lambda h: h.detail["properties"].update(mode="Complete"),
    lambda h: h.detail["properties"].pop("correlationId"),
    lambda h: h.detail.update(id=h.history_id + "-foreign"),
    lambda h: h.detail["properties"]["parameters"]["apimName"].update(value="foreign"),
    lambda h: h.detail["properties"]["parameters"]["enableNativeMcp"].update(value=False),
    lambda h: h.exported.update(contentVersion="other"),
    lambda h: h.live_histories.clear(),
    lambda h: h.live_histories.append({"name": "product-demo", "id": h.history_base + "/product-demo"}),
    lambda h: h.inventory["/tags"][0]["properties"].update(displayName="foreign"),
    lambda h: h.inventory["/tags"][0].update(id=h.client.base + "-foreign/tags/demo"),
    lambda h: h.inventory["/tags"].append({"name": "demo-foreign", "properties": {"displayName": "demo-foreign"}}),
    lambda h: h.inventory.update({"/apis": [{"name": "demo-things"}]}),
    lambda h: h.inventory.update({"/apis": [{"name": "demo-old"}], "/apis/demo-old/tags": [{"name": "demo"}]}),
    lambda h: h.inventory.update({"/products": [{"name": "demo-product"}]}),
    lambda h: h.inventory.update({"/subscriptions": [{"name": "demo-pilot"}]}),
    lambda h: h.inventory.update({"/namedValues": [{"name": "demo-secret"}]}),
    lambda h: h.inventory.update({"/backends": [{"name": "demo-backend"}]}),
    lambda h: h.inventory.update({"/subscriptions": [{"name": "foreign", "properties": {
        "scope": h.client.base + "/products/demo-product"}}]}),
])
def test_uncertain_foreign_mixed_or_changed_evidence_is_closed(recovery, monkeypatch, mutation):
    mutation(recovery)
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("kind", ["api", "operation", "product"])
def test_shared_tag_even_without_owned_anchor_is_refused(recovery, monkeypatch, kind):
    recovery.inventory["/tagResources"] = [
        {"tag": {"id": "/tags/demo", "name": "demo"}, kind: {"id": "/apis/foreign"}}
    ]
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


def test_complete_paginated_operations_are_required(recovery, monkeypatch, capsys):
    first = recovery.history_id + "/operations?api-version=2022-09-01"
    second = recovery.history_id + "/operations?api-version=2022-09-01&$skiptoken=second"
    recovery.pages[first] = {"value": recovery.operations[:1], "nextLink": second}
    recovery.pages[second] = {"value": recovery.operations[1:]}
    invoke(recovery, monkeypatch)
    assert "Review token:" in capsys.readouterr().out
    assert any(second in call for call in recovery.calls)
    assert_no_mutations(recovery)


def test_foreign_association_on_later_page_blocks(recovery, monkeypatch):
    first = recovery.client.base + "/tagResources?api-version=" + API_VERSION
    second = first + "&$skiptoken=second"
    recovery.pages[first] = {"value": [], "nextLink": second}
    recovery.pages[second] = {"value": [{"tag": {"id": "/tags/demo"}, "product": {"id": "/products/foreign"}}]}
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("next_link", [
    "https://evil.invalid/history",
    "/subscriptions/other/resourceGroups/fixture/providers/Microsoft.Resources/deployments",
    False, 9, "",
])
def test_evidence_pagination_never_leaves_exact_context(recovery, next_link):
    uri = recovery.history_base + "?api-version=2022-09-01"
    recovery.pages[uri] = {"value": [], "nextLink": next_link}
    with pytest.raises(ReconcileError):
        EvidenceReader(recovery.context, recovery.recovery_run).paged(recovery.history_base)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("page", [
    {"value": [], "count": 1},
    {"value": [], "count": False},
    {"value": {}},
    {"value": [None]},
])
def test_incomplete_evidence_pages_are_refused(recovery, page):
    uri = recovery.history_base + "?api-version=2022-09-01"
    recovery.pages[uri] = page
    with pytest.raises(ReconcileError):
        EvidenceReader(recovery.context, recovery.recovery_run).paged(recovery.history_base)


def test_history_pagination_cycle_is_refused(recovery):
    uri = recovery.history_base + "?api-version=2022-09-01"
    recovery.pages[uri] = {"value": [], "nextLink": uri}
    with pytest.raises(ReconcileError, match="cyclic"):
        EvidenceReader(recovery.context, recovery.recovery_run).paged(recovery.history_base)


def test_unlinked_history_on_later_page_is_not_adopted(recovery, monkeypatch):
    first = recovery.history_base + "?api-version=2022-09-01"
    second = first + "&$skiptoken=second"
    recovery.pages[first] = {"value": recovery.live_histories, "nextLink": second}
    recovery.pages[second] = {"value": [{"name": "product-demo", "id": recovery.history_base + "/product-demo"}]}
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("change", ["history", "operations", "template"])
def test_changed_live_proof_invalidates_review(recovery, monkeypatch, capsys, change):
    invoke(recovery, monkeypatch)
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]
    if change == "history":
        recovery.detail["properties"]["correlationId"] = "different-attempt"
    elif change == "operations":
        recovery.operations[0]["properties"]["timestamp"] = "changed"
    else:
        recovery.exported["contentVersion"] = "changed"
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch, "--yes", "--review-token", token)
    assert_no_mutations(recovery)


def test_proof_revalidated_immediately_before_mutation(recovery, monkeypatch, capsys):
    invoke(recovery, monkeypatch)
    token = capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]

    def change_on_final_read(count):
        if count == 3:
            recovery.detail["properties"]["correlationId"] = "different-attempt"
    recovery.before_detail = change_on_final_read
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch, "--yes", "--review-token", token)
    assert_no_mutations(recovery)


@pytest.mark.parametrize("field", ["id", "tenantId"])
def test_recovery_context_mismatch_precedes_evidence(recovery, monkeypatch, field):
    recovery.account[field] = "00000000-0000-0000-0000-000000000009"
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch, "--yes")
    assert not any("rest" in call or "deployment" in call for call in recovery.calls)
    assert_no_mutations(recovery)


def test_no_receipt_or_arbitrary_history_authorization(recovery, monkeypatch):
    recovery.live_histories.clear()
    local = recovery.root / ".mcp-kit" / "workflow" / "demo"
    local.mkdir(parents=True)
    (local / "preview.json").write_text(json.dumps({"approved": True, "tags": ["demo", "demo-mock"]}))
    with pytest.raises(SystemExit):
        invoke(recovery, monkeypatch)
    assert_no_mutations(recovery)
