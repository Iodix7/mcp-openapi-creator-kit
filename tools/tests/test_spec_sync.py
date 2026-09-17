import json
import os

import pytest
import yaml

from mcp_openapi_creator_kit import cli, scenario
from test_scenario_consistency import snapshot
from test_workflow_progress import reader, spec


def narrative(root):
    path = root / "docs" / "fixture" / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf# Reviewed story\r\n\r\nStore 004; ask before rescheduling.\r\n")
    return path


def test_preview_creates_nothing_and_write_scaffolds_unapproved_draft(mcp_workspace, capsys):
    before = snapshot(mcp_workspace)
    assert cli.main(["--workspace", str(mcp_workspace), "spec-sync", "fixture"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["action"] == "create" and not plan["writes"]
    assert plan["diff"] and plan["narrativeReviewRequired"]
    assert snapshot(mcp_workspace) == before
    cli.main(["--workspace", str(mcp_workspace), "spec-sync", "fixture", "--write"])
    result = json.loads(capsys.readouterr().out)
    assert result["writes"]
    text = (mcp_workspace / "docs" / "fixture" / "spec.md").read_text("utf-8")
    assert "Status: Draft" in text and scenario.BLOCK_START in text
    check = scenario.check_text(text, scenario.inventory(mcp_workspace, "fixture"))
    assert check.status == "mismatch" and "TO CLARIFY" in check.issues[0].message
    assert not (mcp_workspace / ".mcp-kit").exists()


def test_sync_preserves_narrative_bytes_and_is_idempotent(mcp_workspace, monkeypatch):
    path = narrative(mcp_workspace)
    before = path.read_bytes()
    plan = scenario.plan_spec_sync(mcp_workspace, "fixture")
    assert scenario.apply_spec_sync(mcp_workspace, plan)
    assert path.read_bytes().startswith(before)
    text = path.read_bytes().decode("utf-8")
    assert "\r\n" in text and scenario.check_text(text, scenario.inventory(mcp_workspace, "fixture")).status == "consistent"
    original = path.read_bytes()
    plan = scenario.plan_spec_sync(mcp_workspace, "fixture")
    monkeypatch.setattr(scenario.os, "replace", lambda *_: pytest.fail("No-op rewrote file"))
    assert not plan.changed and not scenario.apply_spec_sync(mcp_workspace, plan)
    assert path.read_bytes() == original


@pytest.mark.parametrize("before", [b"", b" \r\n", b"\xef\xbb\xbf"])
def test_empty_existing_spec_remains_an_unapproved_draft(mcp_workspace, before):
    path = narrative(mcp_workspace)
    path.write_bytes(before)
    scenario.apply_spec_sync(mcp_workspace, scenario.plan_spec_sync(mcp_workspace, "fixture"))
    assert path.read_bytes().startswith(before)
    check = scenario.check_spec(mcp_workspace, "fixture", scenario.inventory(mcp_workspace, "fixture"))
    assert check.status == "mismatch" and "TO CLARIFY" in check.issues[0].message


def test_source_change_stales_block_and_sync_preserves_prefix_and_suffix(mcp_workspace):
    path = narrative(mcp_workspace)
    scenario.apply_spec_sync(mcp_workspace, scenario.plan_spec_sync(mcp_workspace, "fixture"))
    path.write_bytes(path.read_bytes() + b"\r\n## Decisions\r\nKeep this exact ending.")
    text = path.read_bytes().decode("utf-8")
    start, end = scenario.managed_span(text)
    prefix, suffix = text[:start], text[end:]
    source = mcp_workspace / "apis" / "customer-care" / "openapi.yaml"
    doc = yaml.safe_load(source.read_text("utf-8"))
    doc["paths"]["/v1/customer-context"]["get"]["responses"]["200"]["description"] += " Updated."
    source.write_text(yaml.safe_dump(doc), "utf-8")
    records = scenario.inventory(mcp_workspace, "fixture")
    assert "stale" in scenario.check_text(text, records).issues[0].message
    plan = scenario.plan_spec_sync(mcp_workspace, "fixture")
    assert scenario.apply_spec_sync(mcp_workspace, plan)
    updated = path.read_bytes().decode("utf-8")
    start, end = scenario.managed_span(updated)
    assert updated[:start] == prefix and updated[end:] == suffix
    assert scenario.check_text(updated, records).status == "consistent"


@pytest.mark.parametrize("mutation", ["narrative", "contract"])
def test_stale_plan_never_overwrites_concurrent_edits(mcp_workspace, mutation):
    path = narrative(mcp_workspace)
    plan = scenario.plan_spec_sync(mcp_workspace, "fixture")
    if mutation == "narrative":
        path.write_bytes(path.read_bytes() + b"Concurrent user edit")
    else:
        source = mcp_workspace / "apis" / "customer-care" / "openapi.yaml"
        doc = yaml.safe_load(source.read_text("utf-8"))
        doc["paths"]["/v1/customer-context"]["get"]["summary"] = "Changed since planning"
        source.write_text(yaml.safe_dump(doc), "utf-8")
    before = snapshot(mcp_workspace)
    with pytest.raises(ValueError, match="changed since preview"):
        scenario.apply_spec_sync(mcp_workspace, plan)
    assert snapshot(mcp_workspace) == before


@pytest.mark.parametrize("broken", [
    scenario.BLOCK_START, scenario.BLOCK_END,
    scenario.BLOCK_END + "\n" + scenario.BLOCK_START,
    scenario.BLOCK_START + "\n" + scenario.BLOCK_START + "\n" + scenario.BLOCK_END,
    "```markdown\n" + scenario.BLOCK_START + "\n" + scenario.BLOCK_END + "\n```\n",
    "prefix " + scenario.BLOCK_START + "\n" + scenario.BLOCK_END,
])
def test_ambiguous_markers_are_not_repaired_or_overwritten(mcp_workspace, broken):
    path = narrative(mcp_workspace)
    path.write_text(broken, "utf-8")
    before = snapshot(mcp_workspace)
    with pytest.raises(scenario.SpecSyncConflict):
        scenario.plan_spec_sync(mcp_workspace, "fixture")
    assert scenario.check_text(broken, scenario.inventory(mcp_workspace, "fixture")).status == "mismatch"
    assert snapshot(mcp_workspace) == before


def test_legacy_technical_tables_require_deliberate_migration(mcp_workspace):
    path = spec(mcp_workspace)
    before = snapshot(mcp_workspace)
    with pytest.raises(scenario.SpecSyncConflict, match="Unmanaged technical table"):
        scenario.plan_spec_sync(mcp_workspace, "fixture")
    assert scenario.scenario_report(mcp_workspace, "fixture")["check"]["status"] == "consistent"
    assert "conflict" in scenario.scenario_report(mcp_workspace, "fixture")["specSync"]
    assert snapshot(mcp_workspace) == before and path.exists()


@pytest.mark.parametrize("fence", ["```markdown", "~~~"])
def test_unclosed_narrative_fence_cannot_hide_the_generated_block(mcp_workspace, fence):
    path = narrative(mcp_workspace)
    path.write_text("# Notes\n\n" + fence + "\nUnfinished code example\n", "utf-8")
    before = snapshot(mcp_workspace)
    with pytest.raises(scenario.SpecSyncConflict, match="outside code fences"):
        scenario.plan_spec_sync(mcp_workspace, "fixture")
    assert snapshot(mcp_workspace) == before


def test_safe_paths_size_limits_and_atomic_failure_leave_original(mcp_workspace, monkeypatch):
    path = narrative(mcp_workspace)
    os.link(path, mcp_workspace / "alias")
    with pytest.raises(ValueError, match="Hard-linked"):
        scenario.plan_spec_sync(mcp_workspace, "fixture")
    (mcp_workspace / "alias").unlink()
    plan = scenario.plan_spec_sync(mcp_workspace, "fixture")
    before = snapshot(mcp_workspace)
    def fail(*_):
        raise OSError("synthetic atomic replacement failure")
    monkeypatch.setattr(scenario.os, "replace", fail)
    with pytest.raises(OSError, match="synthetic"):
        scenario.apply_spec_sync(mcp_workspace, plan)
    assert snapshot(mcp_workspace) == before
    path.write_bytes(b"x" * scenario.MAX_SPEC_BYTES)
    with pytest.raises(scenario.SpecSyncConflict, match="No content was truncated"):
        scenario.plan_spec_sync(mcp_workspace, "fixture")
    assert path.stat().st_size == scenario.MAX_SPEC_BYTES


def test_generated_block_does_not_hide_bad_narrative_tool_references(mcp_workspace):
    path = narrative(mcp_workspace)
    path.write_bytes(path.read_bytes() + b"\r\n- Tool: `invented-call`\r\n")
    scenario.apply_spec_sync(mcp_workspace, scenario.plan_spec_sync(mcp_workspace, "fixture"))
    check = scenario.check_spec(mcp_workspace, "fixture", scenario.inventory(mcp_workspace, "fixture"))
    assert check.status == "mismatch"
    assert any("invented-call" in item.message for item in check.issues)


def test_narrative_story_mapping_table_is_not_adopted_or_rejected(mcp_workspace):
    path = narrative(mcp_workspace)
    path.write_bytes(path.read_bytes() + b"\r\n| User story | Operation | Purpose |\r\n"
                     b"|---|---|---|\r\n| Look up | get-customer-context | Explain delay |\r\n")
    before = path.read_bytes()
    scenario.apply_spec_sync(mcp_workspace, scenario.plan_spec_sync(mcp_workspace, "fixture"))
    assert path.read_bytes().startswith(before)


def test_workflow_proposes_exact_sync_then_prepare_without_azure(mcp_workspace, monkeypatch):
    workspace = reader(mcp_workspace, monkeypatch)
    status = workspace.workflow_status("fixture")
    assert status.next_stage == "define-scenario"
    assert status.next_invocation.cli_arguments == ["spec-sync", "fixture", "--write"]
    assert status.next_invocation.effect == "local-write"
    cli.main(["--workspace", str(mcp_workspace), "spec-sync", "fixture", "--write"])
    status = workspace.workflow_status("fixture")
    assert status.next_stage == "define-scenario" and status.next_invocation is None
    path = mcp_workspace / "docs" / "fixture" / "spec.md"
    text = path.read_text("utf-8")
    start, _ = scenario.managed_span(text)
    path.write_text("# Reviewed Store 004 story; confirm before any write.\n\n" + text[start:], "utf-8")
    status = workspace.workflow_status("fixture")
    assert status.next_stage == "prepare"
    cli.main(["--workspace", str(mcp_workspace), "prepare", "clients/fixture", "--profile", "native-mcp"])
    status = workspace.workflow_status("fixture")
    assert status.completion == "preview-required"
    assert status.approval_status == "not-granted"
