import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from mcp_openapi_creator_kit.consumer_export import (
    contained, inline_local, inspect_targets, load_client, preview_plan, validate_schema,
)
from mcp_openapi_creator_kit.export_cli import main, write_artifacts
from mcp_openapi_creator_kit.targets import TargetError, https_url, parse_targets, target_capabilities

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def target_workspace(tmp_path):
    manifest, specs = load_client(ROOT, "sample")
    manifest["apis"][0]["mcpTools"] = ["get-customer-context"]
    manifest["targets"] = {
        "consumer": "copilot-studio", "gateway": "ai-gateway-preview",
        "preview": {"region": "eastus2", "restBaseUrls": {
            "customer-care": "https://sample.example.org/sample/customer-care"}},
    }
    for name, spec in specs.items():
        path = tmp_path / "apis" / name / "openapi.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(spec), "utf-8")
    (tmp_path / "clients" / "sample").mkdir(parents=True)
    return tmp_path, manifest, specs


def test_default_model_preserves_existing_profiles():
    assert parse_targets({}).consumer == "copilot-studio"
    assert parse_targets({}).gateway == "existing-apim"
    capabilities = target_capabilities()
    assert capabilities["consumerTargets"] == ["copilot-studio", "rest"]
    assert capabilities["gatewayProfilesUnchanged"] == [
        "native-mcp", "policy-mcp-consumption", "rest-consumption"]
    assert not capabilities["aiGatewayPreview"]["applySupported"]


@pytest.mark.parametrize("targets", [
    None, [], {"gateway": "native-mcp"}, {"consumer": "mcp-apps"},
    {"consumer": "m365-api-plugin"}, {"consumer": "m365-mcp-plugin"}, {"m365": {}},
    {"gateway": "ai-gateway-preview"}, {"preview": {"region": "westus"}},
    {"consumre": "rest"},
    {"gateway": "ai-gateway-preview", "preview": {"region": "westus"}},
    {"gateway": "ai-gateway-preview", "preview": {"region": "eastus2", "secret": "hidden"}},
])
def test_strict_target_model(targets):
    with pytest.raises(TargetError):
        parse_targets({"targets": targets})


def test_validation_does_not_echo_unknown_secret(target_workspace):
    _, manifest, _ = target_workspace
    manifest["targets"]["preview"]["secret"] = "DO-NOT-LOG-THIS"
    with pytest.raises(TargetError) as error:
        parse_targets(manifest)
    assert "DO-NOT-LOG-THIS" not in str(error.value)


@pytest.mark.parametrize("url", [
    "http://example.org", "******example.org", "https://example.org?k=secret",
    "https://example.org/#secret", "https://example.org/../admin",
    "https://example.org/%2e%2e/admin", "https://example.org\\admin",
    "https://example.org/\nadmin", "https://example.org:999999", "https://",
])
def test_unsafe_urls(url):
    with pytest.raises((TargetError, ValueError)):
        https_url(url)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/outside", "x\\outside", "a/../b", "a//b"])
def test_unsafe_paths(tmp_path, path):
    with pytest.raises(TargetError):
        contained(tmp_path, path)


def test_selected_projection_preserves_canonical_and_deployed_paths(target_workspace):
    _, manifest, specs = target_workspace
    before = copy.deepcopy(specs)
    first = preview_plan(manifest, specs)
    assert first == preview_plan(manifest, specs)
    assert before == specs
    assert set(first) == {"gateway-openapi-customer-care.json", "ai-gateway-plan.json"}
    doc = json.loads(first["gateway-openapi-customer-care.json"])
    assert doc["servers"] == [{"url": "https://sample.example.org/sample/customer-care"}]
    assert len(doc["paths"]) == 1
    assert doc["paths"]["/v1/customer-context"]["get"]["operationId"] == "get-customer-context"
    assert "create-reschedule-request" not in first["gateway-openapi-customer-care.json"].decode()
    assert '"$ref"' not in first["gateway-openapi-customer-care.json"].decode()
    assert '"x-mock"' not in first["gateway-openapi-customer-care.json"].decode()


def test_write_projection_preserves_required_idempotency(target_workspace):
    _, manifest, specs = target_workspace
    manifest["apis"][0]["mcpTools"] = ["create-reschedule-request"]
    before = copy.deepcopy(specs)
    artifacts = preview_plan(manifest, specs)
    doc = json.loads(artifacts["gateway-openapi-customer-care.json"])
    operation = next(iter(doc["paths"].values()))["post"]
    assert operation["operationId"] == "create-reschedule-request"
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"])
    assert before == specs


@pytest.mark.parametrize("mutation", ["external", "missing", "recursive", "sibling"])
def test_reference_failures_are_closed(target_workspace, mutation):
    _, manifest, specs = target_workspace
    spec = specs["customer-care"]
    op = spec["paths"]["/v1/customer-context"]["get"]
    schema = {"$ref": {
        "external": "https://evil.example/secret.yaml", "missing": "#/components/schemas/Missing",
        "recursive": "#/components/schemas/Cycle", "sibling": "#/components/schemas/Cycle",
    }[mutation]}
    spec["components"]["schemas"]["Cycle"] = {"$ref": "#/components/schemas/Cycle"}
    if mutation == "sibling":
        schema["description"] = "ambiguous"
    op["responses"]["200"]["content"]["application/json"]["schema"] = schema
    with pytest.raises(TargetError):
        preview_plan(manifest, specs)


def test_gateway_plan_does_not_promise_mock_or_management_apply(target_workspace):
    root, manifest, specs = target_workspace
    artifacts = preview_plan(manifest, specs)
    assert artifacts == preview_plan(manifest, specs)
    plan = json.loads(artifacts["ai-gateway-plan.json"])
    assert plan["applyAllowed"] is False
    assert plan["liveVerified"] is False
    assert plan["managementApiVersion"] == "2026-05-01-preview"
    assert any("not execute" in w for w in plan["warnings"])
    manifest["targets"]["consumer"] = "rest"
    report = inspect_targets(root, manifest, specs)
    assert any("not a replacement" in b for b in report["reports"]["ai-gateway-plan.json"]["blockers"])
    with pytest.raises(SystemExit) as error:
        main(["sample", "--root", str(root), "--apply"])
    assert error.value.code == 2
    assert not (root / "clients" / "sample" / "generated").exists()


def test_remote_mcp_federation_plan(target_workspace):
    _, manifest, specs = target_workspace
    manifest["targets"]["preview"] = {
        "region": "swedencentral", "source": "remote-mcp", "remoteServers": [{
            "name": "external-tools", "url": "https://example.org/mcp", "auth": "oauth2-interactive"}]}
    plan = json.loads(preview_plan(manifest, specs)["ai-gateway-plan.json"])
    assert plan["sources"][0]["name"] == "external-tools"
    assert "secret" not in json.dumps(plan["sources"])


def test_cli_writes_only_generated_namespace_and_readonly_report(target_workspace, capsys):
    root, manifest, _ = target_workspace
    path = root / "clients" / "sample" / "mcp-manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), "utf-8")
    assert main(["sample", "--root", str(root), "--report"]) == 0
    assert not (path.parent / "generated").exists()
    assert json.loads(capsys.readouterr().out)["offlineReady"]
    assert main(["sample", "--root", str(root)]) == 0
    output = path.parent / "generated" / "targets"
    assert {p.name for p in output.iterdir()} == {
        "ai-gateway-plan.json", "gateway-openapi-customer-care.json"}
    assert yaml.safe_load(path.read_text("utf-8")) == manifest


@pytest.mark.parametrize("consumer", ["copilot-studio", "rest"])
def test_existing_apim_report_works_but_has_no_preview_export(target_workspace, capsys, consumer):
    root, manifest, specs = target_workspace
    manifest["targets"] = {"consumer": consumer}
    report = inspect_targets(root, manifest, specs)
    assert report["offlineReady"] and report["artifacts"] == []
    path = root / "clients" / "sample" / "mcp-manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), "utf-8")
    assert main(["sample", "--root", str(root), "--report"]) == 0
    with pytest.raises(SystemExit) as error:
        main(["sample", "--root", str(root)])
    assert error.value.code == 2
    assert "Configure targets.gateway ai-gateway-preview first" in capsys.readouterr().err
    assert not (path.parent / "generated").exists()


def test_pinned_schemas_have_verifiable_hashes():
    path = ROOT / "src" / "mcp_openapi_creator_kit" / "schemas"
    sources = json.loads((path / "sources.json").read_text("utf-8"))
    assert set(sources) == {"openapi"}
    assert {p.name for p in path.glob("*.json")} == {"openapi.json", "sources.json"}
    for name, source in sources.items():
        assert hashlib.sha256((path / f"{name}.json").read_bytes()).hexdigest() == source["sha256"]


def test_example_payload_ref_is_data_not_a_network_reference(target_workspace):
    _, manifest, specs = target_workspace
    operation = specs["customer-care"]["paths"]["/v1/customer-context"]["get"]
    payload = {"$ref": "https://example.org/not-fetched", "x-mock": "literal data"}
    operation["responses"]["200"]["content"]["application/json"]["example"] = payload
    output = json.loads(preview_plan(manifest, specs)["gateway-openapi-customer-care.json"])
    assert output["paths"]["/v1/customer-context"]["get"]["responses"]["200"]["content"]["application/json"]["example"] == payload


def test_named_property_example_still_resolves_schema_refs():
    spec = {"components": {"schemas": {"String": {"type": "string"}}}}
    value = {"type": "object", "properties": {"example": {"$ref": "#/components/schemas/String"}}}
    assert inline_local(spec, value)["properties"]["example"] == {"type": "string"}


@pytest.mark.parametrize("name", [
    "content", "headers", "responses", "schema", "properties", "schemas",
    "parameters", "requestBodies", "examples", "encoding", "links", "callbacks",
    "paths", "securitySchemes", "example", "default", "enum", "value", "$ref", "x-data",
])
@pytest.mark.parametrize("external", [False, True])
def test_structural_refs_under_keyword_property_names(target_workspace, name, external):
    _, manifest, specs = target_workspace
    spec = specs["customer-care"]
    nested = {"type": "object", "properties": {"result": {"type": "string"}}}
    spec["components"]["schemas"]["Nested"] = nested
    ref = {"$ref": "https://example.org/nested.yaml" if external else "#/components/schemas/Nested"}
    schema = {"type": "object", "properties": {
        name: ref,
        "nested": {"type": "array", "items": {"type": "object", "properties": {
            name: {"type": "object", "properties": {name: ref}}}}},
    }}
    operation = spec["paths"]["/v1/customer-context"]["get"]
    operation["responses"]["200"]["content"]["application/json"]["schema"] = schema
    if external:
        with pytest.raises(TargetError, match="Only local"):
            preview_plan(manifest, specs)
        return
    output = json.loads(preview_plan(manifest, specs)["gateway-openapi-customer-care.json"])
    projected = output["paths"]["/v1/customer-context"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert projected["properties"][name] == nested
    assert projected["properties"]["nested"]["items"]["properties"][name]["properties"][name] == nested
    assert "#/components/" not in json.dumps(output)


@pytest.mark.parametrize("name", ["content", "headers", "responses", "schema", "value", "$ref"])
def test_media_examples_preserve_literal_refs_with_structural_names(target_workspace, name):
    _, manifest, specs = target_workspace
    spec = specs["customer-care"]
    payload = {"$ref": "#/components/schemas/NotAStructuralReference",
               "nested": {"$ref": "https://example.org/literal", "externalValue": "literal"},
               "properties": {"content": {"$ref": "literal"}}}
    spec["components"]["examples"] = {"Payload": {"value": payload}}
    media = spec["paths"]["/v1/customer-context"]["get"]["responses"]["200"]["content"]["application/json"]
    media.pop("example", None)
    media["examples"] = {name: {"$ref": "#/components/examples/Payload"},
                         "inline": {"value": payload}}
    output = json.loads(preview_plan(manifest, specs)["gateway-openapi-customer-care.json"])
    examples = output["paths"]["/v1/customer-context"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert examples == {name: {"value": payload}, "inline": {"value": payload}}


@pytest.mark.parametrize("name", ["content", "headers", "responses", "schema", "$ref"])
@pytest.mark.parametrize("external_example", [False, True])
def test_named_media_examples_cannot_hide_external_references(target_workspace, name, external_example):
    _, manifest, specs = target_workspace
    media = specs["customer-care"]["paths"]["/v1/customer-context"]["get"]["responses"]["200"]["content"]["application/json"]
    media.pop("example", None)
    media["examples"] = {name: {
        "externalValue" if external_example else "$ref": "https://example.org/not-fetched"}}
    with pytest.raises(TargetError, match="External examples|Only local"):
        preview_plan(manifest, specs)


def test_hardlink_output_cannot_overwrite_source(target_workspace):
    root, _, _ = target_workspace
    output = root / "clients" / "sample" / "generated" / "targets"
    output.mkdir(parents=True)
    original = root / "original.txt"
    original.write_bytes(b"original")
    (output / "ai-gateway-plan.json").hardlink_to(original)
    with pytest.raises(TargetError, match="hard-linked"):
        write_artifacts(root, "sample", {"ai-gateway-plan.json": b"replacement"})
    assert original.read_bytes() == b"original"


def test_source_and_output_symlinks_are_contained(target_workspace):
    root, _, _ = target_workspace
    alias = root / "alias"
    try:
        alias.symlink_to(root.parent, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows account")
    with pytest.raises(TargetError, match="escapes workspace"):
        contained(root, "alias/outside")
    generated = root / "clients" / "sample" / "generated"
    generated.mkdir()
    target = root / "outputs"
    target.mkdir()
    (generated / "targets").symlink_to(target, target_is_directory=True)
    with pytest.raises(TargetError, match="symlinks"):
        write_artifacts(root, "sample", {"ai-gateway-plan.json": b"replacement"})
    assert not list(target.iterdir())


def test_schema_failures_omit_payload_content():
    with pytest.raises(TargetError) as error:
        validate_schema({"name": "PRIVATE-PAYLOAD"}, "openapi")
    assert "PRIVATE-PAYLOAD" not in str(error.value)


def test_special_property_names_are_not_reference_objects():
    value = {"type": "object", "properties": {
        "$ref": {"type": "string"}, "externalValue": {"type": "string"}}}
    assert inline_local({}, value) == value


def test_discriminator_mapping_cannot_leave_dangling_components():
    with pytest.raises(TargetError, match="Discriminator"):
        inline_local({}, {"type": "object", "discriminator": {
            "propertyName": "type", "mapping": {"dog": "#/components/schemas/Dog"}}})
