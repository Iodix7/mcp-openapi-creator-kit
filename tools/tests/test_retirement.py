"""Offline installed-retirement safety and packaging regressions."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from lifecycle import ReconcileError

spec = importlib.util.spec_from_file_location("retirement_test_command", TOOLS / "retire-client.py")
rc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rc
spec.loader.exec_module(rc)

SUB = "00000000-0000-0000-0000-000000000000"
TENANT = "00000000-0000-0000-0000-000000000001"
BASE = f"/subscriptions/{SUB}/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture-apim"


class RetirementHarness:
    def __init__(self, root, monkeypatch):
        self.root = root
        self.directory = root / "clients" / "demo"
        self.directory.mkdir(parents=True)
        self.manifest = {"client": "demo", "apis": [{"name": "things", "backend": {"mode": "mock"}}]}
        self.save()
        contract = root / "apis" / "things" / "openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text("openapi: 3.0.3\n", encoding="utf-8")
        self.inventory = {}
        self.calls = []
        self.account = {"id": SUB, "tenantId": TENANT, "user": "operator@example.test"}
        self.gateway = {"id": BASE, "sku": {"name": "BasicV2"}}
        self.before_delete = None
        self.after_delete = None
        self.add("/apis/demo-things", {"type": "http", "apiRevision": "1"})
        self.add("/apis/demo-things/tags/demo", {"displayName": "demo"}, canonical="/tags/demo")
        self.add("/apis/demo-things/tags/demo-mock", {"displayName": "demo-mock"}, canonical="/tags/demo-mock")
        self.add("/apis/demo-things/operations/get-thing", {"method": "GET", "urlTemplate": "/things"})
        self.add("/apis/demo-things/policies/policy", {"value": "<policies>PRIVATE_POLICY</policies>"})
        self.add("/apis/demo-things/schemas/schema", {"document": {"value": "PRIVATE_SCHEMA"}})
        self.add("/apis/demo-things-mcp", {"type": "mcp"})
        self.add("/apis/demo-things-mcp/tags/demo", {"displayName": "demo"}, canonical="/tags/demo")
        self.add("/apis/demo-things-mcp/tools/get-thing", {
            "operationId": BASE + "/apis/demo-things/operations/get-thing"})
        self.add("/products/demo-product", {"displayName": "Demo"})
        self.add("/products/demo-product/tags/demo", {"displayName": "demo"}, canonical="/tags/demo")
        self.add("/products/demo-product/policies/policy", {"value": "PRIVATE_PRODUCT_POLICY"})
        self.add("/products/demo-product/groups/developers", {"type": "system"}, canonical="/groups/developers")
        self.add("/products/demo-product/apis/demo-things", {}, canonical="/apis/demo-things")
        self.add("/products/demo-product/apis/demo-things-mcp", {}, canonical="/apis/demo-things-mcp")
        self.add("/subscriptions/demo-pilot", {"scope": BASE + "/products/demo-product", "state": "active",
                                              "primaryKey": "NEVER_PRINT_THIS_KEY"})
        self.add("/tags/demo", {"displayName": "demo"})
        self.add("/tags/demo-mock", {"displayName": "demo-mock"})
        self.add("/apis/unrelated", {"type": "http"})
        self.add("/subscriptions/master", {"scope": "/apis"})
        monkeypatch.setattr(rc, "REPO_ROOT", root)
        monkeypatch.setattr(rc, "run", self.run)
        monkeypatch.setattr(rc, "local_python", lambda _: sys.executable)
        monkeypatch.setattr(rc.time, "sleep", lambda _: None)
        self.monkeypatch = monkeypatch

    def save(self):
        (self.directory / "mcp-manifest.yaml").write_text(yaml.safe_dump(self.manifest), encoding="utf-8")

    def add(self, suffix, properties, *, canonical=None):
        parent, _, name = suffix.rpartition("/")
        record = {"id": BASE + (canonical or suffix), "name": name, "properties": properties}
        self.inventory.setdefault(parent, []).append(record)
        return record

    def remove(self, suffix):
        parent, _, name = suffix.rpartition("/")
        self.inventory[parent] = [item for item in self.inventory.get(parent, []) if item["name"] != name]
        for key in list(self.inventory):
            if key.startswith(suffix + "/"):
                del self.inventory[key]

    @property
    def client(self):
        return rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=lambda args: self.run(args, capture=True))

    def run(self, args, capture=False):
        self.calls.append(args)
        if args[:3] == ["az", "account", "show"]:
            return json.dumps(self.account)
        assert args[:2] == ["az", "rest"], f"Unexpected executable path: {args[:3]}"
        assert args[args.index("--subscription") + 1] == SUB
        uri = args[args.index("--uri") + 1]
        assert uri.startswith(BASE)
        method = args[args.index("--method") + 1]
        suffix = uri.split("?", 1)[0].removeprefix(BASE)
        if method == "DELETE":
            assert "--headers" in args and "If-Match=*" in args
            if self.before_delete:
                self.before_delete(suffix)
            self.remove(suffix)
            if self.after_delete:
                self.after_delete(suffix)
            return ""
        assert method == "GET"
        if not suffix:
            return json.dumps(self.gateway)
        records = copy.deepcopy(self.inventory.get(suffix, []))
        if suffix == "/tagResources":
            for collection, items in self.inventory.items():
                if not collection.endswith("/tags") or collection == "/tags":
                    continue
                parent = collection.removesuffix("/tags")
                kind = ("operation" if "/operations/" in parent else
                        "product" if parent.startswith("/products/") else "api")
                for item in items:
                    tag_name = item["name"]
                    tag = next((tag for tag in self.inventory.get("/tags", []) if tag["name"] == tag_name), item)
                    records.append({"tag": {"id": "/tags/" + tag_name,
                                            "name": tag.get("properties", {}).get("displayName")},
                                    kind: {"id": parent}})
        if suffix in {"/subscriptions", "/namedValues"}:
            assert "--query" in args
            for item in records:
                item["properties"].pop("primaryKey", None)
                item["properties"].pop("secondaryKey", None)
        return json.dumps({"value": records})

    def invoke(self, *extra):
        arguments = [
            "retire-client.py", "clients/demo", "--subscription", SUB, "--tenant", TENANT,
            "--resource-group", "fixture", "--apim-name", "fixture-apim",
            "--profile", "native-mcp", "--confirm-subscription", SUB, *extra,
        ]
        self.monkeypatch.setattr(sys, "argv", arguments)
        rc.main()

    def preview(self, capsys):
        self.invoke()
        return capsys.readouterr().out.split("Review token: ")[1].splitlines()[0]

    @property
    def deletes(self):
        return [args[args.index("--uri") + 1].split("?", 1)[0]
                for args in self.calls if "DELETE" in args]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return RetirementHarness(tmp_path, monkeypatch)


def test_preview_is_read_only_exact_ids_and_no_secrets(harness, capsys, monkeypatch):
    monkeypatch.setenv("MCP_RECONCILE_APPLY", "true")
    before = {str(p): p.read_bytes() for p in harness.root.rglob("*") if p.is_file()}
    cloud = copy.deepcopy(harness.inventory)
    harness.invoke()
    output = capsys.readouterr().out
    assert "Retirement DRY-RUN" in output and "CASCADE DELETE" in output
    for suffix in ("/apis/demo-things", "/apis/demo-things/operations/get-thing",
                   "/apis/demo-things/policies/policy", "/apis/demo-things/schemas/schema",
                   "/products/demo-product", "/products/demo-product/groups/developers",
                   "/subscriptions/demo-pilot", "/tags/demo", "/tags/demo-mock"):
        assert BASE + suffix in output
    assert "PRIVATE_" not in output and "NEVER_PRINT_THIS_KEY" not in output
    assert "remain as audit records" in output
    assert not harness.deletes
    assert cloud == harness.inventory
    assert before == {str(p): p.read_bytes() for p in harness.root.rglob("*") if p.is_file()}
    assert all(not any(term in str(arg).casefold() for term in ("listsecrets", "login", "azd"))
               for args in harness.calls for arg in args)


def test_apply_only_exact_reviewed_ids_and_order_then_idempotent(harness, capsys):
    preview_plan = rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    token = harness.preview(capsys)
    harness.invoke("--yes", "--review-token", token)
    output = capsys.readouterr().out
    assert "absence verified" in output
    assert harness.deletes == [entry.resource_id for entry in preview_plan.deletions]
    ids = harness.deletes
    assert ids.index(BASE + "/apis/demo-things-mcp/tools/get-thing") < ids.index(BASE + "/apis/demo-things-mcp")
    assert ids.index(BASE + "/apis/demo-things-mcp") < ids.index(BASE + "/apis/demo-things")
    assert ids.index(BASE + "/subscriptions/demo-pilot") < ids.index(BASE + "/products/demo-product")
    assert ids.index(BASE + "/products/demo-product/apis/demo-things") < ids.index(BASE + "/apis/demo-things")
    assert ids.index(BASE + "/products/demo-product") < ids.index(BASE + "/tags/demo")
    for args in harness.calls:
        if "DELETE" not in args:
            continue
        uri = args[args.index("--uri") + 1]
        path = uri.split("?", 1)[0]
        if path == BASE + "/products/demo-product":
            assert "deleteSubscriptions=false" in uri
        if path in {BASE + "/apis/demo-things", BASE + "/apis/demo-things-mcp"}:
            assert "deleteRevisions=false" in uri
    assert harness.inventory["/apis"][0]["name"] == "unrelated"
    assert harness.inventory["/subscriptions"][0]["name"] == "master"
    assert (harness.directory / "mcp-manifest.yaml").is_file()
    harness.calls.clear()
    token = harness.preview(capsys)
    harness.invoke("--yes", "--review-token", token)
    assert not harness.deletes


@pytest.mark.parametrize("token", [None, "wrong"])
def test_yes_requires_review_token(harness, capsys, token):
    with pytest.raises(SystemExit):
        harness.invoke("--yes", *([] if token is None else ["--review-token", token]))
    assert "stale retirement review token" in capsys.readouterr().err
    assert not harness.deletes


@pytest.mark.parametrize("change", ["manifest", "policy", "account", "gateway"])
def test_stale_changes_block_before_any_mutation(harness, capsys, change):
    token = harness.preview(capsys)
    if change == "manifest":
        harness.manifest["displayName"] = "Changed"
        harness.save()
    elif change == "policy":
        harness.inventory["/apis/demo-things/policies"][0]["properties"]["value"] = "Changed"
    elif change == "account":
        harness.account["user"] = "another@example.test"
    else:
        harness.gateway["sku"]["name"] = "StandardV2"
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", token)
    assert not harness.deletes


def test_concurrent_change_between_deletes_stops_remaining_operations(harness, capsys):
    token = harness.preview(capsys)
    def changed(_):
        harness.add("/products/foreign", {})
        harness.add("/products/foreign/apis/demo-things", {}, canonical="/apis/demo-things")
    harness.after_delete = changed
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", token)
    assert len(harness.deletes) == 1
    assert harness.inventory["/apis/demo-things/operations"]


def test_change_during_final_inventory_blocks_all_deletes(harness, capsys, monkeypatch):
    token = harness.preview(capsys)
    original = rc.inspect_retirement
    reads = 0
    def inspect(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            harness.inventory["/apis/demo-things/policies"][0]["properties"]["value"] = "concurrent change"
        return original(*args, **kwargs)
    monkeypatch.setattr(rc, "inspect_retirement", inspect)
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", token)
    assert not harness.deletes


@pytest.mark.parametrize("change", ["local", "account"])
def test_checks_after_initial_review_and_during_retirement(harness, capsys, monkeypatch, change):
    token = harness.preview(capsys)
    original = rc.inspect_retirement
    reads = 0
    def inspect(*args, **kwargs):
        nonlocal reads
        result = original(*args, **kwargs)
        reads += 1
        if reads == 1:
            if change == "local":
                harness.manifest["displayName"] = "concurrent local write"
                harness.save()
            else:
                harness.account["user"] = "different@example.test"
        return result
    monkeypatch.setattr(rc, "inspect_retirement", inspect)
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", token)
    assert not harness.deletes


def test_policy_shards_and_native_child_policies_are_included_without_generated_index(harness, capsys):
    harness.add("/apis/demo-agent-shard-1", {"type": "http"})
    harness.add("/apis/demo-agent-shard-1/tags/demo", {"displayName": "demo"}, canonical="/tags/demo")
    harness.add("/apis/demo-agent-shard-1/operations/mcp", {"method": "POST"})
    harness.add("/apis/demo-agent-shard-1/operations/mcp/policies/policy", {"value": "PRIVATE_OP_POLICY"})
    harness.add("/apis/demo-things-mcp/policies/policy", {"value": "PRIVATE_NATIVE_POLICY"})
    assert not (harness.directory / "generated").exists()
    plan = rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    covered = {rid for deletion in plan.deletions for rid in deletion.ids}
    assert BASE + "/apis/demo-things-mcp/policies/policy" in covered
    assert BASE + "/apis/demo-agent-shard-1/operations/mcp/policies/policy" in covered
    token = harness.preview(capsys)
    harness.invoke("--yes", "--review-token", token)
    assert BASE + "/apis/demo-agent-shard-1" in harness.deletes


@pytest.mark.parametrize("scenario", [
    "missing-api-tag", "wrong-api-prefix", "missing-product-tag", "foreign-api-in-product",
    "extra-product", "foreign-tag", "operation-tag", "extra-subscription", "api-subscription",
    "pilot-other-product", "named-value", "backend", "external-tag", "native-reference",
    "owned-tool-foreign-source", "api-diagnostic", "api-revision", "api-version", "tag-display",
])
def test_ownership_and_relationships_fail_closed(harness, capsys, scenario):
    if scenario == "missing-api-tag":
        harness.remove("/apis/demo-things/tags/demo")
    elif scenario == "wrong-api-prefix":
        harness.add("/apis/unrelated/tags/demo", {}, canonical="/tags/demo")
    elif scenario == "missing-product-tag":
        harness.remove("/products/demo-product/tags/demo")
    elif scenario == "foreign-api-in-product":
        harness.add("/products/demo-product/apis/unrelated", {}, canonical="/apis/unrelated")
    elif scenario == "extra-product":
        harness.add("/products/other-product", {})
        harness.add("/products/other-product/apis/demo-things", {}, canonical="/apis/demo-things")
    elif scenario == "foreign-tag":
        harness.add("/apis/demo-things/tags/shared", {}, canonical="/tags/shared")
    elif scenario == "operation-tag":
        harness.add("/apis/unrelated/operations/get", {})
        harness.add("/apis/unrelated/operations/get/tags/demo-mock", {}, canonical="/tags/demo-mock")
    elif scenario == "extra-subscription":
        harness.add("/subscriptions/foreign", {"scope": BASE + "/products/demo-product"})
    elif scenario == "api-subscription":
        harness.add("/subscriptions/foreign", {"scope": BASE + "/apis/demo-things"})
    elif scenario == "pilot-other-product":
        harness.inventory["/subscriptions"][0]["properties"]["scope"] = BASE + "/products/other"
    elif scenario == "named-value":
        harness.add("/namedValues/demo-old-secret", {"tags": ["demo"]})
    elif scenario == "backend":
        harness.add("/backends/demo-external", {})
    elif scenario == "external-tag":
        harness.add("/tags/demo-external", {"displayName": "demo-external"})
    elif scenario == "native-reference":
        harness.add("/apis/other-mcp", {"type": "mcp"})
        harness.add("/apis/other-mcp/tools/foreign-tool", {
            "operationId": BASE + "/apis/demo-things/operations/get-thing"})
    elif scenario == "owned-tool-foreign-source":
        harness.inventory["/apis/demo-things-mcp/tools"][0]["properties"]["operationId"] = BASE + "/apis/unrelated/operations/get"
    elif scenario == "api-diagnostic":
        harness.add("/apis/demo-things/diagnostics/applicationinsights", {})
    elif scenario == "api-revision":
        harness.inventory["/apis"][0]["properties"]["apiRevision"] = "2"
    elif scenario == "api-version":
        harness.inventory["/apis"][0]["properties"]["apiVersionSetId"] = BASE + "/apiVersionSets/shared"
    elif scenario == "tag-display":
        harness.inventory["/tags"][0]["properties"]["displayName"] = "Foreign"
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", "anything")
    assert not harness.deletes


@pytest.mark.parametrize("backend", [
    {"mode": "external"}, {"mode": "hosted"}, {}, {"mode": "mock", "outboundAuth": {"secretRef": "demo-secret"}},
    {"mode": "mock", "url": "https://backend.example.test"},
])
def test_bounded_mock_manifest_fails_before_cloud_calls(harness, backend):
    harness.manifest["apis"][0]["backend"] = backend
    harness.save()
    with pytest.raises(SystemExit):
        harness.invoke()
    assert harness.calls == []


@pytest.mark.parametrize("partial", ["no-native", "no-product", "only-product", "no-pilot", "no-mode-tag", "no-resources"])
def test_partial_absent_states_can_be_retired(harness, capsys, partial):
    if partial in {"no-native", "only-product", "no-resources"}:
        harness.remove("/apis/demo-things-mcp")
        harness.remove("/products/demo-product/apis/demo-things-mcp")
    if partial in {"only-product", "no-resources"}:
        harness.remove("/apis/demo-things")
        harness.remove("/products/demo-product/apis/demo-things")
    if partial in {"no-product", "no-resources"}:
        harness.remove("/products/demo-product")
        harness.remove("/subscriptions/demo-pilot")
    if partial == "no-pilot":
        harness.remove("/subscriptions/demo-pilot")
    if partial in {"no-mode-tag", "no-resources"}:
        harness.remove("/tags/demo-mock")
        harness.remove("/apis/demo-things/tags/demo-mock")
    if partial == "no-resources":
        harness.remove("/tags/demo")
    token = harness.preview(capsys)
    harness.invoke("--yes", "--review-token", token)
    assert not rc.inspect_retirement(harness.client, "demo", account=harness.account["user"]).deletions


def test_detached_tags_without_live_anchor_refuse_even_with_yes(harness):
    for suffix in ("/apis/demo-things", "/apis/demo-things-mcp", "/products/demo-product",
                   "/subscriptions/demo-pilot"):
        harness.remove(suffix)
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", "not-an-ownership-proof")
    assert not harness.deletes


@pytest.mark.parametrize("field", ["id", "tenantId"])
def test_wrong_active_context_stops_before_resource_reads(harness, field):
    harness.account[field] = "00000000-0000-0000-0000-000000000999"
    with pytest.raises(SystemExit):
        harness.invoke()
    assert all(args[:3] == ["az", "account", "show"] for args in harness.calls)


def test_async_deletion_waits_for_absence_and_never_reissues_delete(harness, monkeypatch):
    deletion = rc.Deletion(BASE + "/apis/demo-things")
    original = harness.client
    calls = []
    def runner(args):
        calls.append(args)
        if "DELETE" in args:
            return ""
        return json.dumps({"value": [{"name": "demo-things"}] if len(calls) < 4 else []})
    original.runner = runner
    original.delete_reviewed(deletion)
    assert sum("DELETE" in args for args in calls) == 1
    assert len(calls) == 4


def test_delete_timeout_does_not_continue_or_retry(harness, capsys):
    token = harness.preview(capsys)
    def restore(suffix):
        harness.add(suffix, {"operationId": BASE + "/apis/demo-things/operations/get-thing"})
    harness.after_delete = restore
    with pytest.raises(SystemExit):
        harness.invoke("--yes", "--review-token", token)
    assert len(harness.deletes) == 1


def test_malformed_scope_and_pagination_cannot_broaden_target(harness):
    harness.inventory["/apis"][0]["id"] = BASE + "-other/apis/demo-things"
    with pytest.raises(ReconcileError, match="scope"):
        rc.inspect_retirement(harness.client, "demo", account=harness.account["user"])
    client = rc.RetirementClient(SUB, "fixture", "fixture-apim", runner=lambda _: json.dumps({
        "value": [], "nextLink": "https://example.test/steal"}))
    with pytest.raises(ReconcileError, match="endpoint"):
        client.records("/subscriptions")


def test_provider_failure_never_logs_output_or_secret(harness, monkeypatch, capsys):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(rc.shutil, "which", lambda _: "az")
    monkeypatch.setattr(rc.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout=io.BytesIO(), stderr=io.BytesIO(),
        communicate=lambda **kw: (b"NEVER_PRINT_PRIMARY_KEY", b"NEVER_PRINT_SECONDARY_KEY")))
    with pytest.raises(installed.ReconcileError, match="suppressed"):
        installed.run(["az", "rest"], capture=True)
    output = capsys.readouterr()
    assert "NEVER_PRINT" not in output.out + output.err
    assert not harness.deletes


@pytest.mark.parametrize("executable,payload,expected", [
    ("az", [0x97], "\u2014"),
    ("python", [0xE2, 0x80, 0x94], "\u2014"),
])
def test_wrapper_decodes_azure_os_locale_and_python_utf8(tmp_path, monkeypatch, executable, payload, expected):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(installed, "REPO_ROOT", tmp_path)
    # Substitute a local byte-emitting Python process: this never invokes Azure.
    monkeypatch.setattr(installed.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(installed.locale, "getencoding", lambda: "cp1252")
    result = installed.run([
        executable, "-I", "-X", "utf8", "-c",
        f"import sys; sys.stdout.buffer.write(bytes({payload!r}))",
    ], capture=True)
    assert result == expected


def test_wrapper_never_replaces_invalid_azure_output(tmp_path, monkeypatch):
    from mcp_openapi_creator_kit.runtime import command
    installed = command("retire-client")
    monkeypatch.setattr(installed, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(installed.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(installed.locale, "getencoding", lambda: "cp1252")
    with pytest.raises(UnicodeDecodeError):
        installed.run(["az", "-I", "-c", "import sys; sys.stdout.buffer.write(bytes([0x81]))"], capture=True)


@pytest.mark.parametrize("profile", ["native-mcp", "rest-consumption", "policy-mcp-consumption"])
def test_installed_cli_dispatches_all_retirement_profiles(harness, capsys, monkeypatch, profile):
    from mcp_openapi_creator_kit import cli, runtime
    installed = runtime.command("retire-client")
    monkeypatch.setattr(installed, "run", harness.run)
    monkeypatch.setattr(installed, "local_python", lambda _: sys.executable)
    assert cli.main([
        "--workspace", str(harness.root), "retire", "clients/demo",
        "--subscription", SUB, "--tenant", TENANT, "--resource-group", "fixture",
        "--apim-name", "fixture-apim", "--profile", profile, "--confirm-subscription", SUB,
    ]) == 0
    assert "Retirement DRY-RUN" in capsys.readouterr().out
    assert not harness.deletes


def test_cli_and_packaged_registration_uses_trusted_command(tmp_path, capsys):
    from mcp_openapi_creator_kit import cli, runtime, _build
    assert runtime.COMMANDS["retire"] == "retire-client"
    assert "retire-client" in _build.COMMANDS
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "retire-client.py").write_text("raise RuntimeError('CUSTOMER_EXECUTED')", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--workspace", str(tmp_path), "retire", "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "--review-token" in output and "--confirm-subscription" in output
    assert "CUSTOMER_EXECUTED" not in output
    assert Path(runtime.command("retire-client").__file__).resolve() == TOOLS / "retire-client.py"


def test_real_build_stages_retirement_and_integrity_hash(tmp_path):
    from setuptools import Distribution, find_packages
    from mcp_openapi_creator_kit._build import BuildPy
    root = TOOLS.parent
    distribution = Distribution({
        "packages": find_packages(str(root / "src")),
        "package_dir": {"": str(root / "src")},
        "package_data": {"mcp_openapi_creator_kit": ["schemas/*.json", "py.typed"]},
    })
    distribution.script_name = str(root / "setup.py")
    build = BuildPy(distribution)
    build.ensure_finalized()
    build.build_lib = str(tmp_path / "installed")
    build.run()
    package = Path(build.build_lib) / "mcp_openapi_creator_kit"
    staged = package / "_commands" / "retire-client.py"
    assert staged.read_bytes() == (TOOLS / "retire-client.py").read_bytes()
    manifest = json.loads((package / "assets" / "manifest.json").read_text("utf-8"))
    assert manifest["tools/retire-client.py"] == rc.hashlib.sha256(staged.read_bytes()).hexdigest()
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from mcp_openapi_creator_kit.assets import verify_assets; "
            "assert verify_assets()['verifiedFiles'] > 0; "
            "from mcp_openapi_creator_kit.runtime import command; "
            "assert command('retire-client').__package__ == 'mcp_openapi_creator_kit._commands'; "
            "from mcp_openapi_creator_kit.cli import main; main(['retire', '--help'])")
    result = subprocess.run([sys.executable, "-I", "-c", code, build.build_lib],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--confirm-subscription" in result.stdout
