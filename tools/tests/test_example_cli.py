"""Installed CLI discovers and explicitly imports optional library data."""
import json

from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.workspace import WorkspaceReader


def test_builtin_catalog_exposes_full_library_without_active_clients(tmp_path, capsys):
    index = WorkspaceReader(tmp_path).catalog("builtin")
    assert len(index["scenarios"]) == 5
    assert sum(len(item["operations"]) for item in index["scenarios"]) == 18
    assert index["clients"] == [] and index["workflow"] == []
    assert all(not item["usedBy"] for item in index["scenarios"])
    assert main(["--workspace", str(tmp_path), "catalog", "--source", "builtin"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["scenarios"]) == 5 and output["clients"] == []
    assert not list(tmp_path.iterdir())


def test_example_cli_list_and_preview_do_not_activate_clients(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "examples"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["activeClients"] == [] and result["writes"] is False
    assert len(result["scenarios"]) == 2
    assert not list(tmp_path.iterdir())
    assert main(["--workspace", str(tmp_path), "import-example", "customer-care", "librarycase"]) == 0
    assert json.loads(capsys.readouterr().out)["writes"] is False
    assert not list(tmp_path.iterdir())
    assert main(["--workspace", str(tmp_path), "import-example", "customer-care", "librarycase", "--write"]) == 0
    assert json.loads(capsys.readouterr().out)["writes"] is True
    status = WorkspaceReader(tmp_path).status()
    assert [client["id"] for client in status["clients"]] == ["librarycase"]
    assert len(status["contracts"]) == 1
