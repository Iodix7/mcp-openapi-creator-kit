"""Regress the observed nested DNS failure without leaking provider payloads."""
import json
import sys
from types import SimpleNamespace

import pytest

from mcp_openapi_creator_kit.diagnostics import (
    MARKER, classify, failure_record, process_error,
)
from mcp_openapi_creator_kit.runtime import child_command
from test_deploy_encoding import deploy


@pytest.mark.parametrize("payload,category", [
    (b"Failed to resolve 'management.azure.com' ([Errno 11001] getaddrinfo failed) PRIVATE_KEY", "dns"),
    (b"ERROR: (AuthorizationFailed) PRIVATE_TOKEN", "authentication"),
    (b"ERROR: (InvalidTemplateDeployment) PRIVATE_TEMPLATE", "arm-validation"),
    (b"connection timed out PRIVATE_POLICY", "timeout"),
    (b"unrecognized error PRIVATE_KEY", "unknown"),
])
def test_allowlisted_category_no_raw_content(payload, category):
    value = classify(["az", "deployment", "group", "list", "--subscription", "PRIVATE_ID"],
                     b"PRIVATE_STDOUT", payload, 1)
    public = value.public()
    assert public["category"] == category
    assert public["command"] == "az.deployment.group.list"
    assert public["outcome"] == "read-failed"
    assert public["scope"] == "failing-command-only"
    assert public["automaticRetry"] is False
    assert "PRIVATE" not in json.dumps(public) + value.message()


@pytest.mark.parametrize("args", [
    ["az", "deployment", "group", "create"], ["az", "rest", "--method", "DELETE"],
    ["az", "rest", "--method", "PUT"], ["PRIVATE_EXECUTABLE", "PRIVATE_ARGUMENT"],
])
def test_failed_writes_and_unknown_commands_never_claim_no_change(args):
    result = classify(args, category="timeout").public()
    assert result["outcome"] == "unknown" and result["automaticRetry"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_actual_deploy_wrapper_preserves_dns_through_nested_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout=b"PRIVATE_TEMPLATE", stderr=b"getaddrinfo failed PRIVATE_KEY"))
    with pytest.raises(deploy.ReconcileError) as caught:
        deploy.run(["az", "deployment", "group", "list"], capture=True)
    nested = process_error(RuntimeError, child_command(tmp_path, "deploy", "clients/fixture"),
                           stderr=("mcp-kit: " + str(caught.value) + "\n").encode(), exit_code=2)
    report = failure_record(nested, "deploy")
    assert report["diagnostic"]["category"] == "dns"
    assert report["diagnostic"]["command"] == "az.deployment.group.list"
    assert report["phase"] == "deploy" and "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("field,value", [
    ("category", "PRIVATE_KEY"), ("armCode", "PRIVATE_KEY"), ("command", "PRIVATE_ARG"),
    ("exitCode", "PRIVATE_KEY"), ("nextAction", "PRIVATE_MESSAGE"),
])
def test_nested_marker_is_validated_not_trusted(tmp_path, field, value):
    wire = {"command": "az.deployment.group.list", "category": "dns", "exitCode": 1, "armCode": "unavailable"}
    wire[field] = value
    result = classify(child_command(tmp_path, "deploy"), stderr=(MARKER + json.dumps(wire)).encode(), exit_code=2)
    assert result.command == "kit.deploy"
    assert result.category == "unknown"
    assert "PRIVATE" not in result.message()


def test_provider_marker_does_not_override_azure_command():
    wire = {"command": "az.account.show", "category": "dns", "exitCode": 1, "armCode": "unavailable"}
    result = classify(["az", "deployment", "group", "create"],
                      stderr=(MARKER + json.dumps(wire)).encode(), exit_code=1)
    assert result.command == "az.deployment.group.create"
    assert result.public()["outcome"] == "unknown"


def test_unknown_arm_code_and_encoding_do_not_leak():
    result = classify(["az", "account", "show"],
                      stderr=b'{"error":{"code":"PRIVATE_KEY","message":"PRIVATE_TOKEN"}}', exit_code=1)
    assert result.armCode == "unavailable" and "PRIVATE" not in result.message()
    assert classify(["az", "account", "show"], category="encoding").category == "encoding"
