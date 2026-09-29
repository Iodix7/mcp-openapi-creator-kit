import copy
import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from lifecycle import ReconcileError
from test_build_facade import CONTRACT, MANIFEST, bf

spec = importlib.util.spec_from_file_location("prepare_variant", TOOLS / "prepare-variant.py")
variant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variant)


@pytest.fixture
def source(tmp_path):
    api = tmp_path / "apis" / "things"
    client = tmp_path / "clients" / "demo"
    api.mkdir(parents=True)
    client.mkdir(parents=True)
    (api / "openapi.yaml").write_text(yaml.safe_dump(CONTRACT), encoding="utf-8")
    (client / "mcp-manifest.yaml").write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")
    return tmp_path


def test_variant_deterministic_dry_run_and_valid_generator_output(source, monkeypatch):
    before = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
    plan = variant.prepare(source, "demo", "variant")
    assert plan == variant.prepare(source, "demo", "variant")
    assert {path: path.read_bytes() for path in source.rglob("*") if path.is_file()} == before
    variant.apply(source, plan)
    manifest = yaml.safe_load((source / "clients" / "variant" / "mcp-manifest.yaml").read_text())
    contract = yaml.safe_load((source / "apis" / "things-variant" / "openapi.yaml").read_text())
    assert manifest["apis"][0]["mcpTools"] == ["variant-get-thing"]
    assert manifest["apis"][0]["name"] == "things-variant"
    assert contract["paths"]["/v1/things/{thingId}"]["get"]["responses"] == CONTRACT["paths"]["/v1/things/{thingId}"]["get"]["responses"]
    assert not (source / "clients" / "variant" / "generated").exists()
    monkeypatch.setattr(bf, "REPO_ROOT", source)
    bf.build_client(source / "clients" / "variant")
    assert (source / "clients" / "variant" / "generated" / "client.bicep").exists()
    assert all(path.read_bytes() == contents for path, contents in before.items())


@pytest.mark.parametrize("collision", ["client", "contract", "operation"])
def test_collision_validated_before_source_writes(source, collision):
    if collision == "client":
        (source / "clients" / "variant").mkdir()
    elif collision == "contract":
        (source / "apis" / "things-variant").mkdir()
    else:
        other = source / "apis" / "other"
        other.mkdir()
        contract = copy.deepcopy(CONTRACT)
        contract["paths"]["/v1/things/{thingId}"]["get"]["operationId"] = "variant-get-thing"
        (other / "openapi.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    before = {str(path.relative_to(source)) for path in source.rglob("*")}
    with pytest.raises(ReconcileError):
        variant.prepare(source, "demo", "variant")
    assert before == {str(path.relative_to(source)) for path in source.rglob("*")}


def test_external_reference_rejected_without_writes(source):
    path = source / "apis" / "things" / "openapi.yaml"
    contract = copy.deepcopy(CONTRACT)
    contract["components"] = {"schemas": {"External": {"$ref": "https://example.invalid/schema"}}}
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    with pytest.raises(ReconcileError, match="local JSON Pointer"):
        variant.prepare(source, "demo", "variant")
    assert not (source / "clients" / "variant").exists()


def test_variant_rewrites_links_not_examples_or_canonical_schemas(source):
    path = source / "apis" / "things" / "openapi.yaml"
    contract = copy.deepcopy(CONTRACT)
    response = contract["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"]
    response["links"] = {"self": {"operationId": "get-thing"}}
    contract["components"] = {"schemas": {"Literal": {
        "type": "object", "example": {"operationId": "get-thing", "$ref": "literal"}}}}
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    plan = variant.prepare(source, "demo", "variant")
    output = yaml.safe_load(plan[source / "apis" / "things-variant" / "openapi.yaml"])
    assert output["components"]["schemas"] == contract["components"]["schemas"]
    assert output["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"]["links"]["self"]["operationId"] == "variant-get-thing"


def test_write_rechecks_no_overwrite(source):
    plan = variant.prepare(source, "demo", "variant")
    (source / "clients" / "variant").mkdir()
    with pytest.raises(ReconcileError, match="nothing written"):
        variant.apply(source, plan)
    assert not (source / "apis" / "things-variant").exists()


def test_external_example_reference_rejected_but_literal_example_preserved(source):
    path = source / "apis" / "things" / "openapi.yaml"
    contract = copy.deepcopy(CONTRACT)
    media = contract["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"]["content"]["application/json"]
    media.pop("example")
    media["examples"] = {"remote": {"$ref": "https://example.invalid/example.yaml"}}
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    with pytest.raises(ReconcileError, match="local JSON Pointer"):
        variant.prepare(source, "demo", "variant")


def test_component_links_and_operation_refs_preserved_consistently(source):
    path = source / "apis" / "things" / "openapi.yaml"
    contract = copy.deepcopy(CONTRACT)
    contract["components"] = {"links": {
        "byId": {"operationId": "get-thing"},
        "byPointer": {"operationRef": "#/paths/~1v1~1things~1{thingId}/get"},
    }}
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    plan = variant.prepare(source, "demo", "variant")
    output = yaml.safe_load(plan[source / "apis" / "things-variant" / "openapi.yaml"])
    assert output["components"]["links"]["byId"]["operationId"] == "variant-get-thing"
    assert output["components"]["links"]["byPointer"] == contract["components"]["links"]["byPointer"]


@pytest.mark.parametrize("simulation", [None, 429, 503])
def test_runtime_variant_validates_target_context_and_preserves_contract(tmp_path, monkeypatch, simulation):
    from test_rest_runtime import agreement, write_agreement, PATH
    contract, manifest = agreement()
    manifest["apis"][0]["runtime"]["allowedSubscriptionIds"] = [
        "demo-pilot", "demo-other", "shared-subscription", "DEMO-pilot"]
    if simulation:
        manifest["apis"][0]["runtime"]["simulate"] = {"status": simulation}
    write_agreement(tmp_path, contract, manifest)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    plan = variant.prepare(tmp_path, "demo", "variant")
    assert plan == variant.prepare(tmp_path, "demo", "variant")
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    target_manifest = yaml.safe_load(plan[tmp_path / "clients/variant/mcp-manifest.yaml"])
    target_contract = yaml.safe_load(plan[tmp_path / "apis/things-variant/openapi.yaml"])
    target_api = target_manifest["apis"][0]
    assert target_api["name"] == "things-variant"
    assert target_api["mcpTools"] == ["variant-get-thing-status"]
    assert target_api["runtime"]["allowedSubscriptionIds"] == [
        "variant-pilot", "demo-other", "shared-subscription", "DEMO-pilot"]
    assert target_api["runtime"].get("simulate") == manifest["apis"][0]["runtime"].get("simulate")
    assert target_manifest["inboundAuth"] == manifest["inboundAuth"]
    expected = copy.deepcopy(contract)
    expected["paths"][PATH]["get"]["operationId"] = "variant-get-thing-status"
    assert target_contract == expected

    variant.apply(tmp_path, plan)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    outputs = bf.build_client(tmp_path / "clients/variant", write=False)
    policy = outputs[tmp_path / "clients/variant/generated/api-things-variant.policy.xml"]
    assert 'context.Subscription.Id == &quot;variant-pilot&quot;' in policy
    assert 'context.Subscription.Id == &quot;demo-pilot&quot;' not in policy
    assert 'context.Subscription.Id == &quot;demo-other&quot;' in policy
    assert all(path.read_bytes() == contents for path, contents in before.items())


def test_runtime_variant_without_source_pilot_does_not_invent_authorization(tmp_path):
    from test_rest_runtime import agreement, write_agreement
    contract, manifest = agreement()
    manifest["apis"][0]["runtime"]["allowedSubscriptionIds"] = ["independent-subscription"]
    write_agreement(tmp_path, contract, manifest)
    plan = variant.prepare(tmp_path, "demo", "variant")
    target = yaml.safe_load(plan[tmp_path / "clients/variant/mcp-manifest.yaml"])
    assert target["apis"][0]["runtime"]["allowedSubscriptionIds"] == ["independent-subscription"]


def test_runtime_v2_variant_renames_both_override_maps(tmp_path, monkeypatch):
    from test_rest_runtime_v2 import agreement_v2
    from test_rest_runtime import write_agreement
    contract, manifest = agreement_v2()
    contract["x-kit-runtime"]["operations"] = {
        "post-thing": {"correlation": copy.deepcopy(contract["x-kit-runtime"]["correlation"])}}
    manifest["apis"][0]["runtime"]["operations"] = {
        "post-thing": {"rateLimit": {"calls": 3, "renewalPeriod": 60}, "simulate": {"status": 503}}}
    write_agreement(tmp_path, contract, manifest)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plan = variant.prepare(tmp_path, "demo", "variant")
    target_contract = yaml.safe_load(plan[tmp_path / "apis/things-variant/openapi.yaml"])
    target_manifest = yaml.safe_load(plan[tmp_path / "clients/variant/mcp-manifest.yaml"])
    for source, target in (
        (contract["x-kit-runtime"], target_contract["x-kit-runtime"]),
        (manifest["apis"][0]["runtime"], target_manifest["apis"][0]["runtime"]),
    ):
        assert target["operations"] == {"variant-post-thing": source["operations"]["post-thing"]}
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    variant.apply(tmp_path, plan)
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    bf.build_client(tmp_path / "clients/variant", write=False)


@pytest.mark.parametrize("failure", ["missing-runtime", "unsupported-header", "oversized", "pilot-collision"])
def test_runtime_variant_failures_leave_no_files(tmp_path, failure):
    from test_rest_runtime import agreement, write_agreement, PATH
    contract, manifest = agreement()
    if failure == "missing-runtime":
        manifest["apis"][0].pop("runtime")
    elif failure == "unsupported-header":
        contract["paths"][PATH]["get"]["responses"]["200"]["headers"]["Cache-Control"] = {
            "description": "no-store", "schema": {"type": "string"}}
    elif failure == "oversized":
        media = contract["paths"][PATH]["get"]["responses"]["200"]["content"]["application/json"]
        media["examples"]["T-1"]["value"]["thingId"] = "x" * 16384
    else:
        manifest["apis"][0]["runtime"]["allowedSubscriptionIds"] = ["demo-pilot", "variant-pilot"]
    write_agreement(tmp_path, contract, manifest)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit):
        variant.prepare(tmp_path, "demo", "variant")
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "clients/variant").exists()
    assert not (tmp_path / "apis/things-variant").exists()
