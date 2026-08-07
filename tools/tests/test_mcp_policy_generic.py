import importlib.util
import json
import sys
from pathlib import Path

import yaml

_TOOLS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mcp_policy", _TOOLS / "mcp_policy.py")
mp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mp
_spec.loader.exec_module(mp)
REPO_ROOT = _TOOLS.parent


def test_fixtures_manifest_driven_fit_expected_exposure():
    expected = {
        "sample": (1, 6),
    }
    for client, (server_count, tool_count) in expected.items():
        plan = mp.build_client_plan(REPO_ROOT, REPO_ROOT / "clients" / client)
        assert len(plan["servers"]) == server_count
        assert sum(len(server["tools"]) for server in plan["servers"]) == tool_count
        assert all(server["sizeBytes"] <= mp.POLICY_LIMIT_BYTES
                   for server in plan["servers"])
        assert all('?.ToString().IndexOf' not in server["policy"]
               and '?.ToString().StartsWith' not in server["policy"]
               for server in plan["servers"])
        assert all('JToken.Parse(' not in server["policy"] for server in plan["servers"])


def test_datetime_offset_remains_opaque_json_text():
    plan = mp.build_client_plan(REPO_ROOT, REPO_ROOT / "clients" / "sample")
    policy = plan["servers"][0]["policy"]

    assert "2026-08-06T14:30:00+02:00" in policy
    assert "2026-08-06T02:30:00" not in policy


def test_generator_source_has_no_fixture_dependency():
    source = (_TOOLS / "mcp_policy.py").read_text(encoding="utf-8").lower()
    for fixture in ("customer-care", "novaretail"):
        assert fixture not in source


def test_synthetic_client_and_contract(tmp_path):
    contract = {
        "openapi": "3.0.3",
        "info": {"title": "Inventory", "version": "1.0.0"},
        "paths": {
            "/v1/items/{itemId}": {
                "get": {
                    "operationId": "get-item",
                    "description": "Read an inventory item.",
                    "parameters": [{
                        "name": "itemId", "in": "path", "required": True,
                        "schema": {"type": "string"},
                    }],
                    "responses": {
                        "200": {"description": "ok", "content": {
                            "application/json": {"example": {
                                "itemId": "I-1", "available": True}}}},
                    },
                },
            },
        },
    }
    manifest = {
        "client": "acme",
        "displayName": "Acme Inventory",
        "mcpExposure": {"mode": "facade", "facadeName": "inventory"},
        "apis": [{
            "name": "inventory", "displayName": "Inventory",
            "backend": {"mode": "mock"}, "mcpTools": ["get-item"],
        }],
    }
    (tmp_path / "apis" / "inventory").mkdir(parents=True)
    (tmp_path / "apis" / "inventory" / "openapi.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    client_dir = tmp_path / "clients" / "acme"
    client_dir.mkdir(parents=True)
    (client_dir / "mcp-manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    plan = mp.build_client_plan(tmp_path, client_dir)

    assert plan["client"] == "acme"
    assert len(plan["servers"]) == 1
    server = plan["servers"][0]
    assert server["resourceName"] == "acme-inventory-policy-mcp"
    assert server["path"] == "acme/inventory-policy-mcp"
    assert server["tools"] == ["get-item"]
    assert "I-1" in server["policy"]


def test_sharding_is_automatic_and_bounded():
    client_dir = REPO_ROOT / "clients" / "sample"
    plan = mp.build_client_plan(REPO_ROOT, client_dir, limit=10000)

    assert len(plan["servers"]) > 1
    assert sum(len(server["tools"]) for server in plan["servers"]) == 6
    assert all(server["sizeBytes"] <= 10000 for server in plan["servers"])
    assert len({tool for server in plan["servers"] for tool in server["tools"]}) == 6


def test_generated_artifact_index_contains_no_policy_body(tmp_path):
    contract_root = tmp_path / "repo"
    client_dir = contract_root / "clients" / "acme"
    api_dir = contract_root / "apis" / "sample"
    api_dir.mkdir(parents=True)
    client_dir.mkdir(parents=True)
    (api_dir / "openapi.yaml").write_text(yaml.safe_dump({
        "openapi": "3.0.3", "info": {"title": "S", "version": "1"},
        "paths": {"/s": {"get": {"operationId": "get-s", "responses": {
            "200": {"description": "ok", "content": {
                "application/json": {"example": {"ok": True}}}}}}}},
    }, sort_keys=False), encoding="utf-8")
    (client_dir / "mcp-manifest.yaml").write_text(yaml.safe_dump({
        "client": "acme", "displayName": "Acme",
        "mcpExposure": {"mode": "perApi"},
        "apis": [{"name": "sample", "displayName": "Sample",
                  "backend": {"mode": "mock"}, "mcpTools": ["get-s"]}],
    }, sort_keys=False), encoding="utf-8")
    plan = mp.build_client_plan(contract_root, client_dir)

    output = mp.write_client_plan(client_dir, plan)
    index = json.loads((output / "servers.json").read_text(encoding="utf-8"))

    assert "policy" not in index["servers"][0]
    assert (output / index["servers"][0]["policyFile"]).exists()


def test_direct_build_rejects_external_backend(tmp_path):
    client_dir = tmp_path / "clients" / "acme"
    api_dir = tmp_path / "apis" / "sample"
    api_dir.mkdir(parents=True)
    client_dir.mkdir(parents=True)
    (api_dir / "openapi.yaml").write_text(yaml.safe_dump({
        "openapi": "3.0.3", "info": {"title": "S", "version": "1"},
        "paths": {"/s": {"get": {"operationId": "get-s", "responses": {
            "200": {"description": "ok", "content": {
                "application/json": {"example": {"ok": True}}}}}}}},
    }), encoding="utf-8")
    (client_dir / "mcp-manifest.yaml").write_text(yaml.safe_dump({
        "client": "acme", "displayName": "Acme",
        "apis": [{"name": "sample", "displayName": "Sample",
                  "backend": {"mode": "external", "url": "https://example.test"},
                  "mcpTools": ["get-s"]}],
    }), encoding="utf-8")

    try:
        mp.build_client_plan(tmp_path, client_dir)
        assert False, "external backend must be rejected"
    except mp.PolicyBuildError as error:
        assert "supports mock backend only" in str(error)
