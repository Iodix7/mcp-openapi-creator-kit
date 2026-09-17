"""Original supported fictional content, explicitly selected and safely imported."""
import copy
import hashlib
from pathlib import Path
import socket
import subprocess

import pytest
import yaml

from mcp_openapi_creator_kit import examples
from mcp_openapi_creator_kit.runtime import command
from mcp_openapi_creator_kit.workspace import WorkspaceReader


ORIGINAL_CONTRACT_HASHES = {
    "customer-care": "04fc975f09b61e86238452b971f35fee45b96f91afd72411944a8d1357da598c",
    "fsi-compliance": "90a95bd3c90571d227afe249961310749bfcbbf5d1734461ce243637cd10b50f",
    "fsi-core-banking": "ff533764031fc1653ad9cb24455978b91fce8615aed0b9cfb651b6d4bc1fecc0",
    "fsi-crm": "cfe9899f7e471d6c6b3edc3ba311727e11ab3c30cf89366cdab2c998af03c8df",
    "fsi-monitoring": "95a90acf9e92004c5c5a23cf297afeb40543d63e4a444722a9b9bb1e1fea82df",
}


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_complete_supported_inventory_and_byte_identical_contract_transfer():
    library = examples.library_root()
    listed = examples.list_examples()
    assert listed["summary"] == {"scenarios": 2, "apis": 5, "tools": 18}
    assert [item["id"] for item in listed["scenarios"]] == ["customer-care", "fsi-rm360"]
    assert listed["activeClients"] == [] and listed["writes"] is False
    assert {p.parent.name for p in (library / "apis").glob("*/openapi.yaml")} == set(ORIGINAL_CONTRACT_HASHES)
    for name, expected in ORIGINAL_CONTRACT_HASHES.items():
        assert hashlib.sha256((library / "apis" / name / "openapi.yaml").read_bytes()).hexdigest() == expected
    for path in library.rglob("*"):
        assert not {"generated", ".azure", "experimental", "secrets"} & set(path.parts)
        if path.is_file():
            assert path.suffix in {".yaml", ".md"}
    for item in listed["scenarios"]:
        manifest = yaml.safe_load(
            (library / "clients" / item["sourceClient"] / "mcp-manifest.yaml").read_text("utf-8"))
        assert all(api["backend"] == {"mode": "mock"} for api in manifest["apis"])
        assert manifest["networkProfile"] == "public"
        assert not any(k in manifest for k in ("subscriptionId", "tenantId", "apimName", "resourceGroup"))
    schemas = [yaml.safe_load(p.read_text("utf-8"))["components"]["schemas"]["Problem"]
               for p in (library / "apis").glob("*/openapi.yaml")]
    assert all(schema == schemas[0] for schema in schemas)


def test_original_storyline_and_instructions_transferred_without_deployment_claims():
    docs = examples.library_root() / "docs" / "fsi"
    assert {p.name for p in docs.glob("*.md")} == {"storylines.md", "rm360-spec.md", "agent-instructions.md"}
    story = (docs / "storylines.md").read_text("utf-8")
    for phrase in ("Molino Ferrari", "CUST-FSI-0042", "LN-2026-0187", "Eastbridge Trading FZE",
                   "pending-approval", "cash management", "Idempotency-Key"):
        assert phrase in story
    spec = (docs / "rm360-spec.md").read_text("utf-8")
    assert "Epic A" in spec and "Epic B" in spec and "Criteri di accettazione" in spec
    assert "Mapping Dataverse" in spec and "mcp-kit build" in spec
    for path in docs.glob("*.md"):
        text = path.read_text("utf-8")
        assert "python tools/" not in text
        assert "import-map.json" in text


def test_full_builtin_catalog_is_inactive_and_has_unambiguous_asset_provenance(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Builtin catalog must not write files or contact Azure")

    before = snapshot(examples.library_root())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    catalog = examples.builtin_catalog()
    assert catalog["summary"] == {"scenarios": 5, "operations": 18, "schemas": 12, "clients": 0}
    assert catalog["source"] == "builtin-starter-library"
    assert catalog["clients"] == [] and catalog["workflow"] == []
    assert catalog["exampleLibrary"]["assetRoot"] == "examples/library"
    assert catalog["exampleLibrary"]["summary"] == {"scenarios": 2, "apis": 5, "tools": 18}
    assert {record["id"] for record in catalog["scenarios"]} == set(ORIGINAL_CONTRACT_HASHES)
    for record in catalog["scenarios"]:
        assert record["usedBy"] == [] and record["scenarioContexts"] == []
        assert record["sourceLanguage"] == "it"
        assert record["exampleSource"] == f"examples/library/apis/{record['id']}/openapi.yaml"
        expected = "customer-care" if record["id"] == "customer-care" else "fsi-rm360"
        assert record["exampleScenarioIds"] == [expected]
    assert not list(tmp_path.iterdir())
    assert snapshot(examples.library_root()) == before


@pytest.mark.parametrize("scenario,count", [("customer-care", 6), ("fsi-rm360", 12)])
def test_dry_run_import_and_real_generators_keep_examples_shapes_refs(tmp_path, monkeypatch, scenario, count):
    def forbidden(*args, **kwargs):
        pytest.fail("Example listing/import must not invoke cloud/network/child commands")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    original = snapshot(tmp_path)
    assert WorkspaceReader(tmp_path).status()["clients"] == []
    assert examples.list_examples()["activeClients"] == []
    plan = examples.plan_import(tmp_path, scenario, "pilot")
    assert plan == examples.plan_import(tmp_path, scenario, "pilot")
    assert snapshot(tmp_path) == original
    assert len(plan.tool_map) == count
    assert all(tool.startswith("pilot-") for tool in plan.tool_map.values())
    assert all(name.endswith("-pilot") for name in plan.api_map.values())
    result = examples.apply_import(tmp_path, plan)
    assert result["writes"] and not result["deployed"]
    assert not list(tmp_path.rglob("generated"))
    assert not (tmp_path / "docs" / "pilot" / "spec.md").exists()
    for old, new in plan.api_map.items():
        baseline = yaml.safe_load((examples.library_root() / "apis" / old / "openapi.yaml").read_text("utf-8"))
        variant = yaml.safe_load((tmp_path / "apis" / new / "openapi.yaml").read_text("utf-8"))
        assert variant["components"]["schemas"] == baseline["components"]["schemas"]
        for path, methods in baseline["paths"].items():
            for method, operation in methods.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                updated = variant["paths"][path][method]
                assert updated["operationId"] == plan.tool_map[operation["operationId"]]
                assert updated.get("x-mock") == operation.get("x-mock")
                assert updated["responses"] == operation["responses"]
    bf = command("build-facade")
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    bf.build_client(tmp_path / "clients" / "pilot")
    assert (tmp_path / "clients" / "pilot" / "generated" / "client.bicep").is_file()
    from mcp_openapi_creator_kit.catalog import build_index
    catalog = build_index(tmp_path)
    assert catalog["summary"]["operations"] == count
    assert all(s["compatibility"]["policy-mcp-consumption"]["supported"] for s in catalog["scenarios"])
    from mcp_openapi_creator_kit.policy import build_client_plan, write_client_plan
    policy_plan = build_client_plan(tmp_path, tmp_path / "clients" / "pilot")
    assert {tool for server in policy_plan["servers"] for tool in server["tools"]} == set(plan.tool_map.values())
    assert all(server["sizeBytes"] <= 16384 for server in policy_plan["servers"])
    write_client_plan(tmp_path / "clients" / "pilot", policy_plan)


def test_all_scenarios_coexist_and_second_identity_remains_distinct(tmp_path):
    for scenario, client in (("customer-care", "care"), ("fsi-rm360", "bank"), ("fsi-rm360", "bank-two")):
        examples.apply_import(tmp_path, examples.plan_import(tmp_path, scenario, client))
    data = WorkspaceReader(tmp_path).status()
    assert len(data["clients"]) == 3 and len(data["contracts"]) == 9
    assert (tmp_path / "docs" / "bank" / "library" / "storylines.md").is_file()
    assert (tmp_path / "docs" / "bank-two" / "library" / "import-map.json").is_file()
    manifest = yaml.safe_load((tmp_path / "clients" / "bank" / "mcp-manifest.yaml").read_text())
    assert manifest["mcpExposure"]["mode"] == "perApi"
    assert not (tmp_path / "clients" / "fsi-demo").exists()


@pytest.mark.parametrize("collision", ["client", "api", "tool", "docs", "canonical"])
def test_import_collision_never_modifies_customer_data(tmp_path, collision):
    if collision in {"client", "api", "docs"}:
        target = {"client": tmp_path / "clients" / "pilot",
                  "api": tmp_path / "apis" / "customer-care-pilot",
                  "docs": tmp_path / "docs" / "pilot"}[collision]
        target.mkdir(parents=True)
        (target / "keep.txt").write_text("Customer-owned work", encoding="utf-8")
    else:
        folder = tmp_path / "apis" / "existing"
        folder.mkdir(parents=True)
        spec = yaml.safe_load((examples.library_root() / "apis" / "customer-care" / "openapi.yaml").read_text("utf-8"))
        if collision == "tool":
            operation = next(op for _, _, op in command("build-facade").iter_operations(spec)
                             if op["operationId"] == "get-customer-context")
            operation["operationId"] = "pilot-get-customer-context"
        else:
            spec["components"]["schemas"]["Problem"]["description"] = "Different canonical schema"
        (folder / "openapi.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises((RuntimeError, ValueError, SystemExit)):
        examples.plan_import(tmp_path, "customer-care", "pilot")
    assert snapshot(tmp_path) == before


def test_apply_rechecks_collisions_and_rejects_modified_plan(tmp_path):
    plan = examples.plan_import(tmp_path, "customer-care", "pilot")
    tampered = copy.deepcopy(plan)
    tampered.files[tmp_path / "docs" / "pilot" / "example-reference.md"] = "unreviewed content"
    with pytest.raises(ValueError, match="changed"):
        examples.apply_import(tmp_path, tampered)
    assert snapshot(tmp_path) == {}
    (tmp_path / "clients" / "pilot").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already exists"):
        examples.apply_import(tmp_path, plan)
    assert not (tmp_path / "apis").exists()


def test_failed_write_rolls_back_only_new_data(tmp_path, monkeypatch):
    plan = examples.plan_import(tmp_path, "fsi-rm360", "pilot")
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    before = snapshot(tmp_path)
    original = Path.open

    def fail(path, mode="r", *args, **kwargs):
        if mode == "x" and path.name == "example-reference.md":
            raise OSError("simulated disk error")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="simulated disk error"):
        examples.apply_import(tmp_path, plan)
    assert snapshot(tmp_path) == before
    assert list(tmp_path.iterdir()) == [tmp_path / "keep.txt"]


@pytest.mark.parametrize("scenario,client", [("unknown", "pilot"), ("customer-care", "../escape"), ("fsi-rm360", "Bad_ID")])
def test_unknown_or_unsafe_selection_makes_no_writes(tmp_path, scenario, client):
    with pytest.raises((ValueError, RuntimeError)):
        examples.plan_import(tmp_path, scenario, client)
    assert not list(tmp_path.iterdir())
