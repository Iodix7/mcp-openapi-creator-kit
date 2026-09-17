"""Customer/kit trust separation regressions (no Azure)."""
import json
import os
from pathlib import Path
import sys

import pytest
from mcp import Client

from mcp_openapi_creator_kit import __version__, guidance
from mcp_openapi_creator_kit.assets import kit_root
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.data_paths import safe_data_path
from mcp_openapi_creator_kit.server import create_server
from mcp_openapi_creator_kit.workspace import WorkspaceError, WorkspaceReader


def test_empty_workspace_is_valid_and_missing_root_is_not_created(tmp_path):
    status = WorkspaceReader(tmp_path).status()
    assert status["valid"] and status["workspaceMode"] == "empty"
    assert status["clients"] == status["contracts"] == []
    assert status["kitVersion"] == __version__
    missing = tmp_path / "missing"
    with pytest.raises(WorkspaceError, match="existing"):
        WorkspaceReader(missing)
    assert not missing.exists()


def test_init_and_sample_are_explicit_and_no_overwrite(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "init"]) == 0
    assert list(tmp_path.iterdir()) == []
    main(["--root", str(tmp_path), "init", "--write"])
    assert {p.name for p in tmp_path.iterdir()} == {"clients", "apis", "docs"}
    main(["--workspace", str(tmp_path), "import-sample", "acme"])
    assert list((tmp_path / "clients").iterdir()) == []
    main(["--workspace", str(tmp_path), "import-sample", "acme", "--write"])
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not (tmp_path / "clients" / "sample").exists()
    with pytest.raises(SystemExit):
        main(["--workspace", str(tmp_path), "import-sample", "acme", "--write"])
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_builtin_catalog_never_activates_sample(tmp_path):
    reader = WorkspaceReader(tmp_path)
    assert reader.catalog()["summary"]["clients"] == 0
    builtin = reader.catalog("builtin")
    assert builtin["scenarios"]
    assert builtin["clients"] == [] and builtin["summary"]["clients"] == 0
    assert all(item["usedBy"] == [] for item in builtin["scenarios"])
    assert reader.manifests() == []


def test_customer_html_and_instructions_are_not_trusted(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "tools").mkdir()
    for path in (tmp_path / "AGENTS.md", tmp_path / "skills" / "discovery.md",
                 tmp_path / "catalog" / "template.html",
                 tmp_path / "tools" / "build-facade.py"):
        path.write_text("MALICIOUS_CUSTOMER_OVERRIDE", encoding="utf-8")
    _, html = WorkspaceReader(tmp_path).dashboard()
    assert "--cp-bg" in html
    assert "MALICIOUS_CUSTOMER_OVERRIDE" not in html
    guide = guidance.workflow_guide("discovery")
    assert "MALICIOUS_CUSTOMER_OVERRIDE" not in json.dumps(guide)
    assert guide["procedure"]["content"] == (kit_root() / "skills" / "discovery.md").read_text("utf-8")
    with pytest.raises(ValueError):
        guidance.reference("../../credentials")


@pytest.mark.anyio
async def test_full_guides_same_in_tools_resources_and_prompts(tmp_path):
    async with Client(create_server(tmp_path).mcp, mode="legacy") as client:
        info = await client.call_tool("kit-info", {})
        assert "Non-negotiable" in info.structured_content["constitution"]["content"]
        for workflow in ("discovery", "onboarding", "lifecycle"):
            guide = await client.call_tool("workflow-guide", {"workflow": workflow})
            resource = await client.read_resource("kit://skills/" + workflow)
            prompt = await client.get_prompt(workflow)
            text = guide.structured_content["procedure"]["content"]
            assert text == resource.contents[0].text
            assert text in prompt.messages[0].content.text
            assert "python tools/" not in text and ".venv/bin/python" not in text
        result = await client.call_tool("kit-reference", {"name": "../../secrets"})
        assert result.is_error
        for tool in (await client.list_tools()).tools:
            assert tool.annotations.read_only_hint
            assert not tool.annotations.destructive_hint


def test_hardlinks_and_escaping_outputs_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_text("keep")
    linked = tmp_path / "linked"
    os.link(source, linked)
    with pytest.raises(ValueError, match="Hard-linked"):
        safe_data_path(tmp_path, linked)
    with pytest.raises(ValueError, match="escapes"):
        safe_data_path(tmp_path, tmp_path / ".." / "outside")


def test_generated_modules_trusted_and_selected_only(tmp_path, capsys):
    main(["--workspace", str(tmp_path), "import-sample", "acme", "--write"])
    main(["--workspace", str(tmp_path), "import-sample", "other", "--write"])
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "api-with-mcp.bicep").write_text("MALICIOUS")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build-facade.py").write_text("raise RuntimeError('MALICIOUS')")
    main(["--workspace", str(tmp_path), "build", "clients/acme"])
    generated = tmp_path / "clients" / "acme" / "generated"
    first = {str(p.relative_to(generated)): p.read_bytes() for p in generated.rglob("*") if p.is_file()}
    assert not (tmp_path / "clients" / "other" / "generated").exists()
    assert not (tmp_path / "infra").exists()
    assert first[str(Path("kit-modules") / "api-with-mcp.bicep")] == (kit_root() / "modules" / "api-with-mcp.bicep").read_bytes()
    main(["--workspace", str(tmp_path), "build", "clients/acme"])
    assert first == {str(p.relative_to(generated)): p.read_bytes() for p in generated.rglob("*") if p.is_file()}


def test_user_config_pins_installation_and_workspace(tmp_path, capsys):
    main(["--workspace", str(tmp_path), "vscode-config"])
    config = json.loads(capsys.readouterr().out)["servers"]["mcp-openapi-creator"]
    assert config["command"] == str(Path(sys.executable).absolute())
    assert config["args"] == ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", str(tmp_path)]
    assert list(tmp_path.iterdir()) == []


def test_installed_deployment_rejects_global_interpreter(tmp_path, monkeypatch):
    from mcp_openapi_creator_kit.runtime import command
    local = command("local_python")
    monkeypatch.setattr(local.sys, "prefix", sys.base_prefix)
    with pytest.raises(RuntimeError, match="dedicated virtual environment"):
        local.local_python(tmp_path)


def test_build_and_validate_cannot_escape_customer_root(tmp_path):
    for arguments in (["build", "../outside"], ["validate", "../outside"]):
        with pytest.raises(SystemExit):
            main(["--workspace", str(tmp_path), *arguments])


def test_target_report_rejects_hardlinked_customer_input(tmp_path):
    main(["--workspace", str(tmp_path), "import-sample", "acme", "--write"])
    manifest = tmp_path / "clients" / "acme" / "mcp-manifest.yaml"
    os.link(manifest, tmp_path / "linked-manifest")
    with pytest.raises(WorkspaceError, match="Hard-linked"):
        WorkspaceReader(tmp_path).target_report("acme")
