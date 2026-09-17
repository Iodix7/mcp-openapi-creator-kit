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
