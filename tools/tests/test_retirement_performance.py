"""Bounded offline inventory, progress and subprocess-cleanup regressions."""
from collections import Counter
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from test_retirement import BASE, SUB, RetirementHarness, rc, harness


def absent_baseline(harness):
    harness.inventory.clear()
    for index in range(16):
        name = f"baseline-{index}"
        harness.add("/apis/" + name, {"type": "http"})
        for operation in range(3 if index < 6 else 2):
            harness.add(f"/apis/{name}/operations/get-{operation}", {})
    for index in range(3):
        harness.add(f"/products/baseline-{index}", {})
    for index in range(4):
        harness.add(f"/subscriptions/baseline-{index}", {"scope": "/apis"})
        harness.add(f"/tags/baseline-{index}", {"displayName": f"Baseline {index}"})
    harness.add("/apis/baseline-0/tags/baseline-0", {"displayName": "Baseline 0"},
                canonical="/tags/baseline-0")
    harness.add("/apis/baseline-1/operations/get-0/tags/baseline-1", {"displayName": "Baseline 1"},
                canonical="/tags/baseline-1")
    harness.add("/products/baseline-0/tags/baseline-2", {"displayName": "Baseline 2"},
                canonical="/tags/baseline-2")


def rest_paths(calls):
    return [args[args.index("--uri") + 1].split("?", 1)[0].removeprefix(BASE)
            for args in calls if args[:2] == ["az", "rest"]]


def test_absent_16_api_38_operation_client_needs_exactly_eight_rest_reads(harness):
    absent_baseline(harness)
    assert sum(len(items) for path, items in harness.inventory.items() if path.endswith("/operations")) == 38
    plan = rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert not plan.deletions and not plan.full_inventory
    assert Counter(rest_paths(harness.calls)) == Counter({
        "": 1, "/apis": 1, "/products": 1, "/tags": 1, "/subscriptions": 1,
        "/namedValues": 1, "/backends": 1, "/tagResources": 1,
    })
    assert not harness.deletes


def test_full_graph_exact_requests_and_each_delete_refreshes_all_collections(harness, capsys):
    plan = rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    calls = rest_paths(harness.calls)
    assert len(calls) == 25 and len(set(calls)) == 25
    assert not any(path.endswith("/tags") and path != "/tags" for path in calls)
    assert "/apis/unrelated/operations" in calls
    assert "/tagResources" in calls
    harness.calls.clear()
    token = harness.preview(capsys)
    harness.calls.clear()
    harness.invoke("--yes", "--review-token", token)
    assert len(harness.deletes) == len(plan.deletions)
    counts = Counter(rest_paths(harness.calls))
    assert counts["/tagResources"] == len(plan.deletions) + 2
    assert counts["/apis/unrelated/operations"] == len(plan.deletions) + 2


def test_consumption_never_requests_unsupported_product_groups_and_can_retire(harness, monkeypatch, capsys):
    harness.gateway["sku"]["name"] = "Consumption"
    harness.remove("/apis/demo-things-mcp")
    harness.remove("/products/demo-product/apis/demo-things-mcp")
    harness.remove("/products/demo-product/groups/developers")
    original = harness.run

    def run(args, capture=False):
        if "--uri" in args and "/products/demo-product/groups?" in args[args.index("--uri") + 1]:
            pytest.fail("Unsupported Consumption call: Bad Request / MethodNotAllowedInPricingTier")
        return original(args, capture=capture)

    monkeypatch.setattr(rc, "run", run)
    harness.invoke()
    output = capsys.readouterr()
    notice = "UNAVAILABLE-BY-TIER (observed Consumption)"
    assert notice in output.out and notice in output.err
    assert BASE + "/products/demo-product/groups" in output.out
    token = output.out.split("Review token: ")[1].splitlines()[0]
    harness.invoke("--yes", "--review-token", token)
    assert "/products/demo-product/groups" not in rest_paths(harness.calls)
    assert BASE + "/products/demo-product" in harness.deletes
    assert "/tagResources" in rest_paths(harness.calls)
    assert "/subscriptions" in rest_paths(harness.calls)
    assert "/products/demo-product/apis" in rest_paths(harness.calls)
    assert not any("/groups/" in rid for rid in harness.deletes)


@pytest.mark.parametrize("tier", ["BasicV2", "Developer", "Standard", None])
def test_non_consumption_or_unknown_tier_does_not_hide_product_group_errors(harness, tier):
    harness.gateway["sku"] = {} if tier is None else {"name": tier}

    def runner(args):
        if "/products/demo-product/groups?" in args[args.index("--uri") + 1]:
            harness.calls.append(args)
            raise rc.ReconcileError("MethodNotAllowedInPricingTier")
        return harness.run(args, capture=True)

    client = rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=runner)
    with pytest.raises(rc.ReconcileError, match="MethodNotAllowedInPricingTier"):
        rc.inspect_retirement(client, "demo", account=harness.account["user"])
    assert "/products/demo-product/groups" in rest_paths(harness.calls)
    assert not harness.deletes


@pytest.mark.parametrize("relationship", ["api", "tag", "subscription"])
def test_consumption_keeps_all_applicable_shared_relationship_checks(harness, relationship):
    harness.gateway["sku"]["name"] = "Consumption"
    if relationship == "api":
        harness.add("/products/demo-product/apis/unrelated", {}, canonical="/apis/unrelated")
    elif relationship == "tag":
        harness.add("/apis/unrelated/tags/demo", {"displayName": "demo"}, canonical="/tags/demo")
    else:
        harness.add("/subscriptions/foreign", {"scope": BASE + "/products/demo-product"})
    with pytest.raises(rc.ReconcileError):
        rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert "/products/demo-product/groups" not in rest_paths(harness.calls)
    assert not harness.deletes


def test_native_tier_group_association_scope_remains_checked(harness):
    harness.inventory["/products/demo-product/groups"][0]["id"] = BASE + "-foreign/groups/developers"
    with pytest.raises(rc.ReconcileError, match="scope"):
        rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert "/products/demo-product/groups" in rest_paths(harness.calls)
    assert not harness.deletes


def test_service_and_full_graph_reads_use_at_most_four_concurrent_runners(harness, monkeypatch):
    gate = threading.Barrier(4, timeout=5)
    lock = threading.Lock()
    first_batch = {"/apis", "/backends", "/namedValues", ""}
    active = maximum = requests = 0
    original = harness.run

    def runner(args):
        nonlocal active, maximum, requests
        with lock:
            active += 1
            requests += 1
            maximum = max(maximum, active)
        try:
            suffix = args[args.index("--uri") + 1].split("?", 1)[0].removeprefix(BASE)
            if suffix in first_batch:
                gate.wait()
            return original(args, capture=True)
        finally:
            with lock:
                active -= 1

    client = rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=runner)
    plan = rc.inspect_retirement(client, "demo", account=harness.account["user"])
    assert plan.deletions
    assert maximum == rc.MAX_READ_WORKERS == 4
    assert requests == 25 and active == 0


def test_failed_read_cancels_unscheduled_work_and_cannot_apply():
    barrier = threading.Barrier(4, timeout=5)
    calls = []
    lock = threading.Lock()

    class Client:
        base = BASE

        def records(self, suffix, *, version):
            with lock:
                calls.append(suffix)
            barrier.wait()
            if suffix == "/a":
                raise rc.ReconcileError("Fixture read failed")
            threading.Event().wait(0.1)
            return []

    reads = rc.SnapshotReads(Client())
    with pytest.raises(rc.ReconcileError, match="Fixture"):
        reads.load([(f"/{letter}", rc.API_VERSION) for letter in "abcdefghij"], "fixture")
    assert set(calls) == {"/a", "/b", "/c", "/d"}


@pytest.mark.parametrize("relationship", ["api", "product", "operation", "native", "subscription", "named-value"])
def test_absent_fast_path_never_ignores_remaining_identity_or_reference(harness, relationship):
    absent_baseline(harness)
    if relationship in {"api", "product", "operation"}:
        harness.add("/tags/demo", {"displayName": "demo"})
        suffix = {
            "api": "/apis/baseline-0/tags/demo",
            "product": "/products/baseline-0/tags/demo",
            "operation": "/apis/baseline-0/operations/get-0/tags/demo",
        }[relationship]
        harness.add(suffix, {"displayName": "demo"}, canonical="/tags/demo")
    elif relationship == "native":
        harness.add("/apis/baseline-native", {"type": "mcp"})
        harness.add("/apis/baseline-native/tools/dangling", {
            "operationId": BASE + "/apis/demo-missing/operations/get"})
    elif relationship == "subscription":
        harness.add("/subscriptions/foreign", {"scope": BASE + "/products/demo-product"})
    else:
        harness.add("/namedValues/foreign", {"tags": ["demo"]})
    with pytest.raises(rc.ReconcileError):
        rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert not harness.deletes


def test_absent_native_references_are_fetched_even_without_matching_resource_names(harness):
    absent_baseline(harness)
    harness.add("/apis/baseline-native", {"type": "mcp"})
    harness.add("/apis/baseline-native/tools/get", {
        "operationId": BASE + "/apis/baseline-0/operations/get-0"})
    plan = rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert not plan.deletions
    assert len(rest_paths(harness.calls)) == 9
    assert "/apis/baseline-native/tools" in rest_paths(harness.calls)


def test_relative_native_reference_cannot_bypass_absence_verification(harness):
    absent_baseline(harness)
    harness.add("/apis/baseline-native", {"type": "mcp"})
    harness.add("/apis/baseline-native/tools/get", {"operationId": "/apis/demo-missing/operations/get"})
    with pytest.raises(rc.ReconcileError, match="unsupported"):
        rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert not harness.deletes


def test_bulk_relationships_are_refetched_after_each_delete(harness, capsys):
    token = harness.preview(capsys)
    harness.after_delete = lambda _: harness.add(
        "/apis/unrelated/tags/demo", {"displayName": "demo"}, canonical="/tags/demo")
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", token)
    assert len(harness.deletes) == 1


@pytest.mark.parametrize("case", ["missing-value", "incomplete-count", "foreign-scope", "cycle", "page-limit"])
def test_pagination_is_complete_bounded_and_fail_closed(case, monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "MAX_COLLECTION_PAGES", 2)
    uri = BASE + "/tags?api-version=" + rc.API_VERSION

    def runner(args):
        calls.append(args)
        if case == "missing-value":
            return "{}"
        if case == "incomplete-count":
            return json.dumps({"value": [], "count": 1})
        next_uri = (
            BASE + "/apis?api-version=" + rc.API_VERSION if case == "foreign-scope" else
            uri if case == "cycle" else uri + "&page=" + str(len(calls))
        )
        return json.dumps({"value": [], "nextLink": next_uri})

    client = rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=runner)
    with pytest.raises(rc.ReconcileError):
        client.records("/tags")
    assert len(calls) == (2 if case == "page-limit" else 1)


def test_later_page_client_tag_cannot_be_missed_by_absence_probe(harness):
    absent_baseline(harness)
    harness.add("/tags/demo", {"displayName": "demo"})
    original = harness.run
    tag_calls = 0

    def runner(args):
        nonlocal tag_calls
        uri = args[args.index("--uri") + 1]
        if "/tags?" in uri:
            tag_calls += 1
            if "&page=2" not in uri:
                return json.dumps({"value": [], "nextLink": uri + "&page=2"})
        return original(args, capture=True)

    client = rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=runner)
    with pytest.raises(rc.ReconcileError, match="Detached"):
        rc.inspect_retirement(client, "demo", account=harness.account["user"])
    assert tag_calls == 2 and not harness.deletes


def test_progress_is_flushed_and_contains_only_stages_and_resource_ids(harness, monkeypatch, capsys):
    writes = []
    output = SimpleNamespace(
        write=lambda value: writes.append(("write", value)),
        flush=lambda: writes.append(("flush", "")),
    )
    monkeypatch.setattr(rc.sys, "stderr", output)
    rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    text = "".join(value for event, value in writes if event == "write")
    assert "READ begin " + BASE + "/tagResources" in text
    assert "READ complete " + BASE + "/tagResources" in text
    assert "inventory complete: full graph" in text
    assert sum(event == "flush" for event, _ in writes) >= 50
    assert not any(secret in text for secret in ("PRIVATE_", "NEVER_PRINT", "operator@example", "api-version="))


def test_runner_timeout_has_exact_budget_tree_cleanup_and_no_retry(tmp_path, monkeypatch, capsys):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(installed, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(installed.shutil, "which", lambda _: sys.executable)
    popens, communications, cleanup = [], [], []
    process = SimpleNamespace(pid=123456, stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=0)

    def communicate(*, timeout):
        communications.append(timeout)
        if len(communications) == 1:
            raise subprocess.TimeoutExpired(["SECRET_RAW_COMMAND"], timeout, output=b"PRIVATE_OUTPUT")
        return b"", b""

    process.communicate = communicate
    monkeypatch.setattr(installed.subprocess, "Popen", lambda *args, **kwargs: popens.append(kwargs) or process)
    monkeypatch.setattr(installed, "terminate_process_tree", lambda child: cleanup.append(child.pid))
    with pytest.raises(installed.ReconcileError, match="timed out after 60s"):
        installed.run(["az", "rest", "--method", "GET", "--uri", BASE + "/apis?api-version=secret-query"], capture=True)
    assert len(popens) == 1 and cleanup == [123456]
    assert communications == [60, 10]
    assert process.stdout.closed and process.stderr.closed
    output = capsys.readouterr()
    assert "CLI timeout GET " + BASE + "/apis" in output.err
    assert all(value not in output.err for value in ("PRIVATE_OUTPUT", "SECRET_RAW_COMMAND", "secret-query"))


def test_failed_timeout_cleanup_is_explicit_and_does_not_block_on_reader_locks(tmp_path, monkeypatch):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(installed, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(installed.shutil, "which", lambda _: sys.executable)
    actions = []

    def expired(**kwargs):
        raise subprocess.TimeoutExpired(["SECRET"], kwargs["timeout"])

    process = SimpleNamespace(
        pid=123456, stdout=io.BytesIO(), stderr=io.BytesIO(), communicate=expired,
        kill=lambda: actions.append("kill"), wait=lambda **kwargs: actions.append(("wait", kwargs["timeout"])),
    )
    monkeypatch.setattr(installed.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(installed, "terminate_process_tree",
                        lambda _: (_ for _ in ()).throw(installed.ReconcileError("Fixture cleanup failure")))
    with pytest.raises(installed.ReconcileError, match="cleanup was not confirmed"):
        installed.run(["az", "account", "show"], capture=True)
    assert actions == ([] if os.name == "nt" else ["kill"]) + [("wait", 10)]
    assert not process.stdout.closed and not process.stderr.closed


def test_snapshot_deadline_stops_before_any_read(harness, monkeypatch):
    monkeypatch.setattr(rc, "SNAPSHOT_TIMEOUT_SECONDS", 0)
    with pytest.raises(rc.ReconcileError, match="deadline"):
        rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    assert not harness.calls


def test_cancelled_pagination_never_launches_another_cli_process():
    cancelled = threading.Event()
    calls = []

    def runner(args):
        calls.append(args)
        cancelled.set()
        return json.dumps({"value": [], "nextLink": BASE + "/tags?api-version=test&page=2"})

    client = rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=runner)
    token = rc._READ_BUDGET.set(rc.ReadBudget(time.monotonic() + 300, cancelled))
    try:
        with pytest.raises(rc.ReconcileError, match="cancelled"):
            client.records("/tags")
    finally:
        rc._READ_BUDGET.reset(token)
    assert len(calls) == 1


def test_remaining_snapshot_budget_caps_cli_timeout(tmp_path, monkeypatch):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(installed, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(installed.shutil, "which", lambda _: sys.executable)
    timeouts = []
    process = SimpleNamespace(
        returncode=0, stdout=io.BytesIO(), stderr=io.BytesIO(),
        communicate=lambda **kwargs: timeouts.append(kwargs["timeout"]) or (b"{}", b""),
    )
    monkeypatch.setattr(installed.subprocess, "Popen", lambda *args, **kwargs: process)
    token = installed._READ_BUDGET.set(installed.ReadBudget(time.monotonic() + 5, threading.Event()))
    try:
        assert installed.run(["az", "account", "show"], capture=True) == "{}"
    finally:
        installed._READ_BUDGET.reset(token)
    assert 0 < timeouts[0] <= 5


def test_delete_absence_wait_has_an_independent_total_deadline(harness, monkeypatch):
    monkeypatch.setattr(rc, "DELETE_WAIT_SECONDS", 0)
    with pytest.raises(rc.ReconcileError, match="deadline"):
        harness.client.delete_reviewed(rc.Deletion(BASE + "/apis/demo-things"))
    assert len(harness.deletes) == 1
    assert len(harness.calls) == 1
    assert rc._READ_BUDGET.get() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific PID tree cleanup")
def test_windows_cleanup_uses_cim_identity_guards_stop_process_ids_and_devnull(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "windows_creation_time", lambda _: 12345678901234567)
    monkeypatch.setattr(rc.subprocess, "run",
                        lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    rc.terminate_process_tree(SimpleNamespace(pid=98765))
    args, kwargs = calls[0]
    assert Path(args[0]).name == "powershell.exe"
    assert {"-NoProfile", "-NonInteractive", "-Command"} <= set(args)
    script = args[-1]
    assert script.endswith(" 98765 12345678901234567")
    assert "Get-CimInstance -ClassName Win32_Process" in script
    assert "Stop-Process -Id $node.Id" in script
    assert "Stop-Process -Name" not in script and "taskkill" not in script.lower()
    assert "$root.Started -ne $ExpectedStart" in script
    assert "$null = $item.Handle" in script
    assert "$child.Started - $cimStart" in script
    assert kwargs == {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "timeout": 10}


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific PID tree cleanup")
def test_windows_cleanup_refuses_mismatched_creation_identity_without_killing_target(monkeypatch):
    process = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    original = rc.windows_creation_time
    try:
        with monkeypatch.context() as context:
            context.setattr(rc, "windows_creation_time", lambda child: original(child) + 1)
            with pytest.raises(rc.ReconcileError, match="could not be confirmed"):
                rc.terminate_process_tree(process)
        assert process.poll() is None
    finally:
        if process.poll() is None:
            rc.terminate_process_tree(process)
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific PID tree cleanup")
def test_windows_cleanup_preserves_unrelated_sibling():
    command = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
    root = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sibling = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        rc.terminate_process_tree(root)
        root.wait(timeout=10)
        assert sibling.poll() is None
    finally:
        for process in (root, sibling):
            if process.poll() is None:
                rc.terminate_process_tree(process)
            process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific PID tree cleanup")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_windows_timeout_stops_real_local_descendant_without_azure(tmp_path, monkeypatch, parent_exits):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(installed, "REPO_ROOT", tmp_path)
    # A Windows venv launcher adds another ancestor. The exited-root case
    # deliberately uses a direct interpreter so the retained root is the parent.
    executable = sys._base_executable if parent_exits else sys.executable
    monkeypatch.setattr(installed.shutil, "which", lambda _: executable)
    monkeypatch.setattr(installed, "CLI_TIMEOUT_SECONDS", 2)
    pid_file = tmp_path / "child-pids.json"
    code = (
        "import json,os,subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid])); "
        + ("pass" if parent_exits else "time.sleep(60)")
    )
    with pytest.raises(installed.ReconcileError, match="process tree stopped"):
        installed.run(["az", "-I", "-c", code, str(pid_file)], capture=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for pid in json.loads(pid_file.read_text()):
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 0) == 0, f"Timed-out child still active: {pid}"
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87
