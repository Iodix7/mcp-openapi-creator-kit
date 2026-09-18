"""Executed with python -I -c from a clean installed-wheel customer directory."""
import asyncio
import contextlib
import hashlib
import io
import importlib
import json
import os
import re
from pathlib import Path
import sys
import sysconfig
import urllib.request
from unittest.mock import patch

AUDIT_PROGRAM = '''
import os, sys
def audit(event, args):
    if event not in {"open", "os.listdir", "os.scandir"} or not args:
        return
    path = args[0]
    if isinstance(path, (str, bytes, os.PathLike)):
        absolute = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        if absolute == denied or absolute.startswith(denied + os.sep):
            raise AssertionError("Installed runtime attempted to read source checkout")
sys.addaudithook(audit)
'''

def main(source_root: str):
    denied = os.path.normcase(os.path.abspath(source_root))
    exec(AUDIT_PROGRAM, {"denied": denied})
    import mcp_openapi_creator_kit as package
    from mcp import Client
    from mcp_openapi_creator_kit.assets import kit_root, verify_assets
    from mcp_openapi_creator_kit.cli import main as cli
    from mcp_openapi_creator_kit.runtime import command
    from mcp_openapi_creator_kit.server import create_server
    from mcp_openapi_creator_kit.workspace import WorkspaceReader

    root = Path.cwd()
    assert root.is_dir() and not list(root.iterdir())
    assert Path(package.__file__).is_relative_to(Path(sys.prefix))
    assert verify_assets()["source"] == "installed-package"
    assert verify_assets()["verifiedFiles"] > 20
    assert kit_root().is_relative_to(Path(sys.prefix))
    recovery = importlib.import_module("mcp_openapi_creator_kit._commands.deployment_recovery")
    assert Path(recovery.__file__).is_relative_to(Path(sys.prefix))
    recovery_payloads = importlib.import_module(
        "mcp_openapi_creator_kit._commands.deployment_recovery_payloads")
    assert Path(recovery_payloads.__file__).is_relative_to(Path(sys.prefix))

    def call(*args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli(list(args))
        assert result in (None, 0), (args, result)
        return output.getvalue()

    proposal = call(
        "provision", "--account", "operator@example.test",
        "--subscription", "11111111-1111-4111-8111-111111111111",
        "--tenant", "22222222-2222-4222-8222-222222222222",
        "--resource-group", "fictional-existing-group", "--apim-name", "fictional-new-gateway",
        "--location", "westeurope", "--publisher-name", "Fictional publisher",
        "--publisher-email", "publisher@example.test",
        "--profile", "policy-mcp-consumption", "--dry-run",
    )
    assert '"status": "dry-run"' in proposal and '"applied": false' in proposal
    assert not list(root.iterdir())
    group = call(
        "provision-group", "--account", "operator@example.test",
        "--subscription", "11111111-1111-4111-8111-111111111111",
        "--tenant", "22222222-2222-4222-8222-222222222222",
        "--resource-group", "fictional-new-group", "--location", "northeurope",
        "--dry-run",
    )
    assert '"status": "dry-run"' in group and '"applied": false' in group
    assert not list(root.iterdir())
    assert (kit_root() / "platform" / "resource-group.bicep").is_file()
    assert Path(command("provision-resource-group").__file__).is_relative_to(Path(sys.prefix))
    examples = json.loads(call("examples"))
    assert examples["writes"] is False and examples["activeClients"] == []
    assert len(examples["scenarios"]) >= 2
    library_preview = json.loads(call("import-example", "fsi-rm360", "library-preview"))
    assert library_preview["writes"] is False
    assert not list(root.iterdir())

    async def surfaces():
        runtime = create_server(root)
        async with Client(runtime.mcp, mode="legacy") as client:
            tools = (await client.list_tools()).tools
            assert all(tool.annotations.read_only_hint for tool in tools)
            for workflow in ("discovery", "onboarding", "lifecycle"):
                guide = await client.call_tool("workflow-guide", {"workflow": workflow})
                assert not guide.is_error
                text = guide.structured_content["procedure"]["content"]
                assert len(text) > 1000
                resource = await client.read_resource("kit://skills/" + workflow)
                prompt = await client.get_prompt(workflow)
                assert resource.contents[0].text == text
                assert text in prompt.messages[0].content.text
            info = await client.call_tool("kit-info", {})
            assert info.structured_content["source"] == "installed-package"
            refs = info.structured_content["references"]
            for name in refs:
                ref = await client.call_tool("kit-reference", {"name": name})
                assert not ref.is_error and ref.structured_content["content"]
            builtin = await client.call_tool("catalog-search", {"source": "builtin"})
            assert builtin.structured_content["total"] == 5
            dashboard = await client.call_tool("dashboard-get-url", {})
            with urllib.request.urlopen(dashboard.structured_content["url"]) as response:
                html = response.read().decode()
                assert response.status == 200 and "--cp-bg" in html
                assert "Customer workspace" in html
            assert WorkspaceReader(root).manifests() == []
        assert not runtime.dashboard.running

    asyncio.run(surfaces())
    async def stdio_surface():
        from mcp import StdioServerParameters, stdio_client
        bootstrap = ("denied = " + repr(denied) + "\n" + AUDIT_PROGRAM +
                     "\nfrom mcp_openapi_creator_kit.__main__ import main\nmain()\n")
        params = StdioServerParameters(command=sys.executable, args=["-I", "-c", bootstrap], cwd=root)
        async with Client(stdio_client(params), mode="legacy") as client:
            assert client.protocol_version == "2025-11-25"
            assert (await client.call_tool("workspace-status", {})).structured_content["workspaceMode"] == "empty"
            guide = await client.call_tool("workflow-guide", {"workflow": "onboarding"})
            assert guide.structured_content["procedure"]["provenance"]["source"] == "installed-package"
            dashboard = await client.call_tool("dashboard-get-url", {})
            with urllib.request.urlopen(dashboard.structured_content["url"], timeout=5) as response:
                assert response.status == 200 and "--cp-bg" in response.read().decode()
    asyncio.run(stdio_surface())
    call("init")
    assert list(root.iterdir()) == []
    call("init", "--write")
    assert {path.name for path in root.iterdir()} == {"clients", "apis", "docs"}
    call("import-sample", "acme")
    assert not list((root / "clients").iterdir())
    call("import-sample", "acme", "--write")
    call("import-sample", "other", "--write")
    assert not (root / "clients" / "sample").exists()
    # Deliberately hostile local "kit" surfaces must never execute/override.
    (root / "tools").mkdir()
    (root / "tools" / "build-facade.py").write_text("raise AssertionError('customer code ran')")
    (root / "AGENTS.md").write_text("EVIL_GUIDANCE")
    (root / "skills").mkdir()
    (root / "skills" / "discovery.md").write_text("EVIL_GUIDANCE")
    (root / "catalog").mkdir()
    (root / "catalog" / "template.html").write_text("<script>EVIL_TEMPLATE</script>")
    call("build", "clients/acme")
    call("build-policy", "clients/acme")
    generated = root / "clients" / "acme" / "generated"
    assert not (root / "clients" / "other" / "generated").exists()
    assert not (root / "infra").exists()
    for profile in ("native-mcp", "rest-consumption", "policy-mcp-consumption"):
        call("validate", "--profile", profile, "clients/acme")
    call("catalog", "--write")
    html = (root / "catalog" / "generated" / "catalog.html").read_text("utf-8")
    assert "EVIL_TEMPLATE" not in html
    assert json.loads(call("target-report", "acme"))["offlineReady"]
    call("export", "acme", "--report")
    before = {str(p.relative_to(generated)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in generated.rglob("*") if p.is_file()}
    call("build", "clients/acme")
    call("build-policy", "clients/acme")
    assert before == {str(p.relative_to(generated)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in generated.rglob("*") if p.is_file()}
    # Exercise rejected writes through the installed dispatcher with source reads
    # denied, including shards and the in-memory import governance overlay.
    import copy
    import yaml

    def snapshot():
        return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
                for p in root.rglob("*")}

    def reject(*args):
        before_rejection = snapshot()
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            try:
                call(*args)
            except SystemExit as error:
                assert error.code in (1, 2)
            else:
                raise AssertionError("Invalid installed write was accepted")
        assert errors.getvalue() and "Traceback" not in errors.getvalue()
        assert snapshot() == before_rejection

    manifest_path = root / "clients" / "acme" / "mcp-manifest.yaml"
    manifest_bytes = manifest_path.read_bytes()
    original = yaml.safe_load(manifest_bytes)
    for field in ("client", "api", "facade"):
        for value in ("../../../../../outside", "%2e%2e%2foutside", str(root.parent)):
            invalid = copy.deepcopy(original)
            if field == "client":
                invalid["client"] = value
            elif field == "api":
                invalid["apis"][0]["name"] = value
            else:
                invalid["mcpExposure"]["facadeName"] = value
            manifest_path.write_text(yaml.safe_dump(invalid), "utf-8")
            for builder in ("build", "build-policy"):
                reject(builder, "clients/acme")
            reject("variant", "acme", "rejected", "--write")
    manifest_path.write_bytes(manifest_bytes)
    call("build-policy", "clients/acme", "--limit-bytes", "10000")
    policy_dir = generated / "policy-mcp"
    shards = json.loads((policy_dir / "servers.json").read_text("utf-8"))["servers"]
    assert len(shards) > 1
    linked_shard = policy_dir / shards[-1]["policyFile"]
    guard = root / "linked-shard"
    os.link(linked_shard, guard)
    reject("build-policy", "clients/acme", "--limit-bytes", "10000")
    guard.unlink()
    call("build-policy", "clients/acme")
    conflict = root / "apis" / "conflicting"
    conflict.mkdir()
    (conflict / "openapi.yaml").write_text(yaml.safe_dump({
        "openapi": "3.0.3", "info": {"title": "Conflict", "version": "1.0.0"},
        "paths": {}, "components": {"schemas": {"Problem": {"type": "string"}}},
    }), "utf-8")
    reject("import-sample", "rejected", "--write")
    (conflict / "openapi.yaml").unlink()
    conflict.rmdir()
    config = json.loads(call("vscode-config"))
    assert config["servers"]["mcp-openapi-creator"]["command"] == sys.executable
    for name in ("verify-mcp", "verify-rest", "deploy"):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                cli([name, "--help"])
            except SystemExit as error:
                assert error.code == 0
    # Mock endpoint transport: discovery only, never Azure/azd management.
    verifier = command("verify-mcp")
    import yaml
    manifest = yaml.safe_load((root / "clients" / "acme" / "mcp-manifest.yaml").read_text("utf-8"))
    methods = []
    expected = sorted(tool for api in manifest["apis"] for tool in api["mcpTools"])

    def rpc(url, key, payload, sid=None):
        methods.append(payload["method"])
        result = {"tools": [{"name": name} for name in expected]} if payload["method"] == "tools/list" else {}
        return {"result": result}, None

    with patch.object(verifier, "mcp_rpc", rpc), \
         patch.object(verifier, "azd_env", side_effect=AssertionError("azd called")), \
         patch.dict(os.environ, {"MCP_KEY": "fictional-offline-key"}):
        call("verify-mcp", "clients/acme", "--gateway-url", "https://gateway.example.invalid",
             "--profile", "native-mcp")
    assert methods == ["initialize", "notifications/initialized", "tools/list"]
    other_path = root / "clients" / "other" / "mcp-manifest.yaml"
    other = yaml.safe_load(other_path.read_text("utf-8"))
    other["targets"] = {
        "consumer": "copilot-studio", "gateway": "ai-gateway-preview",
        "preview": {"region": "eastus2", "restBaseUrls": {
            api["name"]: "https://gateway.example.invalid/other/" + api["name"]
            for api in other["apis"]}},
    }
    other_path.write_text(yaml.safe_dump(other, sort_keys=False), "utf-8")
    call("export", "other")
    assert (other_path.parent / "generated" / "targets" / "ai-gateway-plan.json").is_file()
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            call("export", "other", "--apply")
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("AI Gateway apply was not blocked")
    # Only the external command boundary is simulated. The installed inventory,
    # diagnostics, ownership, secret metadata, reconciler and review checks run.
    import runpy
    fixture = runpy.run_path(sys.argv[2])
    scenarios = fixture["run_scenarios"](root)
    specification = root / "docs" / "acme" / "spec.md"
    specification.parent.mkdir(parents=True, exist_ok=True)
    from mcp_openapi_creator_kit.scenario import inventory, reference_markdown
    narrative = "# Fictional customer-care demo\nStore 004 delay; confirm before rescheduling.\n"
    specification.write_text(narrative, encoding="utf-8")
    preview = json.loads(call("spec-sync", "acme"))
    assert not preview["writes"] and preview["changed"] and preview["diff"]
    assert specification.read_text("utf-8") == narrative
    assert json.loads(call("spec-sync", "acme", "--write"))["writes"]
    assert not json.loads(call("spec-sync", "acme", "--write"))["writes"]
    assert specification.read_text("utf-8").startswith(narrative)
    matching_spec = specification.read_text("utf-8")
    specification.write_text(matching_spec.replace("GET /v1/customer-context", "GET /invented"), encoding="utf-8")
    with contextlib.redirect_stderr(io.StringIO()) as errors:
        try:
            call("prepare", "clients/acme", "--profile", "native-mcp")
        except SystemExit as error:
            assert error.code == 2
            assert "Scenario/contract mismatch" in errors.getvalue()
        else:
            raise AssertionError("Installed prepare accepted an invented scenario route")
    specification.write_text(matching_spec, encoding="utf-8")
    invocation = WorkspaceReader(root).workflow_status("acme", requires_mcp=True,
                                                      gateway_mode="existing").next_invocation
    assert invocation.executable == sys.executable
    assert invocation.arguments[:5] == ["-I", "-m", "mcp_openapi_creator_kit.cli", "--workspace", str(root)]
    call("prepare", "clients/acme", "--profile", "native-mcp")
    from mcp_openapi_creator_kit.workflow import GatewayProvisioningTarget

    new_target = GatewayProvisioningTarget(
        account="operator@example.test",
        subscription="11111111-1111-4111-8111-111111111111",
        tenant="22222222-2222-4222-8222-222222222222",
        resource_group="fictional-new-group", resource_group_mode="new",
        resource_group_location="northeurope", apim_name="fictional-new-gateway",
        location="westeurope", publisher_name="Fictional publisher",
        publisher_email="publisher@example.test",
    )
    group_reader = WorkspaceReader(root)
    group_status = group_reader.workflow_status(
        "acme", requires_mcp=True, gateway_mode="new", selected_profile="native-mcp",
        provisioning_target=new_target,
    )
    assert group_status.current_step.stage == "provision-resource-group"
    assert group_status.next_invocation.cli_arguments[0] == "provision-group"
    assert group_status.next_invocation.executable == sys.executable
    assert group_status.evidence is None and group_status.approval_status == "not-granted"
    existing_group = GatewayProvisioningTarget.model_validate({
        **new_target.model_dump(), "resource_group_mode": "existing", "resource_group_location": None,
    })
    gateway_status = group_reader.workflow_status("acme", provisioning_target=existing_group)
    assert gateway_status.current_step.stage == "provision-gateway"
    assert gateway_status.next_invocation.cli_arguments[0] == "provision"
    assert gateway_status.evidence is None and gateway_status.approval_status == "not-granted"
    from mcp_openapi_creator_kit.runtime import command
    with patch.object(command("deploy-client"), "run", fixture["SyntheticAzure"](root).run):
        call(*fixture["PREVIEW_ARGUMENTS"])
    async def evidence_surfaces():
        from mcp_openapi_creator_kit import gateway
        transport = fixture["SyntheticAzure"](root)
        runtime = create_server(root)
        from mcp.types import ElicitResult
        answers = ["decline", "accept"]
        async def consent(_ctx, params):
            assert not transport.calls
            assert fixture["BASE"] in params.message
            action = answers.pop(0)
            return ElicitResult(action=action, content={"inspect": True} if action == "accept" else None)
        async with Client(runtime.mcp, mode="legacy", elicitation_callback=consent) as client:
            contract = await client.call_tool("scenario-contract", {"client": "acme"})
            assert not contract.is_error and contract.structured_content["check"]["status"] == "consistent"
            assert contract.structured_content["referenceMarkdown"] == reference_markdown(inventory(root, "acme"))
            before = await client.call_tool("workflow-status", {
                "client": "acme", "requires_mcp": True, "gateway_mode": "existing"})
            assert before.structured_content["profile"] is None and not transport.calls
            instruction = before.structured_content["currentStep"]
            assert instruction["source"]["source"] == "installed-package"
            full = await client.read_resource(instruction["source"]["resourceUri"])
            assert instruction["instructions"] in full.contents[0].text
            assert instruction["consent"] in full.contents[0].text
            with patch.object(gateway, "run_azure", transport.run):
                denied = await client.call_tool("inspect-gateway", {"target": {
                    "account": "operator@example.test", "subscription": fixture["SUB"],
                    "tenant": fixture["TENANT"], "resourceGroup": "fixture", "apimName": "fixture-apim"}})
                assert denied.is_error and not transport.calls
                inspected = await client.call_tool("inspect-gateway", {"target": {
                    "account": "operator@example.test", "subscription": fixture["SUB"],
                    "tenant": fixture["TENANT"], "resourceGroup": "fixture", "apimName": "fixture-apim"}})
            assert not inspected.is_error
            result = await client.call_tool("workflow-status", {
                "client": "acme", "evidence_id": inspected.structured_content["evidenceId"]})
            assert result.structured_content["profile"] == "native-mcp"
            assert result.structured_content["completion"] == "preview-recorded"
            assert result.structured_content["currentStep"]["stage"] == "review-plan"
            assert result.structured_content["currentStep"]["completion"]
            assert next(s for s in result.structured_content["steps"] if s["id"] == "preview")["plan"]["reviewToken"]
            assert result.structured_content["approvalStatus"] == "not-granted"
            resource = await client.read_resource("kit://workflow/status")
            assert json.loads(resource.contents[0].text)[0] == result.structured_content
            dashboard = await client.call_tool("dashboard-refresh", {})
            with urllib.request.urlopen(dashboard.structured_content["url"], timeout=5) as response:
                html = response.read().decode("utf-8")
                assert result.structured_content["status"] in html and inspected.structured_content["evidenceId"] in html
                assert "esc(w.currentStep.completion)" in html
                assert "esc(tx(w.currentStep.stage))" in html
            rejected = await client.call_tool("workflow-status", {"evidence_id": "invented"})
            assert rejected.is_error
            assert len(transport.calls) == 3
    asyncio.run(evidence_surfaces())
    # Export from the installed dispatcher, then use its actual MCP descriptor
    # from an unrelated cwd. The source-denial hook also applies to that child.
    bundle = root.parent / "plugin"
    assert not bundle.exists()
    assert all(not bundle.is_relative_to(protected) and not protected.is_relative_to(bundle)
               for protected in (root, Path(source_root), Path(sys.prefix)))
    before_export = snapshot()
    plugin_args = ("--workspace", str(root), "plugin-export", "--output", str(bundle))
    preview = json.loads(call(*plugin_args))
    assert not preview["writes"] and not bundle.exists()
    assert snapshot() == before_export
    exported = json.loads(call(*plugin_args, "--write"))
    assert exported == {**preview, "writes": True}
    assert snapshot() == before_export
    assert exported["plugin"] == {
        "name": "mcp-openapi-creator", "version": package.__version__, "format": "copilot",
    }
    assert exported["kit"] == verify_assets()
    assert exported["files"] == sorted(
        p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    assert exported["installation"]["vscodeSettings"] == {
        "chat.pluginLocations": {str(bundle): True},
    }
    plugin = json.loads((bundle / "plugin.json").read_text("utf-8"))
    assert "$schema" not in plugin
    assert plugin["name"] == "mcp-openapi-creator" and plugin["version"] == package.__version__
    assert plugin["skills"] == ["skills/"] and plugin["agents"] == "agents/"
    assert plugin["mcpServers"] == ".mcp.json"
    assert "hooks" not in plugin
    mcp_config = json.loads((bundle / ".mcp.json").read_text("utf-8"))
    assert "$schema" not in mcp_config
    server = mcp_config["mcpServers"]["mcp-openapi-creator"]
    assert server.get("type", "stdio") == "stdio"
    assert server["command"] == config["servers"]["mcp-openapi-creator"]["command"]
    assert server["args"] == config["servers"]["mcp-openapi-creator"]["args"]
    connection = json.loads((bundle / "connection.json").read_text("utf-8"))
    assert connection["kit"] == verify_assets()
    assert connection["workspace"] == str(root) and connection["interpreter"] == sys.executable
    assert connection["cliPrefix"] == [
        sys.executable, "-I", "-m", "mcp_openapi_creator_kit.cli", "--workspace", str(root),
    ]
    for name, relative in (
        ("create-mcp", "skills/create-mcp/SKILL.md"),
        ("mcp-openapi-creator", "agents/mcp-openapi-creator.agent.md"),
    ):
        text = (bundle / relative).read_text("utf-8")
        assert text.startswith("---\n")
        metadata = yaml.safe_load(text.split("---\n", 2)[1])
        assert metadata["name"] == name and metadata["description"]
    for asset in ("AGENTS.md", "skills/discovery.md", "skills/onboarding.md", "skills/lifecycle.md"):
        copied = bundle / "skills" / "create-mcp" / "references" / asset
        assert copied.read_bytes() == (kit_root() / asset).read_bytes()
        assert "EVIL_GUIDANCE" not in copied.read_text("utf-8")
        assert connection["guidanceSha256"][copied.relative_to(bundle).as_posix()] == hashlib.sha256(
            copied.read_bytes()).hexdigest()
    expected_workflow = WorkspaceReader(root).workflow_status(
        "acme", requires_mcp=True, gateway_mode="existing").model_dump(mode="json", by_alias=True)

    async def plugin_stdio_surface():
        from mcp import StdioServerParameters, stdio_client
        params = StdioServerParameters(command=server["command"], args=server["args"], cwd=bundle)
        async with Client(stdio_client(params), mode="legacy") as client:
            assert client.protocol_version == "2025-11-25"
            info = await client.call_tool("kit-info", {})
            assert not info.is_error
            for key in ("version", "source", "manifestSha256"):
                assert info.structured_content[key] == connection["kit"][key]
            assert info.structured_content["source"] == "installed-package"
            status = await client.call_tool("workspace-status", {})
            assert status.structured_content["root"] == str(root)
            assert {item["id"] for item in status.structured_content["clients"]} == {"acme", "other"}
            for workflow in ("discovery", "onboarding", "lifecycle"):
                guide = await client.call_tool("workflow-guide", {"workflow": workflow})
                assert not guide.is_error
                procedure = guide.structured_content["procedure"]
                assert procedure["provenance"]["source"] == "installed-package"
                copied = bundle / "skills" / "create-mcp" / "references" / "skills" / f"{workflow}.md"
                assert procedure["content"] == copied.read_text("utf-8")
                resource = await client.read_resource("kit://skills/" + workflow)
                assert resource.contents[0].text == procedure["content"]
            contract = await client.call_tool("scenario-contract", {"client": "acme"})
            assert not contract.is_error and contract.structured_content["check"]["status"] == "consistent"
            assert contract.structured_content["referenceMarkdown"] == reference_markdown(inventory(root, "acme"))
            assert not contract.structured_content["specSync"]["changed"]
            workflow = await client.call_tool("workflow-status", {
                "client": "acme", "requires_mcp": True, "gateway_mode": "existing",
            })
            assert not workflow.is_error and workflow.structured_content == expected_workflow
            # Inspection evidence is process-local, not restored/approved merely
            # because a previous CLI synthetic preview left a receipt on disk.
            assert workflow.structured_content["approvalStatus"] == "not-granted"
            assert workflow.structured_content["evidenceStatus"] == "missing"
            step = workflow.structured_content["currentStep"]
            assert step["source"]["source"] == "installed-package"
            full = await client.read_resource(step["source"]["resourceUri"])
            assert step["instructions"] in full.contents[0].text
            invocation = workflow.structured_content["nextInvocation"]
            assert invocation["executable"] == connection["interpreter"]
            assert invocation["arguments"][:5] == connection["cliPrefix"][1:]
            assert invocation["cwd"] == str(root)
            resource = await client.read_resource("kit://workflow/status")
            statuses = json.loads(resource.contents[0].text)
            assert next(item for item in statuses if item["client"] == "acme") == expected_workflow
            dashboard = await client.call_tool("dashboard-refresh", {})
            with urllib.request.urlopen(dashboard.structured_content["url"], timeout=5) as response:
                html = response.read().decode("utf-8")
                assert response.status == 200 and "--cp-bg" in html
                assert "Customer workspace" in html and "acme" in html
                assert expected_workflow["status"] in html
                assert "esc(tx(w.currentStep.stage))" in html
                assert "EVIL_TEMPLATE" not in html and "EVIL_GUIDANCE" not in html

    # -I deliberately ignores PYTHONPATH/PYTHONSTARTUP. A short-lived .pth in
    # this disposable installation preserves the exact exported command/args.
    # Explicit globals keep the audit callback's imports and denied path visible
    # when site.addpackage executes the .pth line with separate globals/locals.
    guard = Path(sysconfig.get_path("purelib")) / "_wheel_plugin_source_guard.pth"
    assert not guard.exists()
    guard.write_text("import sys; exec(" + repr("denied = " + repr(denied) + "\n" + AUDIT_PROGRAM) + ", {})\n",
                     encoding="utf-8")
    try:
        asyncio.run(plugin_stdio_surface())
    finally:
        guard.unlink()
    assert snapshot() == before_export
    call("build", "clients/acme")
    long_client = "fictional-sap-warehouse-demo"
    call("import-sample", long_client, "--write")
    contract_path = root / "apis" / f"customer-care-{long_client}" / "openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text("utf-8"))
    contract.setdefault("components", {}).setdefault("schemas", {})["Quantity"] = {
        "type": "number", "exclusiveMinimum": 0,
    }
    first_operation = next(operation for path in contract["paths"].values()
                           for method, operation in path.items()
                           if method == "get")
    first_operation.setdefault("parameters", []).append({
        "name": "quantity", "in": "query",
        "schema": {"$ref": "#/components/schemas/Quantity"},
    })
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    long_generated = root / "clients" / long_client / "generated"
    for operation in ("build", "build-policy"):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            try:
                call(operation, f"clients/{long_client}")
            except SystemExit as error:
                assert error.code == 1
            else:
                raise AssertionError("Invalid OpenAPI bound was accepted by installed generator")
        assert "exclusiveMinimum" in errors.getvalue()
        assert "OpenAPI 3.0 requires a boolean" in errors.getvalue()
        assert not long_generated.exists()
    contract["components"]["schemas"]["Quantity"] = {
        "type": "number", "minimum": 0, "exclusiveMinimum": True,
    }
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    call("build", f"clients/{long_client}")
    call("build-policy", f"clients/{long_client}")
    from jsonschema import Draft202012Validator
    from mcp_openapi_creator_kit.policy import load_client, tool_input_schema
    _, tool_groups = load_client(root, long_generated.parent)
    schemas = [tool_input_schema(tool) for tools in tool_groups.values() for tool in tools]
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
    quantity = next(schema["properties"]["quantity"] for schema in schemas
                    if "quantity" in schema["properties"])
    assert Draft202012Validator(quantity).is_valid(1)
    assert not Draft202012Validator(quantity).is_valid(0)
    for template in (long_generated / "client.bicep",
                     long_generated / "policy-mcp" / "client.bicep"):
        names = re.findall(r"module [^\n]+\{\n  name: '([^']+)'",
                           template.read_text("utf-8"))
        assert names and all(len(name) <= 64 for name in names)
    long_servers = json.loads((long_generated / "policy-mcp" / "servers.json").read_text("utf-8"))
    assert all(server["resourceName"].startswith(long_client + "-")
               for server in long_servers["servers"])
    print(json.dumps({"installed": package.__file__, "version": package.__version__,
                      "sourceReadsDenied": True,
                      "deploymentScenarios": [{"scenario": s["scenario"], "passed": s["passed"]} for s in scenarios],
                      "checks": "stdio/guides/resources/prompts/dashboard/init/import/build/catalog/targets/export/config/endpoint-mock/transport-boundary-preview/path-preflight/schema-overlay/sharded-hardlink/verified-gateway-workflow/contextual-procedure-provenance/scenario-contract-gate/exact-installed-invocation/spec-sync-preview-write-preserve-idempotent/plugin-export-copilot-pinned-stdio-guides-workflow-dashboard",
                      "resourceGroupWorkflow": "installed dry-run and group-to-gateway proposal transition passed; no Azure",
                      "fieldRegressions": "installed OpenAPI rejection/correction and bounded long-client deployment names",
                      "bicep": [str(generated / "client.bicep"),
                                str(generated / "policy-mcp" / "client.bicep"),
                                str(kit_root() / "platform" / "resource-group.bicep"),
                                str(kit_root() / "platform" / "gateway.bicep"),
                                str(long_generated / "client.bicep"),
                                str(long_generated / "policy-mcp" / "client.bicep")]}))


def compiled_recovery_probe(source_root: str, template_path: str, manifest_path: str):
    denied = os.path.normcase(os.path.abspath(source_root))
    exec(AUDIT_PROGRAM, {"denied": denied})
    import copy
    import yaml
    from mcp_openapi_creator_kit._commands.deployment_recovery_payloads import (
        PayloadMismatch, templates_match,
    )

    current = json.loads(Path(template_path).read_text("utf-8"))
    historical = copy.deepcopy(current)
    manifest = yaml.safe_load(Path(manifest_path).read_text("utf-8"))
    changed = 0
    for name, value in list(historical.get("variables", {}).items()):
        if not isinstance(value, str) or not value.startswith("openapi:"):
            continue
        contract = yaml.safe_load(value)
        quantity = contract.get("components", {}).get("schemas", {}).get("Quantity")
        if quantity is None:
            continue
        assert quantity["minimum"] == 0 and quantity["exclusiveMinimum"] is True
        quantity.pop("minimum")
        quantity["exclusiveMinimum"] = 0
        historical["variables"][name] = yaml.safe_dump(contract, sort_keys=False)
        changed += 1
    assert changed, "Real compiled template did not include the regression contract"
    templates_match(current, historical, manifest, root=True)
    foreign = copy.deepcopy(historical)
    foreign["resources"].append({
        "type": "Microsoft.Resources/deployments", "apiVersion": "2022-09-01",
        "name": "foreign-deployment", "properties": {},
    })
    try:
        templates_match(current, foreign, manifest, root=True)
    except PayloadMismatch:
        pass
    else:
        raise AssertionError("Installed recovery accepted an unrelated deployment")
    print(json.dumps({"compiledRecoveryPayloads": "passed", "sourceReadsDenied": True}))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--compiled-recovery":
        compiled_recovery_probe(sys.argv[1], sys.argv[3], sys.argv[4])
    else:
        main(sys.argv[1])
