"""Catalog workflow snapshots use the shared evaluator and trusted, read-only UI."""
from html.parser import HTMLParser
import json
import socket
import subprocess

import pytest
import yaml

from mcp_openapi_creator_kit import catalog
from mcp_openapi_creator_kit.assets import asset_text
from mcp_openapi_creator_kit.workflow import evaluate_workflow
from mcp_openapi_creator_kit.guidance import with_current_step


def write_manifest(root, client, **overrides):
    manifest = {
        "client": client,
        "displayName": f"Client {client}",
        "networkProfile": "public",
        "apis": [{
            "name": "example-api", "backend": {"mode": "mock"},
            "mcpTools": ["get-example"],
        }],
        **overrides,
    }
    path = root / "clients" / client / "mcp-manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return path, manifest


@pytest.mark.parametrize("profile", [
    "native-mcp", "policy-mcp-consumption", "rest-consumption",
])
def test_catalog_manifest_profile_is_not_gateway_evidence(tmp_path, monkeypatch, profile):
    path, manifest = write_manifest(tmp_path, "customer", gatewayProfile=profile)
    forged = {
        "evidenceId": "caller-supplied", "evidenceStatus": "verified",
        "profile": profile, "status": "ready-for-preview",
        "facts": {"tier": "Consumption"},
    }
    manifest["observation"] = forged
    manifest["workflow"] = forged
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (path.parent / "gateway-evidence.json").write_text(
        json.dumps(forged), encoding="utf-8")

    def forbidden_io(*args, **kwargs):
        pytest.fail("Offline catalog must not execute commands or connect to Azure")

    monkeypatch.setattr(subprocess, "run", forbidden_io)
    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    index = catalog.build_index(tmp_path)
    result, = index["workflow"]

    assert result == with_current_step(evaluate_workflow(manifest)).model_dump(mode="json", by_alias=True)
    assert result["client"] == "customer"
    assert result["status"] == "needs-input"
    assert result["profile"] is None
    assert result["evidence"] is None
    assert result["evidenceStatus"] == "missing"
    assert {"requires_mcp", "gateway_mode"} <= set(result["missingInputs"])
    assert "deploy-preview" not in result["allowedActions"]
    assert result["approvalStatus"] == "not-granted"
    assert result["checksPending"]
    assert catalog.build_index(tmp_path) == index


def test_catalog_evaluates_each_actual_manifest_once_and_preserves_old_surfaces(
        tmp_path, monkeypatch):
    second_path, second = write_manifest(tmp_path, "z-client")
    first_path, first = write_manifest(tmp_path, "a-client")
    reads, calls = [], []
    original_read = catalog.read_yaml

    def track_read(path):
        reads.append(path)
        return original_read(path)

    def track_evaluation(*args, **kwargs):
        calls.append((args, kwargs))
        return evaluate_workflow(*args, **kwargs)

    monkeypatch.setattr(catalog, "read_yaml", track_read)
    monkeypatch.setattr(catalog, "evaluate_workflow", track_evaluation)
    index = catalog.build_index(tmp_path)

    assert calls == [((first,), {}), ((second,), {})]
    assert reads.count(first_path) == reads.count(second_path) == 1
    assert [item["client"] for item in index["workflow"]] == ["a-client", "z-client"]
    assert index["formatVersion"] == "1.0"
    assert index["summary"]["clients"] == 2
    assert {"profiles", "targetCapabilities", "summary", "scenarios", "clients",
            "canonicalSchemas", "warnings"} <= index.keys()
    usages, clients = catalog.load_usages(tmp_path)
    assert clients == index["clients"]
    assert [item["client"] for item in usages["example-api"]] == ["a-client", "z-client"]
    assert "workflow" not in clients[0]


def test_empty_workspace_does_not_promote_builtin_clients(tmp_path):
    index, html = catalog.render_outputs(tmp_path)
    assert index["clients"] == []
    assert index["workflow"] == []
    assert index["summary"]["clients"] == 0
    assert "No client workflow snapshot available" in html
    assert "const workflows=C.workflow||[]" in html


class ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.inside_script = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.inside_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside_script = False

    def handle_data(self, data):
        if self.inside_script:
            self.scripts[-1] += data


@pytest.mark.parametrize("draft", [False, True])
def test_workflow_json_round_trips_hostile_strings_without_script_injection(tmp_path, draft):
    _, manifest = write_manifest(tmp_path, "customer")
    status = evaluate_workflow(None if draft else manifest).model_dump(
        mode="json", by_alias=True)
    hostile = '</ScRiPt><script>alert("injected")</script><img src=x onerror=alert(1)>&\'\u2028\u2029'
    status.update({
        "client": None if draft else hostile,
        "reason": hostile, "nextAction": hostile,
        "missingInputs": [hostile], "blockers": [hostile],
        "allowedActions": [hostile], "checksPending": [hostile],
        "completion": hostile, "nextCommand": [hostile], "progressNotice": hostile,
        "steps": [{"id": hostile, "status": hostile, "detail": hostile, "plan": {"value": hostile}}],
        "currentStep": {"stage": hostile, "why": [hostile], "instructions": hostile,
                        "consent": hostile, "completion": hostile,
                        "source": {"asset": hostile, "version": hostile, "resourceUri": hostile}},
        "evidence": {
            "evidenceId": hostile, "observedAt": hostile, "expiresAt": hostile,
            "target": {name: hostile for name in (
                "account", "tenant", "subscription", "resourceGroup", "apimName")},
            "facts": {"tier": hostile},
        },
    })
    index = catalog.build_index(tmp_path)
    index["workflow"] = [status]
    html = catalog.render_index(index)
    parser = ScriptCollector()
    parser.feed(html)

    assert len(parser.scripts) == 3
    assert "__CATALOG_DATA__" not in html
    assert hostile not in html
    embedded, = [script for script in parser.scripts if script.startswith("window.CATALOG=")]
    data = json.loads(embedded.removeprefix("window.CATALOG=").removesuffix(";"))
    assert data["workflow"] == [status]
    assert "\\u003c/ScRiPt>" in embedded
    assert "\\u2028\\u2029" in embedded


def test_workflow_template_renders_shared_fields_as_escaped_read_only_text():
    template = asset_text("catalog/template.html")
    rendering = template.split("function workflowItems(", 1)[1].split("init();", 1)[0]
    for expression in (
        'esc(w.client??tx("draftClient"))', "esc(tx(w.status))", "esc(w.reason)",
        'esc(w.profile??tx("notConfirmed"))', 'esc(w.provisionalProfile??tx("notSelected"))',
        "esc(tx(w.approvalStatus))", "esc(tx(w.evidenceStatus))", "esc(w.nextAction)",
        "esc(tx(item))", 'esc(value??tx("notProvided"))',
        "esc(tx(step.id))", "esc(tx(step.status))", "esc(step.detail)",
        "esc(JSON.stringify(step.plan,null,2))", "esc(JSON.stringify(w.nextCommand,null,2))",
        "esc(tx(w.currentStep.stage))", "esc(w.currentStep.instructions)", "esc(w.currentStep.consent)",
        "esc(w.currentStep.completion)", "esc(w.currentStep.source.asset)",
        "esc(w.currentStep.source.version)", "esc(w.currentStep.source.resourceUri)",
    ):
        assert expression in rendering
    for field in ("missingInputs", "blockers", "allowedActions", "checksPending"):
        assert f",w.{field})" in rendering
    for field in ("evidence.evidenceId", "evidence.observedAt", "evidence.expiresAt",
                  "target.account", "target.tenant", "target.subscription",
                  "target.resourceGroup", "target.apimName", "facts.tier"):
        assert field in rendering
    for phrase in ("Confirmed profile", "Provisional profile (unverified)",
                   "Missing inputs", "Blockers", "Gateway evidence", "Next action",
                   "not deployment approval", "Evidence may expire",
                   "dashboard-refresh", "static catalogs must be regenerated",
                   "Refreshing this view does not inspect Azure"):
        assert phrase in template
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "eval(",
                      "new Function", "Date.now(", "evaluate_workflow", ".compatibility"):
        assert forbidden not in rendering
    assert '$("workflow-dialog").showModal()' in rendering
    assert "aria-labelledby=\"workflow-title\"" in template
    assert ".toolbar .button.workflow-button{display:inline-block}" in template
    assert "grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr))" in template
    assert "--cp-bg: #f7f4ef" in template
    assert "background:var(--cp-surface)" in template
