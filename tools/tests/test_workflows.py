from pathlib import Path
import re

import yaml

_REPO = Path(__file__).resolve().parent.parent.parent
_WORKFLOWS = _REPO / ".github" / "workflows"


def load(name):
    return yaml.load((_WORKFLOWS / name).read_text(encoding="utf-8"),
                     Loader=yaml.BaseLoader)


def test_ci_is_offline_and_fork_safe():
    workflow = load("ci.yml")
    triggers = workflow["on"]
    text = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert {"push", "pull_request", "workflow_dispatch", "workflow_call"} <= set(triggers)
    assert "azure/login" not in text
    assert "azd provision" not in text
    assert "AZURE_SUBSCRIPTION_ID" not in text


def test_azure_smoke_is_manual_and_uses_fork_environment():
    workflow = load("azure-smoke.yml")
    triggers = workflow["on"]
    deploy = workflow["jobs"]["deploy"]
    text = (_WORKFLOWS / "azure-smoke.yml").read_text(encoding="utf-8")

    assert set(triggers) == {"workflow_dispatch"}
    assert deploy["environment"] == "azure-smoke"
    assert "confirm_subscription" in triggers["workflow_dispatch"]["inputs"]
    assert "azd provision --preview --no-prompt" in text
    assert "azd provision --no-prompt" in text
    assert 'MCP_RECONCILE_APPLY: "true"' in text
    assert "vars.PUBLISHER_EMAIL ||" not in text
    assert "AZURE_ENV_NAME PUBLISHER_EMAIL" in text
    assert "azure/login@v2" in text


def test_azd_preview_cannot_apply_reconciliation_by_default():
    azure_yaml = (_REPO / "azure.yaml").read_text(encoding="utf-8")

    assert "reconcile-all.py --apply-if-env --skip-if-unprovisioned" in azure_yaml
    assert "reconcile-all.py --apply --skip-if-unprovisioned" not in azure_yaml


def test_workflows_contain_no_upstream_azure_target():
    text = "\n".join(path.read_text(encoding="utf-8")
                     for path in _WORKFLOWS.glob("*.yml"))

    guid = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
    assert not guid.search(text)
    assert "/subscriptions/" not in text
    assert "/resourceGroups/" not in text


def test_ci_rejects_tracked_generated_artifacts():
    text = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "Generated artifacts must not be committed" in text
    assert "catalog/generated/" in text
    assert "clients/[^/]+/generated/" in text
    assert "infra/[^/]+\\.gen\\.bicep" in text
