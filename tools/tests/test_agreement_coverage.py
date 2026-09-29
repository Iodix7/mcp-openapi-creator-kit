import copy
import json

import pytest
import yaml

from mcp_openapi_creator_kit.coverage import coverage_report
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.workspace import WorkspaceReader
from test_rest_runtime import PATH, agreement, write_agreement
from test_rest_runtime_v2 import agreement_v2


def scope(root, requirements=(), intent="demo"):
    directory = root / "docs" / "demo"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.md").write_text("---\n" + yaml.safe_dump(
        {"coverage": {"intent": intent, "requirements": list(requirements)}}) + "---\n# Demo\n", encoding="utf-8")


def test_get_demo_coverage_is_automatic_read_only_and_honest(tmp_path):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    scope(tmp_path)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    report = coverage_report(tmp_path, "demo")
    assert report["preflight"]["technicalIssues"] == []
    assert report["preflight"]["status"] == "passed"
    assert report["demo"]["status"] == "planned-for-agreed-scope"
    assert not report["demo"]["readyLive"] and not report["fullAgreement"]["complete"]
    assert report["summary"]["decisions"] == []
    assert all(r["source"] and r["reason"] and r["verification"]["checks"] for r in report["requirements"])
    assert all(r["verification"]["status"] == "not-run" for r in report["requirements"])
    assert before == sorted(str(p) for p in tmp_path.rglob("*"))
    assert report == coverage_report(tmp_path, "demo")


def test_post_demo_body_is_gateway_but_write_is_simulated(tmp_path):
    spec, manifest = agreement_v2()
    write_agreement(tmp_path, spec, manifest)
    scope(tmp_path)
    report = coverage_report(tmp_path, "demo")
    assert report["preflight"]["technicalIssues"] == []
    body = next(r for r in report["requirements"] if r["operationId"] == "post-thing" and r["id"].endswith("/body"))
    assert body["planned"] == "gateway"
    assert body["source"].endswith("/post/requestBody")
    assert any("no persistence" in s for s in report["summary"]["limitations"])
    assert len(report["summary"]["limitations"]) == len(set(report["summary"]["limitations"]))


def test_state_and_callback_require_one_scope_decision_not_fake_state(tmp_path):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    requirements = [
        {"id": kind, "source": f"original-ia.md#{kind}", "description": text, "kind": kind, "reviewed": True}
        for kind, text in (("persistence", "Create and actually read the new record."),
                           ("callback", "Deliver a completion callback."))]
    scope(tmp_path, requirements)
    report = coverage_report(tmp_path, "demo")
    assert report["demo"]["status"] == "scope-decision-needed"
    assert len(report["summary"]["decisions"]) == 2
    assert report["preflight"]["status"] == "passed"  # Capability gaps are not technical errors.
    for item in requirements:
        item.update(scope="out", decision="User requested a stateless lookup-only demo; creation/callbacks excluded.")
    scope(tmp_path, requirements)
    report = coverage_report(tmp_path, "demo")
    assert report["demo"]["status"] == "planned-for-agreed-scope"
    assert report["summary"]["decisions"] == []
    assert not report["fullAgreement"]["complete"]
    assert len([r for r in report["requirements"] if not r["inDemoScope"]]) == 2


def test_profile_failure_cannot_be_hidden_by_demo_scope(tmp_path):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    scope(tmp_path)
    report = coverage_report(tmp_path, "demo", "policy-mcp-consumption")
    assert report["preflight"]["status"] == "blocked"
    assert report["demo"]["status"] == "technical-blocker"
    assert all(r["planned"] != "gateway" for r in report["requirements"])


def test_existing_specs_need_no_new_fields_or_questions(tmp_path):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    report = coverage_report(tmp_path, "demo")
    assert report["intent"] == "unspecified"
    assert report["summary"]["decisions"] == []
    assert report["preflight"]["status"] == "passed"


def test_cli_and_dashboard_use_same_report(tmp_path, capsys):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    scope(tmp_path)
    assert main(["--workspace", str(tmp_path), "coverage", "demo", "--profile", "rest-consumption"]) == 0
    cli = json.loads(capsys.readouterr().out)
    reader = WorkspaceReader(tmp_path)
    assert cli == reader.agreement_coverage("demo", "rest-consumption")
    index, html = reader.dashboard()
    assert index["clients"][0]["coverage"] == cli
    assert "coverage-dialog" in html
    assert "--cp-surface" in html


def test_invalid_scope_never_claims_approval(tmp_path):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    scope(tmp_path, [{"id": "state", "source": "ia#1", "description": "State", "kind": "persistence", "scope": "out"}])
    report = coverage_report(tmp_path, "demo")
    assert report["preflight"]["status"] == "blocked"
    assert "recorded scope decision" in report["preflight"]["technicalIssues"][0]
    assert not report["fullAgreement"]["complete"]


def test_missing_contract_is_an_explicit_read_only_report(tmp_path, capsys):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    (tmp_path / "apis" / "things" / "openapi.yaml").unlink()
    assert main(["--workspace", str(tmp_path), "coverage", "demo"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report == coverage_report(tmp_path, "demo")
    assert report["preflight"]["status"] == "blocked"
    assert report["evidence"]["offline"] == "failed"
    assert not report["requirements"] and not report["writes"]


def test_failed_coverage_does_not_record_preparation(tmp_path, capsys):
    from mcp_openapi_creator_kit import progress
    from mcp_openapi_creator_kit.scenario import inventory, reference_markdown
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    scope(tmp_path, [{"id": "state", "source": "ia#1", "description": "State",
                     "kind": "persistence", "scope": "out"}])
    path = tmp_path / "docs" / "demo" / "spec.md"
    path.write_text(path.read_text("utf-8") + reference_markdown(inventory(tmp_path, "demo")), "utf-8")
    with pytest.raises(SystemExit) as error:
        main(["--workspace", str(tmp_path), "prepare", "clients/demo", "--profile", "rest-consumption"])
    assert error.value.code == 2
    assert "recorded scope decision" in capsys.readouterr().out
    receipt = progress.read_step(tmp_path, "demo", "prepare", progress.input_digest(tmp_path, "demo"),
                                 profile="rest-consumption")
    assert receipt["status"] != "recorded-current"
    assert not (tmp_path / "clients" / "demo" / "generated").exists()


def test_source_fingerprint_changes_with_ia_text(tmp_path):
    spec, manifest = agreement()
    write_agreement(tmp_path, spec, manifest)
    before = coverage_report(tmp_path, "demo")["sourceHash"]
    scope(tmp_path)
    assert coverage_report(tmp_path, "demo")["sourceHash"] != before
