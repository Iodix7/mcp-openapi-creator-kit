import asyncio
import json
import os
import sys

import pytest
from mcp import Client

from mcp_openapi_creator_kit import cli, scenario
from mcp_openapi_creator_kit.server import create_server
from test_workflow_progress import spec, reader


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_reference_matches_imported_prefixed_contract_without_writes(tmp_path):
    cli.main(["--workspace", str(tmp_path), "import-sample", "acme", "--write"])
    before = snapshot(tmp_path)
    report = scenario.scenario_report(tmp_path, "acme")
    assert report["check"]["status"] == "missing"
    assert len(report["operations"]) == 6
    assert all(op["operationId"].startswith("acme-") for op in report["operations"])
    assert "GET /v1/customer-context" in report["referenceMarkdown"]
    assert "202" in report["referenceMarkdown"]
    assert scenario.check_text(report["referenceMarkdown"], report["operations"]).status == "consistent"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("mutation,expected", [
    ("route", "expected 'GET /v1/customer-context'"),
    ("prefix", "Unknown or ambiguous operation"),
    ("status", "declared response status"),
    ("example", "unknown response example"),
    ("tool", "Unknown or ambiguous operation"),
    ("missing-table", "Missing mocks table"),
    ("missing-operation", "Missing Operations row"),
    ("duplicate", "Duplicate Operations row"),
    ("separator", "Malformed scenario table header"),
    ("fenced", "Missing operations table"),
])
def test_actual_beta_failures_and_malformed_assertions_are_rejected(mcp_workspace, mutation, expected):
    records = scenario.inventory(mcp_workspace, "fixture")
    text = scenario.reference_markdown(records)
    op = next(item["operationId"] for item in records if item["selected"])
    if mutation == "route":
        text = text.replace("GET /v1/customer-context", "GET /customers/{customerId}/context")
    elif mutation == "prefix":
        text = text.replace(op, "invented-" + op)
    elif mutation == "status":
        text = text.replace("| 202 |", "| 201 invented success |")
    elif mutation == "example":
        text = text.replace("| 202 |", "| 202 `nonexistent` |")
    elif mutation == "tool":
        text += "\n- Tool after confirmation: `invented-write`\n"
    elif mutation == "missing-table":
        text = text.split("## Mock behavior")[0]
    elif mutation == "missing-operation":
        text = "\n".join(line for line in text.splitlines() if f"`{op}` | `" not in line)
    elif mutation == "duplicate":
        line = next(line for line in text.splitlines() if f"`{op}` | `" in line)
        text = text.replace(line, line + "\n" + line)
    elif mutation == "separator":
        text = text.replace("|---|---|---|", "|x|x|x|")
    else:
        text = "```markdown\n" + text + "```\n"
    result = scenario.check_text(text, records)
    assert result.status == "mismatch"
    assert any(expected in issue.message for issue in result.issues)


def test_rest_duplicate_ids_require_contract_and_unselected_operations_remain_optional():
    records = [
        {"contract": name, "operationId": "get-item", "method": "GET", "path": "/" + name,
         "selected": False, "responses": [{"status": "200", "examples": []}]}
        for name in ("first", "second")
    ]
    text = scenario.reference_markdown(records)
    assert scenario.check_text(text, records).status == "consistent"
    ambiguous = text.replace("| Contract ", "| Extra ").replace("`first`", "")
    assert scenario.check_text(ambiguous, records).status == "mismatch"
    records[0]["selected"] = True
    assert scenario.check_text(scenario.reference_markdown(records[:1]), records).status == "consistent"
    records[0]["path"] = "/escaped|pipe"
    assert scenario.check_text(scenario.reference_markdown(records[:1]), records).status == "consistent"


def test_example_names_and_freeform_limit_are_explicit(mcp_workspace):
    records = scenario.inventory(mcp_workspace, "fixture")
    response = next(response for response in records[0]["responses"] if response["examples"])
    text = scenario.reference_markdown(records)
    line = f"| `{records[0]['contract']}` | `{records[0]['operationId']}` | Review actual examples and x-mock | {response['status']} |"
    text = text.replace(line, line[:-1] + f"`{response['examples'][0]['name']}` |")
    check = scenario.check_text(text + "\nUnverified free-form business claim.\n", records)
    assert check.status == "consistent"
    assert "do not validate free-form prose" in check.notice


def test_separate_method_path_columns_validate_routes_not_only_template_shape(mcp_workspace):
    records = scenario.inventory(mcp_workspace, "fixture")
    text = scenario.reference_markdown(records)
    text = text.replace("| Method and path |", "| Method | Path |").replace(
        "|---|---|---|\n", "|---|---|---|---|\n", 1)
    for method in ("GET", "POST"):
        text = text.replace(f"`{method} /", f"`{method}` | `/")
    assert scenario.check_text(text, records).status == "consistent"
    wrong = text.replace("/v1/customer-context", "/invented")
    issues = scenario.check_text(wrong, records).issues
    assert any("expected 'GET /v1/customer-context'" in item.message for item in issues)
    assert not any("Missing operations table" in item.message for item in issues)


def test_prepare_rejects_bad_spec_before_outputs_and_live_workflow_disallows_preview(mcp_workspace, monkeypatch):
    workspace = reader(mcp_workspace, monkeypatch)
    path = spec(mcp_workspace)
    path.write_text(path.read_text("utf-8").replace("GET /v1/customer-context", "GET /invented"), "utf-8")
    monkeypatch.setattr(cli, "invoke", lambda name, *_: pytest.fail("Build ran") if name != "validate" else None)
    with pytest.raises(SystemExit) as error:
        cli.main(["--workspace", str(mcp_workspace), "prepare", "clients/fixture", "--profile", "native-mcp"])
    assert error.value.code == 2
    state = workspace.workflow_status("fixture")
    assert state.current_step.stage == "define-scenario"
    assert state.completion == "local-incomplete"
    assert not state.next_command and state.next_invocation is None
    assert "deploy-preview" not in state.allowed_actions
    assert "expected 'GET /v1/customer-context'" in next(s.detail for s in state.steps if s.id == "specification")


def test_prepare_requires_spec_but_plain_contract_build_remains_supported(mcp_workspace):
    cli.main(["--workspace", str(mcp_workspace), "build", "clients/fixture"])
    before = snapshot(mcp_workspace / "clients" / "fixture" / "generated")
    with pytest.raises(SystemExit):
        cli.main(["--workspace", str(mcp_workspace), "prepare", "clients/fixture", "--profile", "native-mcp"])
    assert snapshot(mcp_workspace / "clients" / "fixture" / "generated") == before


def test_safe_paths_and_size_limit(mcp_workspace):
    path = spec(mcp_workspace)
    os.link(path, mcp_workspace / "alias")
    with pytest.raises(ValueError, match="Hard-linked"):
        scenario.scenario_report(mcp_workspace, "fixture")
    (mcp_workspace / "alias").unlink()
    path.write_text("x" * (scenario.MAX_SPEC_BYTES + 1), "utf-8")
    assert "128 KiB" in scenario.scenario_report(mcp_workspace, "fixture")["check"]["issues"][0]["message"]
    with pytest.raises(ValueError, match="slug"):
        scenario.scenario_report(mcp_workspace, "../fixture")


def test_cli_mcp_and_report_are_identical_and_read_only(mcp_workspace, capsys):
    before = snapshot(mcp_workspace)
    assert cli.main(["--workspace", str(mcp_workspace), "scenario-contract", "fixture"]) == 2
    output = json.loads(capsys.readouterr().out)
    async def exercise():
        runtime = create_server(mcp_workspace)
        async with Client(runtime.mcp, mode="legacy") as client:
            result = await client.call_tool("scenario-contract", {"client": "fixture"})
            assert not result.is_error
            assert result.structured_content == output
    asyncio.run(exercise())
    assert snapshot(mcp_workspace) == before


def test_next_invocation_has_installed_python_exact_args_and_separate_permission(mcp_workspace, monkeypatch):
    workspace = reader(mcp_workspace, monkeypatch)
    spec(mcp_workspace)
    value = workspace.workflow_status("fixture")
    invocation = value.next_invocation
    assert invocation.executable == sys.executable
    assert invocation.arguments == ["-I", "-m", "mcp_openapi_creator_kit.cli", *value.next_command[1:]]
    assert invocation.cli_arguments == ["prepare", "clients/fixture", "--profile", "native-mcp"]
    assert invocation.cwd == str(mcp_workspace)
    assert invocation.requires_operator_approval and invocation.effect == "local-write"
    assert value.approval_status == "not-granted"
