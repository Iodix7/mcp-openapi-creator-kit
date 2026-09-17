from pathlib import Path
import json
import importlib.util
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
    assert triggers["push"]["branches"] == ["main"]
    assert "azure/login" not in text
    assert "azd provision" not in text
    assert "AZURE_SUBSCRIPTION_ID" not in text


def test_installed_package_acceptance_covers_windows_and_linux():
    job = load("ci.yml")["jobs"]["installed-package"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert job["strategy"]["fail-fast"] == "false"
    assert job["timeout-minutes"] == "25"
    steps = job["steps"]
    install = next(step for step in steps if "install isolated" in step.get("name", ""))
    assert "python -m venv .venv" in install["run"]
    assert "pip install -e" in install["run"]
    assert "RUNNER_TEMP" in install["run"]
    windows = next(step for step in steps if step.get("name") == "Windows test suite")
    assert windows["if"] == "runner.os == 'Windows'"
    smoke = next(step for step in steps if "runtime smoke" in step.get("name", ""))
    assert "tools/wheel-smoke.py" in smoke["run"]
    assert "--compile-bicep" in smoke["run"]
    release = next(step for step in steps if step.get("name") == "build one colleague release candidate")
    assert "tools/build-release.py" in release["run"] and "--apply" in release["run"]
    assert steps.index(release) < steps.index(smoke)
    for flag in ("--wheel", "--sdist", "--expected-sha256"):
        assert flag in smoke["run"]
    candidate = next(step for step in steps if step.get("name") == "retain colleague release candidate")
    assert "KIT_RELEASE_ARTIFACTS" in candidate["with"]["path"]
    assert steps.index(candidate) > steps.index(smoke)
    upload = next(step for step in steps if step.get("name") == "retain acceptance results")
    assert upload["if"] == "always()"
    assert "result.json" in upload["with"]["path"]
    # Model calls consume credits and are deliberately not a push/PR CI stage.
    text = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "github-copilot-sdk" not in text
    assert "agent_eval.py" not in text


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
    assert "MCP_RECONCILE_APPLY" not in text
    assert "reconcile-all.py --apply --skip-if-unprovisioned" in text
    assert "vars.PUBLISHER_EMAIL ||" not in text
    assert "AZURE_ENV_NAME PUBLISHER_EMAIL" in text
    assert "azure/login@v2" in text
    restore = text.split("restore_output()", 1)[1]
    assert '--resource-group "$AZURE_RESOURCE_GROUP"' not in restore
    assert "if ! output=\"$(az resource list" in restore
    assert "[?resourceGroup=='$AZURE_RESOURCE_GROUP'].name" in restore
    assert "exit 1" in restore


def test_azd_preview_cannot_apply_reconciliation_by_default():
    azure_yaml = (_REPO / "azure.yaml").read_text(encoding="utf-8")

    hook = (_REPO / "tools" / "preprovision.py").read_text(encoding="utf-8")
    assert "--apply" not in hook
    assert "--check-azure-resources" in hook
    assert "local_python(root)" in hook
    assert ".venv/bin/python tools/preprovision.py" in azure_yaml
    assert ".venv\\Scripts\\python.exe tools\\preprovision.py" in azure_yaml


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


def test_ci_enforces_publication_and_all_profiles():
    text = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "python tools/check-publication.py" in text
    assert "--report-only" not in text
    for profile in ("native-mcp", "rest-consumption", "policy-mcp-consumption"):
        assert f"--profile {profile}" in text
    assert "az bicep build --file platform/gateway.bicep --stdout" in text


def test_dependabot_monitors_root_package_dependencies():
    dependabot = load("../dependabot.yml")
    pip_directories = {
        update["directory"] for update in dependabot["updates"]
        if update["package-ecosystem"] == "pip"}

    assert {"/", "/tools"} <= pip_directories


def test_vscode_mcp_configs_are_explicit_local_isolated_launches():
    for name in ("mcp.json", "mcp.posix.json"):
        server = json.loads((_REPO / ".vscode" / name).read_text(encoding="utf-8"))["servers"]["mcp-openapi-creator"]
        assert server["command"] in {
            "${workspaceFolder}\\.venv\\Scripts\\python.exe",
            "${workspaceFolder}/.venv/bin/python",
        }
        assert server["args"][:3] == ["-I", "-m", "mcp_openapi_creator_kit"]
        assert server["type"] == "stdio"


def test_preprovision_preserves_local_python_and_never_applies(monkeypatch):
    monkeypatch.syspath_prepend(str(_REPO / "tools"))
    spec = importlib.util.spec_from_file_location("test_hook", _REPO / "tools" / "preprovision.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    calls = []
    monkeypatch.setenv("MCP_RECONCILE_APPLY", "true")
    monkeypatch.setattr(hook.subprocess, "run", lambda arguments, **kwargs: calls.append((arguments, kwargs)))
    hook.main()
    assert len(calls) == 5
    assert all(Path(args[0]).is_relative_to(_REPO / ".venv") for args, _ in calls)
    assert all(kwargs["check"] for _, kwargs in calls)
    assert calls[-1][0][1:] == ["tools/reconcile-all.py", "--skip-if-unprovisioned"]
    assert all("--apply" not in args and "--apply-if-env" not in args for args, _ in calls)


def test_public_sample_contains_no_retired_branding():
    text = (_REPO / "apis" / "customer-care" / "openapi.yaml").read_text(
        encoding="utf-8").lower()

    for retired in ("novaretail", "laura conti", "rm-eur", "po-nr", "roma eur"):
        assert retired not in text
