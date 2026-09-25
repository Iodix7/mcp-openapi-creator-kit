"""Offline runner/state-machine tests; no Azure provider behavior is simulated as proof."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from mcp_openapi_creator_kit import e2e
from mcp_openapi_creator_kit.cli import main

HASH = "a" * 64
TOKEN = "b" * 64
TARGET = {
    "account": "operator@example.test",
    "tenant": "22222222-2222-4222-8222-222222222222",
    "subscription": "11111111-1111-4111-8111-111111111111",
    "resource_group": "dedicated-test", "location": "westeurope",
    "publisher_name": "Fictional operator", "publisher_email": "operator@example.test",
}


@pytest.fixture
def local_run(tmp_path, monkeypatch):
    root = tmp_path / "customer"
    root.mkdir()
    monkeypatch.setattr(e2e, "verify_assets", lambda: {
        "source": "installed-package", "manifestSha256": HASH, "version": "test",
    })

    def execute(workspace, name, *arguments, timeout=300):
        assert name in {"spec-sync", "prepare", "catalog"}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            assert main(["--workspace", str(workspace), name, *arguments]) == 0
        return buffer.getvalue()

    monkeypatch.setattr(e2e, "_execute", execute)
    directory = tmp_path / "run"
    report = e2e.prepare(root, directory, HASH)
    assert report["status"] == "prepared"
    return directory


def test_source_and_unpinned_candidate_rejected_before_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e, "verify_assets", lambda: {"source": "source-development"})
    with pytest.raises(ValueError, match="installed wheel"):
        e2e.prepare(tmp_path, tmp_path / "run", HASH)
    monkeypatch.setattr(e2e, "verify_assets", lambda: {
        "source": "installed-package", "manifestSha256": HASH,
    })
    with pytest.raises(ValueError, match="SHA256"):
        e2e.prepare(tmp_path, tmp_path / "run", TOKEN)
    assert not list(tmp_path.iterdir())


def test_preparation_exact_three_tools_dashboard_and_no_azure(local_run):
    root, report = e2e._load(local_run)
    assert report["azure"] == report["cleanup"]["status"] == "not-started"
    assert len(report["expectedTools"]) == 3
    assert all(tool.startswith(report["client"] + "-") for tool in report["expectedTools"])
    assert (local_run / report["dashboard"]).is_file()
    e2e._check_reference(root, report)
    contract = yaml.safe_load((root / "apis" / "e2e-care" / "openapi.yaml").read_text())
    assert len(contract["paths"]) == 3
    assert contract["paths"]["/v1/reschedule-requests"]["post"]["x-mock"][0]["respond"]["status"] == 400
    with pytest.raises(ValueError, match="never overwritten"):
        e2e.prepare(local_run.parent / "customer", local_run, HASH)


def test_recovery_load_keeps_original_identity_and_requires_old_verification(local_run, monkeypatch):
    from mcp_openapi_creator_kit import ephemeral_gateway
    original = json.loads((local_run / "run.json").read_text())
    monkeypatch.setattr(e2e, "verify_assets", lambda: {
        "source": "installed-package", "manifestSha256": TOKEN, "version": "new",
    })
    with pytest.raises(ValueError, match="SHA256"):
        e2e._load(local_run)
    verified = []
    monkeypatch.setattr(ephemeral_gateway, "verify_creation_kit",
                        lambda path, info: verified.append((path, info)))
    old = local_run.parent / "original-package"
    _, recovered = e2e._load(local_run, old)
    assert verified == [(old, original["kit"])]
    assert recovered == original
    assert json.loads((local_run / "run.json").read_text()) == original
    def reject(*_):
        raise ValueError("Original package changed")
    monkeypatch.setattr(ephemeral_gateway, "verify_creation_kit", reject)
    with pytest.raises(ValueError, match="Original package changed"):
        e2e._load(local_run, old)


def test_changed_reference_or_artifact_blocks_before_azure(local_run, monkeypatch):
    root, report = e2e._load(local_run)
    monkeypatch.setattr(e2e, "_provisioner", lambda *_: pytest.fail("Azure was reached"))
    path = root / "apis" / "e2e-care" / "openapi.yaml"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="reference manifest/contract"):
        e2e.preview(local_run, TARGET, TARGET["subscription"])


@pytest.fixture
def previewed(local_run, monkeypatch):
    return _preview(local_run, monkeypatch)


def _preview(directory, monkeypatch):
    module = e2e.command("provision-gateway")
    calls = []

    def provision(context, **kwargs):
        calls.append(kwargs)
        return {
            "reviewToken": TOKEN, "resourceId": context.resource_id,
            "gatewayUrl": f"https://{context.apim_name}.azure-api.net",
            "context": context.report(), "status": "provisioned" if kwargs.get("apply") else "preview",
        }

    monkeypatch.setattr(module, "provision", provision)
    e2e.preview(directory, TARGET, TARGET["subscription"])
    calls.clear()
    return directory, calls


@pytest.mark.parametrize("approval,subscription,resource_id,token", [
    (False, TARGET["subscription"], None, TOKEN),
    (True, "wrong", None, TOKEN),
    (True, TARGET["subscription"], "wrong", TOKEN),
    (True, TARGET["subscription"], None, "wrong"),
])
def test_run_approval_gates_before_azure(previewed, approval, subscription, resource_id, token):
    directory, calls = previewed
    _, report = e2e._load(directory)
    with pytest.raises(ValueError):
        e2e.run(directory, subscription, token, approval,
                resource_id or report["creationPreview"]["resourceId"])
    assert calls == []
    assert e2e._load(directory)[1]["status"] == "previewed"


def _fake_cleanup(monkeypatch, result=None, error=None):
    calls = []
    class CleanupError(RuntimeError):
        pass

    def cleanup(root, creation, **kwargs):
        calls.append((root, creation, kwargs))
        if error:
            raise error
        return result or {"status": "deleted", "serviceAbsent": True, "purged": False}

    monkeypatch.setitem(sys.modules, "mcp_openapi_creator_kit.ephemeral_gateway",
                        SimpleNamespace(cleanup=cleanup, CleanupError=CleanupError))
    return calls


def test_run_success_requires_tests_and_actual_cleanup_outcome(previewed, monkeypatch):
    directory, calls = previewed
    _, before = e2e._load(directory)
    phases = []
    monkeypatch.setattr(e2e, "_deploy_reference", lambda *_: phases.append("deploy"))
    monkeypatch.setattr(e2e, "_verify", lambda *_: phases.append("verify"))
    deletes = _fake_cleanup(monkeypatch)
    result = e2e.run(directory, TARGET["subscription"], TOKEN, True, before["creationPreview"]["resourceId"])
    assert result["status"] == "passed" and result["testPassed"] is True
    assert phases == ["deploy", "verify"]
    assert len(deletes) == 1 and calls[0]["apply"] is True
    with pytest.raises(ValueError, match="never replayed"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, before["creationPreview"]["resourceId"])
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["deploy", "verify"])
def test_failure_still_cleans_and_redacts(previewed, monkeypatch, stage):
    directory, calls = previewed
    _, report = e2e._load(directory)
    monkeypatch.setattr(e2e, "_deploy_reference", lambda *_: None)
    monkeypatch.setattr(e2e, "_verify", lambda *_: None)

    def fail(*_):
        raise RuntimeError("VERY-PRIVATE-PROVIDER-RESPONSE")

    monkeypatch.setattr(e2e, "_deploy_reference" if stage == "deploy" else "_verify", fail)
    deletes = _fake_cleanup(monkeypatch)
    with pytest.raises(RuntimeError, match="E2E failed"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"])
    text = (directory / "run.json").read_text()
    result = json.loads(text)
    assert "VERY-PRIVATE" not in text
    assert result["failure"]["phase"] == stage
    assert result["status"] == "failed" and result["cleanup"]["serviceAbsent"] is True
    assert len(deletes) == 1


def test_uncertain_create_never_adopts_or_retries(previewed, monkeypatch):
    directory, calls = previewed
    _, report = e2e._load(directory)

    def fail(*_, **__):
        raise RuntimeError("uncertain")

    monkeypatch.setattr(e2e.command("provision-gateway"), "provision", fail)
    deletes = _fake_cleanup(monkeypatch)
    with pytest.raises(RuntimeError, match="E2E failed"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"])
    result = e2e._load(directory)[1]
    assert result["cleanup"]["status"] == "blocked" and deletes == []
    with pytest.raises(ValueError, match="No verified creation"):
        e2e.cleanup_run(directory, TARGET["subscription"], True, report["creationPreview"]["resourceId"])


def test_dns_failure_report_survives_cleanup_without_provider_payload(previewed, monkeypatch):
    from mcp_openapi_creator_kit.diagnostics import process_error
    directory, _ = previewed
    _, report = e2e._load(directory)

    def fail(*_):
        raise process_error(RuntimeError, ["az", "deployment", "group", "list"],
                            stderr=b"getaddrinfo failed PRIVATE_KEY", exit_code=1)

    monkeypatch.setattr(e2e, "_deploy_reference", fail)
    deletes = _fake_cleanup(monkeypatch)
    with pytest.raises(RuntimeError, match="E2E failed"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"])
    saved = e2e._load(directory)[1]
    assert saved["failure"]["phase"] == "deploy"
    assert saved["failure"]["diagnostic"]["category"] == "dns"
    assert saved["failure"]["diagnostic"]["outcome"] == "read-failed"
    assert saved["failure"]["diagnostic"]["scope"] == "failing-command-only"
    assert saved["cleanup"]["serviceAbsent"] and len(deletes) == 1
    assert "PRIVATE_KEY" not in (directory / "run.json").read_text()


@pytest.mark.parametrize("result,error", [
    ({"status": "accepted", "serviceAbsent": False}, None),
    (None, RuntimeError("timeout")),
])
def test_cleanup_acceptance_or_error_is_not_success(previewed, monkeypatch, result, error):
    directory, _ = previewed
    _, report = e2e._load(directory)
    monkeypatch.setattr(e2e, "_deploy_reference", lambda *_: None)
    monkeypatch.setattr(e2e, "_verify", lambda *_: None)
    _fake_cleanup(monkeypatch, result=result, error=error)
    with pytest.raises(RuntimeError, match="E2E failed"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"])
    saved = e2e._load(directory)[1]
    assert saved["testPassed"] is True and saved["status"] == "failed"
    assert saved["cleanup"]["operatorActionRequired"] is True


def test_preview_wrong_confirmation_no_cloud(local_run, monkeypatch):
    monkeypatch.setattr(e2e.command("provision-gateway"), "provision",
                        lambda *_a, **_k: pytest.fail("Azure was reached"))
    with pytest.raises(ValueError, match="confirmation"):
        e2e.preview(local_run, TARGET, "wrong")


def test_status_is_read_only(local_run, monkeypatch, capsys):
    before = {str(p): p.read_bytes() for p in local_run.rglob("*") if p.is_file()}
    monkeypatch.setattr(e2e, "_provisioner", lambda *_: pytest.fail("Azure was reached"))
    assert main(["--workspace", str(local_run.parent), "e2e", "status",
                 "--run-directory", str(local_run)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "prepared"
    assert before == {str(p): p.read_bytes() for p in local_run.rglob("*") if p.is_file()}


@pytest.mark.parametrize("status", [401, 200, 403])
def test_missing_auth_checks_exact_rest_and_mcp_endpoints(previewed, monkeypatch, status):
    directory, _ = previewed
    root, report = e2e._load(directory)
    report["creation"] = report["creationPreview"]
    requests = []

    def invoke(url, method, headers, body):
        requests.append((url, method, headers, body))
        return status, "application/json", {}

    monkeypatch.setattr(e2e.command("verify-rest"), "invoke", invoke)
    if status == 401:
        e2e._verify_missing_auth(root, report)
        assert len(requests) >= 3
        assert any(url.endswith("/mcp") and method == "POST" for url, method, _, _ in requests)
        assert all(url.startswith(report["creation"]["gatewayUrl"] + "/") and not headers
                   for url, _, headers, _ in requests)
    else:
        with pytest.raises(ValueError, match="HTTP 401"):
            e2e._verify_missing_auth(root, report)


@pytest.mark.parametrize("fail", [False, True])
def test_private_key_memory_only_and_environment_restored(previewed, monkeypatch, fail):
    import os
    directory, _ = previewed
    root, report = e2e._load(directory)
    report["creation"] = report["creationPreview"]
    module = e2e.command("provision-gateway")
    monkeypatch.setattr(module, "verify_context", lambda _: None)
    reads = []

    def rest(context, method, uri):
        reads.append((method, uri))
        return {"primaryKey": "PRIVATE-TEST-KEY"}

    monkeypatch.setattr(module, "rest", rest)
    monkeypatch.setenv("MCP_KEY", "original-private-value")
    monkeypatch.setattr(e2e, "_verify_missing_auth", lambda *_: None)
    calls = []

    def execute(root, name, *arguments, **kwargs):
        calls.append(name)
        assert os.environ["MCP_KEY"] == "PRIVATE-TEST-KEY"
        assert all("PRIVATE" not in value for value in arguments)
        if fail:
            raise RuntimeError("test failure")
        return ""

    monkeypatch.setattr(e2e, "_execute", execute)
    if fail:
        with pytest.raises(RuntimeError):
            e2e._verify(root, report)
    else:
        e2e._verify(root, report)
        assert calls == ["verify-rest", "verify-mcp"]
    assert os.environ["MCP_KEY"] == "original-private-value"
    assert reads == [("POST", report["creation"]["resourceId"] +
                     f"/subscriptions/{report['client']}-pilot/listSecrets?api-version=2024-05-01")]
    assert "PRIVATE" not in (directory / "run.json").read_text()


@pytest.mark.parametrize("change", ["valid", "extra", "modify", "delete", "incomplete"])
def test_client_plan_is_exact_creation_only(previewed, monkeypatch, change):
    from mcp_openapi_creator_kit.progress import receipt_path
    directory, _ = previewed
    root, report = e2e._load(directory)
    report["creation"] = report["creationPreview"]
    client_dir = root / "clients" / report["client"]
    manifest = yaml.safe_load((client_dir / "mcp-manifest.yaml").read_text())
    inventory = e2e.command("deployment").template_inventory(
        SimpleNamespace(base=report["creation"]["resourceId"]), manifest, e2e.PROFILE, client_dir)
    changes = [{"changeType": "Deploy" if "/Microsoft.Resources/" in rid else "Create", "resourceId": rid}
               for rid in sorted({rid for _, ids, _ in inventory for rid in ids})]
    plan = {"reviewToken": TOKEN, "deletions": [], "changes": changes}
    if change == "extra":
        plan["changes"].append({"changeType": "Create", "resourceId": report["creation"]["resourceId"] + "/apis/unrelated"})
    elif change == "modify":
        plan["changes"][0]["changeType"] = "Modify"
    elif change == "delete":
        plan["deletions"] = ["unrelated"]
    elif change == "incomplete":
        plan["changes"] = plan["changes"][:1]
    calls = []

    def execute(root, name, *arguments, **kwargs):
        calls.append(arguments)
        path = receipt_path(root, report["client"], "preview")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"plan": plan}))
        return ""

    monkeypatch.setattr(e2e, "_execute", execute)
    if change == "valid":
        e2e._deploy_reference(root, report)
        assert len(calls) == 2 and "--yes" in calls[1]
    else:
        with pytest.raises(ValueError):
            e2e._deploy_reference(root, report)
        assert len(calls) == 1


def test_durable_attempt_blocks_replay_even_with_reset_report(previewed, monkeypatch):
    directory, _ = previewed
    root, report = e2e._load(directory)
    (directory / "run-attempt.json").write_text("{}")
    monkeypatch.setattr(e2e, "_provisioner", lambda *_: pytest.fail("Azure was reached"))
    with pytest.raises(FileExistsError):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"])
    with pytest.raises(ValueError, match="already attempted"):
        e2e.preview(directory, TARGET, TARGET["subscription"])


def test_concurrent_runner_lock_and_release(local_run):
    with e2e._exclusive_run(local_run):
        with pytest.raises(RuntimeError, match="Another process"):
            with e2e._exclusive_run(local_run):
                pytest.fail("Concurrent mutation was admitted")
    with e2e._exclusive_run(local_run):
        pass


def test_prepare_failure_is_durable_and_not_cloud_success(tmp_path, monkeypatch):
    root = tmp_path / "customer"
    root.mkdir()
    monkeypatch.setattr(e2e, "verify_assets", lambda: {
        "source": "installed-package", "manifestSha256": HASH, "version": "test",
    })

    def fail(*_, **__):
        raise RuntimeError("PRIVATE-STAGE-DETAIL")

    monkeypatch.setattr(e2e, "_execute", fail)
    output = tmp_path / "failed-run"
    with pytest.raises(RuntimeError):
        e2e.prepare(root, output, HASH)
    text = (output / "run.json").read_text()
    assert "PRIVATE" not in text
    report = json.loads(text)
    assert report["status"] == "preparation-failed"
    assert report["azure"] == "not-started"


def test_sanitized_cleanup_diagnostics_are_preserved(monkeypatch):
    class CleanupError(RuntimeError):
        report = {"status": "blocked", "reason": "provenance-mismatch", "deleteAttempted": False}
    monkeypatch.setitem(sys.modules, "mcp_openapi_creator_kit.ephemeral_gateway",
                        SimpleNamespace(CleanupError=CleanupError))
    result = e2e._cleanup_failure(CleanupError("Do not persist arbitrary exception text"))
    assert result == {**CleanupError.report, "operatorActionRequired": True}


@pytest.fixture
def consumer_prepared(local_run):
    source, original = e2e._load(local_run)
    sentinel = source / "preserve-me.txt"
    sentinel.write_text("Customer-owned data\n")
    unused = source / "apis" / "unused"
    unused.mkdir()
    (unused / "openapi.yaml").write_text("not a valid contract; do not copy\n")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    directory = local_run.parent / "consumer-run"
    report = e2e.prepare(source, directory, HASH, original["client"], "copilot-studio")
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert report["expectedTools"] == original["expectedTools"]
    assert report["runId"] != original["runId"]
    assert not (directory / "workspace" / "preserve-me.txt").exists()
    assert not (directory / "workspace" / "apis" / "unused").exists()
    return directory


def test_workspace_snapshot_and_consumer_plan_are_local(consumer_prepared):
    root, report = e2e._load(consumer_prepared)
    assert report["scenario"]["kind"] == "workspace"
    assert report["azure"] == report["consumer"]["status"] == "not-started"
    assert (consumer_prepared / report["dashboard"]).is_file()
    assert not report.get("testPassed")
    e2e._check_reference(root, report)
    for relative, digest in report["scenario"]["sourceHashes"].items():
        assert e2e._hash((root / relative).read_bytes()) == digest


@pytest.mark.parametrize("change", ["external", "private", "auth", "slug", "missing-spec", "hardlink"])
def test_workspace_scenario_rejects_unsupported_inputs_before_writes(local_run, change):
    source, report = e2e._load(local_run)
    client = report["client"]
    path = source / "clients" / client / "mcp-manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    if change == "external":
        manifest["apis"][0]["backend"] = {"mode": "external", "url": "https://example.test"}
    elif change == "private":
        manifest["networkProfile"] = "isolated"
    elif change == "auth":
        manifest["inboundAuth"]["mode"] = "entraJwt"
    elif change == "slug":
        client = "../escape"
    elif change == "missing-spec":
        (source / "docs" / client / "spec.md").unlink()
    path.write_text(yaml.safe_dump(manifest))
    if change == "hardlink":
        (source / "linked-manifest").hardlink_to(path)
    output = local_run.parent / "rejected-run"
    with pytest.raises(ValueError):
        e2e.prepare(source, output, HASH, client, "copilot-studio")
    assert not output.exists()


def test_changed_workspace_snapshot_blocks_preview(consumer_prepared, monkeypatch):
    root, _ = e2e._load(consumer_prepared)
    contract = root / "apis" / "e2e-care" / "openapi.yaml"
    contract.write_bytes(contract.read_bytes() + b"\n")
    monkeypatch.setattr(e2e, "_provisioner", lambda *_: pytest.fail("Azure was reached"))
    with pytest.raises(ValueError, match="artifacts changed"):
        e2e.preview(consumer_prepared, TARGET, TARGET["subscription"])


@pytest.fixture
def consumer_live(consumer_prepared, monkeypatch):
    directory, calls = _preview(consumer_prepared, monkeypatch)
    _, report = e2e._load(directory)
    monkeypatch.setattr(e2e, "_deploy_reference", lambda *_: None)
    monkeypatch.setattr(e2e, "_verify", lambda *_: None)
    deletes = _fake_cleanup(monkeypatch)
    resource_id = report["creationPreview"]["resourceId"]
    with pytest.raises(ValueError, match="retain-for-consumer"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, resource_id)
    assert calls == [] and deletes == []
    result = e2e.run(directory, TARGET["subscription"], TOKEN, True, resource_id, True)
    assert result["status"] == "awaiting-consumer"
    assert result["transportPassed"] is True and result["testPassed"] is False
    assert len(calls) == 1 and deletes == []
    return directory, deletes


def _observations(directory):
    _, report = e2e._load(directory)
    source = directory.parent / "observations"
    source.mkdir()
    trace = b"Synthetic unit-test evidence, NOT a real Studio result.\n"
    (source / "transcript.txt").write_bytes(trace)
    evidence = {
        "schemaVersion": 1, "runId": report["runId"],
        "resourceId": report["creation"]["resourceId"],
        "environmentId": TARGET["tenant"], "agentId": TARGET["subscription"],
        "observedAtUtc": e2e._now(),
        "observations": [
            {"operationId": tool, "outcome": "passed", "trace": "transcript.txt",
             "sha256": e2e._hash(trace)} for tool in report["expectedTools"]
        ],
    }
    path = source / "observation.json"
    path.write_bytes(e2e._json(evidence))
    return path, evidence


@pytest.mark.parametrize("result", ["passed", "failed", "missing", "changed", "deleted"])
def test_consumer_result_requires_observation_and_cleanup(consumer_live, result):
    directory, deletes = consumer_live
    _, report = e2e._load(directory)
    if result != "missing":
        path, evidence = _observations(directory)
        if result == "failed":
            evidence["observations"][0]["outcome"] = "failed"
            path.write_bytes(e2e._json(evidence))
        recorded = e2e.record_consumer(directory, path)
        assert recorded["status"] == "consumer-recorded" and deletes == []
        # Evidence is copied; deleting its input does not invalidate the retained record.
        path.unlink()
        if result == "changed":
            (directory / "consumer-evidence" / "transcript.txt").write_text("changed")
        elif result == "deleted":
            (directory / "consumer-evidence" / "transcript.txt").unlink()
        with pytest.raises(ValueError, match="awaiting its consumer"):
            e2e.record_consumer(directory, path)
    cleaned = e2e.cleanup_run(directory, TARGET["subscription"], True, report["creation"]["resourceId"])
    assert len(deletes) == 1 and cleaned["cleanup"]["serviceAbsent"] is True
    expected = "passed" if result == "passed" else "incomplete-cleaned" if result == "missing" else "failed"
    assert cleaned["status"] == expected
    assert cleaned["testPassed"] is (result == "passed")


@pytest.mark.parametrize("invalid", [
    "wrong-run", "wrong-service", "wrong-tool", "missing-tool", "duplicate-tool",
    "hash", "empty-trace", "trace-escape", "stale", "future", "environment", "credential-field",
])
def test_consumer_evidence_rejected_without_writes_or_cloud(consumer_live, invalid):
    directory, deletes = consumer_live
    path, evidence = _observations(directory)
    first = evidence["observations"][0]
    if invalid == "wrong-run":
        evidence["runId"] = TARGET["tenant"]
    elif invalid == "wrong-service":
        evidence["resourceId"] += "-other"
    elif invalid == "wrong-tool":
        first["operationId"] = "unselected-tool"
    elif invalid == "missing-tool":
        evidence["observations"].pop()
    elif invalid == "duplicate-tool":
        first["operationId"] = evidence["observations"][1]["operationId"]
    elif invalid == "hash":
        first["sha256"] = "0" * 64
    elif invalid == "empty-trace":
        (path.parent / "transcript.txt").write_text("")
    elif invalid == "trace-escape":
        first["trace"] = "../transcript.txt"
    elif invalid == "stale":
        evidence["observedAtUtc"] = "2000-01-01T00:00:00+00:00"
    elif invalid == "future":
        evidence["observedAtUtc"] = "9999-01-01T00:00:00+00:00"
    elif invalid == "environment":
        evidence["environmentId"] = "https://example.test"
    else:
        evidence["subscriptionKey"] = "DO-NOT-COPY"
    path.write_bytes(e2e._json(evidence))
    before = (directory / "run.json").read_bytes()
    with pytest.raises(ValueError):
        e2e.record_consumer(directory, path)
    assert before == (directory / "run.json").read_bytes()
    assert not (directory / "consumer-evidence").exists() and deletes == []


def test_consumer_failure_before_retention_still_cleans(consumer_prepared, monkeypatch):
    directory, _ = _preview(consumer_prepared, monkeypatch)
    _, report = e2e._load(directory)
    monkeypatch.setattr(e2e, "_deploy_reference", lambda *_: None)
    def fail(*_):
        raise RuntimeError("PRIVATE")
    monkeypatch.setattr(e2e, "_verify", fail)
    deletes = _fake_cleanup(monkeypatch)
    with pytest.raises(RuntimeError, match="E2E failed"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"], True)
    _, result = e2e._load(directory)
    assert result["status"] == "failed" and len(deletes) == 1
    assert result["cleanup"]["serviceAbsent"] is True


def test_reference_cannot_be_retained_without_prepared_consumer(previewed):
    directory, calls = previewed
    _, report = e2e._load(directory)
    with pytest.raises(ValueError, match="retain-for-consumer"):
        e2e.run(directory, TARGET["subscription"], TOKEN, True, report["creationPreview"]["resourceId"], True)
    assert calls == []


def test_consumer_cleanup_failure_cannot_pass(consumer_live, monkeypatch):
    directory, _ = consumer_live
    path, _ = _observations(directory)
    report = e2e.record_consumer(directory, path)
    deletes = _fake_cleanup(monkeypatch, result={"status": "accepted", "serviceAbsent": False})
    with pytest.raises(RuntimeError, match="absence"):
        e2e.cleanup_run(directory, TARGET["subscription"], True, report["creation"]["resourceId"])
    saved = e2e._load(directory)[1]
    assert saved["status"] == "failed"
    assert saved["cleanup"]["operatorActionRequired"] is True and len(deletes) == 1


def test_consumer_cli_records_default_environment_and_locks(consumer_live, capsys):
    directory, _ = consumer_live
    path, evidence = _observations(directory)
    evidence["environmentId"] = "Default-" + evidence["environmentId"]
    path.write_bytes(e2e._json(evidence))
    arguments = ["e2e", "record-consumer", "--run-directory", str(directory), "--evidence", str(path)]
    with e2e._exclusive_run(directory):
        with pytest.raises(RuntimeError, match="Another process"):
            e2e.main(directory.parent, arguments[1:])
    assert e2e.main(directory.parent, arguments[1:]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "consumer-recorded"
