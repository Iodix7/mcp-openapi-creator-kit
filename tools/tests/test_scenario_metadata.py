"""Specification projections preserve client boundaries and never infer business facts."""
import json

import pytest
from mcp import Client
import yaml

from mcp_openapi_creator_kit import cli
from mcp_openapi_creator_kit.catalog import build_index
from mcp_openapi_creator_kit.scenario import inventory, plan_spec_sync, apply_spec_sync, check_spec
from mcp_openapi_creator_kit.scenario_metadata import parse_spec_metadata
from mcp_openapi_creator_kit.server import create_server


def write_spec(root, client="fixture", *, persona="Support operator"):
    header = "---\n" + yaml.safe_dump({"catalog": {
        "persona": {"en": persona, "it": "Operatore assistenza"},
        "jobToBeDone": {"it": "Controllare la pratica"},
        "outcome": {"en": "Resolve the next action"},
    }}, sort_keys=False, allow_unicode=True) + "---\n\n"
    path = root / "docs" / client / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "# Fictional scenario\n\nApproved narrative for this isolated test.\n",
                    encoding="utf-8")
    return path, header


def test_no_projection_is_invented_from_prose():
    assert parse_spec_metadata("# Persona\nI am a helpful consultant.") == {}
    assert parse_spec_metadata("---\ntitle: Ordinary frontmatter\n---\n# Scenario") == {}
    assert parse_spec_metadata('---\n"catalog":\n  persona: Operator\n---') == {
        "persona": {"source": "Operator"}}


@pytest.mark.parametrize("prefix", ["", "\ufeff"])
@pytest.mark.parametrize("closing", ["---", "..."])
def test_frontmatter_accepts_bom_crlf_and_trailing_whitespace(prefix, closing):
    text = prefix + "--- \t\r\ncatalog:\r\n  persona: |\r\n    Operator\r\n    ---\r\n    Supervisor\r\n"
    text += closing + " \t\r\n# Scenario\r\n"
    assert parse_spec_metadata(text) == {
        "persona": {"source": "Operator\n---\nSupervisor"}}


@pytest.mark.parametrize("header", [
    "catalog: {}",
    "catalog: []",
    "catalog:\n  persona: 42",
    "catalog:\n  persona: {fr: Operateur}",
    "catalog:\n  persona: ''",
    "catalog:\n  persona: {en: Operator, en: Other}",
    "catalog:\n  persona: {en: '<role>'}",
    "catalog:\n  persona: &role Operator\n  outcome: *role",
    "catalog:\n  credential: anything",
    "catalog: [unfinished",
    "catalog:\n  persona: " + "x" * 2001,
])
def test_invalid_declared_metadata_fails_actionably(header):
    with pytest.raises(ValueError, match="[Ss]cenario"):
        parse_spec_metadata("---\n" + header + "\n---\n# Scenario")


def test_unclosed_and_oversized_headers_are_not_silently_ignored():
    for text in ("---\ncatalog:\n  persona: Operator",
                 "---\ncatalog:\n  persona: " + "x" * 17000 + "\n---"):
        with pytest.raises(ValueError, match="16 KiB"):
            parse_spec_metadata(text)


def test_catalog_projects_current_spec_and_sync_preserves_it(mcp_workspace):
    path, header = write_spec(mcp_workspace)
    plan = plan_spec_sync(mcp_workspace, "fixture")
    apply_spec_sync(mcp_workspace, plan)
    assert path.read_text(encoding="utf-8").startswith(header)
    assert check_spec(mcp_workspace, "fixture", inventory(mcp_workspace, "fixture")).status == "consistent"
    first = build_index(mcp_workspace)
    scenario = first["scenarios"][0]
    assert scenario["persona"] == {"en": "Support operator", "it": "Operatore assistenza"}
    assert scenario["jobToBeDone"]["it"] == "Controllare la pratica"
    assert scenario["scenarioContexts"][0]["source"] == "docs/fixture/spec.md"
    assert first["clients"][0]["scenario"]["outcome"]["en"] == "Resolve the next action"
    assert not any(item["field"] == "scenario.persona" for item in first["warnings"])
    path.write_text(path.read_text(encoding="utf-8").replace("Support operator", "Case specialist"),
                    encoding="utf-8")
    assert build_index(mcp_workspace)["scenarios"][0]["persona"]["en"] == "Case specialist"


def test_shared_contract_preserves_conflicting_client_contexts(mcp_workspace):
    write_spec(mcp_workspace)
    path = mcp_workspace / "clients" / "fixture" / "mcp-manifest.yaml"
    other = yaml.safe_load(path.read_text(encoding="utf-8"))
    other["client"], other["displayName"] = "second", "Second fictional client"
    destination = mcp_workspace / "clients" / "second" / "mcp-manifest.yaml"
    destination.parent.mkdir()
    destination.write_text(yaml.safe_dump(other), encoding="utf-8")
    write_spec(mcp_workspace, "second", persona="Supervisor")
    index = build_index(mcp_workspace)
    scenario = index["scenarios"][0]
    assert scenario["persona"] is None
    assert [item["client"] for item in scenario["scenarioContexts"]] == ["fixture", "second"]
    assert any(item["field"] == "scenario.persona.conflict" for item in index["warnings"])
    override = mcp_workspace / "catalog" / "metadata.yaml"
    override.parent.mkdir(exist_ok=True)
    override.write_text(yaml.safe_dump({"contracts": {scenario["id"]: {
        "scenario": {"persona": {"en": "Editorial summary"}}
    }}}), encoding="utf-8")
    result = build_index(mcp_workspace, override)["scenarios"][0]
    assert result["persona"] == {"en": "Editorial summary"}
    assert len(result["scenarioContexts"]) == 2


def test_malformed_metadata_blocks_prepare_before_generating(mcp_workspace, capsys):
    path, _ = write_spec(mcp_workspace)
    apply_spec_sync(mcp_workspace, plan_spec_sync(mcp_workspace, "fixture"))
    path.write_text(path.read_text(encoding="utf-8").replace("Support operator", "<role>"), encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["--workspace", str(mcp_workspace), "prepare", "clients/fixture", "--profile", "native-mcp"])
    assert "placeholder" in capsys.readouterr().err
    assert not (mcp_workspace / "clients" / "fixture" / "generated").exists()


@pytest.mark.anyio
async def test_mcp_search_matches_italian_business_context(mcp_workspace):
    write_spec(mcp_workspace)
    async with Client(create_server(mcp_workspace).mcp, mode="legacy") as client:
        for query in ("Controllare la pratica", "Operatore assistenza", "Resolve the next action"):
            result = await client.call_tool("catalog-search", {"query": query})
            assert not result.is_error
            assert result.structured_content["total"] == 1
        catalog = await client.read_resource("kit://catalog/index")
        assert json.loads(catalog.contents[0].text)["scenarios"][0]["scenarioContexts"]
