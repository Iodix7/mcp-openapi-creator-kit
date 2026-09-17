"""Explicit installed kit CLI; no command executes customer Python or templates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .assets import kit_root, source_info, verify_assets
from .data_paths import safe_data_path, validate_data_tree, workspace_root
from .runtime import COMMANDS, command, invoke


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def initialization_plan(root: Path) -> dict:
    directories = [safe_data_path(root, root / name) for name in ("clients", "apis", "docs")]
    for directory in directories:
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Initialization conflicts with existing file: {directory.name}")
    return {"workspace": str(root), "createDirectories": [
        path.name for path in directories if not path.exists()],
        "writes": False, "sampleImported": False,
        "next": "Repeat init --write; optionally import-sample <new-client> --write"}


def vscode_config(root: Path) -> dict:
    command("local_python").local_python(root)
    return {"servers": {"mcp-openapi-creator": {
        "type": "stdio", "command": str(Path(sys.executable).absolute()),
        "args": ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", str(root)],
    }}}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    root_parser = argparse.ArgumentParser(add_help=False)
    root_parser.add_argument("--workspace", "--root", type=Path, default=Path.cwd())
    root_args, remaining = root_parser.parse_known_args(argv)
    parser = argparse.ArgumentParser(
        prog="mcp-kit",
        description="Installed trusted kit. --workspace/--root selects existing customer data. "
                    "MCP is read-only; this CLI explicitly writes generated files. "
                    "Provision/deploy/retire require explicit cloud/context approval and reviewed plans.",
        parents=[root_parser],
    )
    parser.add_argument("command", nargs="?", choices=[
        *COMMANDS, "init", "import-sample", "examples", "import-example", "catalog", "target-report", "export",
        "vscode-config", "info", "guide", "reference", "workflow-status", "inspect-gateway", "prepare",
        "scenario-contract", "spec-sync", "plugin-export",
    ])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(remaining)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        root = workspace_root(root_args.workspace)
        name = args.command
        arguments = args.arguments
        if name in COMMANDS:
            if name == "build":
                build_parser = argparse.ArgumentParser(
                    prog="mcp-kit build", description="Validate all customer collisions and write selected generated REST/native artifacts")
                build_parser.add_argument("client", nargs="?", help="clients/<id>; omit to build all customer clients")
                build_parser.add_argument("--catalog", nargs="?", const="", choices=["", "schemas"],
                                          metavar="schemas")
                build_parser.parse_args(arguments)
            if "--help" not in arguments and "-h" not in arguments:
                validate_data_tree(root)
                # Positional client/manifest paths cannot broaden the data root.
                for value in arguments:
                    if (value.startswith(("clients/", "clients\\")) or
                            Path(value).is_absolute()):
                        safe_data_path(root, root / value)
            if name in {"deploy", "reconcile-all"} and not (root / "azure.yaml").is_file():
                if name == "reconcile-all":
                    raise ValueError("Full-environment reconciliation is repository-only; use selective deploy.")
                if not any(arg in arguments for arg in ("--help", "-h", "--subscription")):
                    raise ValueError("Customer workspaces require explicit --subscription --tenant "
                                     "--resource-group --apim-name --profile; azd is repository-only.")
            if name in {"verify-mcp", "verify-rest"} and not (root / "azure.yaml").is_file():
                if not any(arg in arguments for arg in ("--help", "-h", "--gateway-url")):
                    raise ValueError("Customer verification requires --gateway-url (and --profile for MCP); "
                                     "legacy azd discovery is repository-only.")
            invoke(name, root, arguments)
            return 0
        if name == "export":
            from .export_cli import main as export
            return export([*arguments, "--root", str(root)])
        sub = argparse.ArgumentParser(prog=f"mcp-kit {name}")
        if name == "prepare":
            sub.description = "Explicit local writes: validate and build one client, then record preparation. No Azure access."
            sub.add_argument("client")
            sub.add_argument("--profile", required=True,
                             choices=["native-mcp", "policy-mcp-consumption", "rest-consumption"])
        if name in {"init", "import-sample", "import-example"}:
            sub.add_argument("--write", action="store_true", help="Explicitly create planned data; never overwrite")
        if name == "import-sample":
            sub.add_argument("client", help="New client slug; all tool IDs receive this prefix")
        if name == "import-example":
            sub.add_argument("scenario", help="Scenario ID from mcp-kit examples")
            sub.add_argument("client", help="New client slug; creates isolated contract/tool variants")
        if name == "catalog":
            sub.add_argument("--source", choices=["workspace", "builtin"], default="workspace")
            sub.add_argument("--write", action="store_true", help="Write customer catalog/generated; not builtin data")
            sub.add_argument("--schemas", action="store_true", help="Include full schemas (already in JSON)")
        if name in {"target-report", "scenario-contract", "spec-sync"}:
            sub.add_argument("client")
        if name == "spec-sync":
            sub.description = "Preview deterministic contract sections in spec.md. Preserve narrative; never replace unmarked tables."
            sub.add_argument("--write", action="store_true", help="Explicitly create/update only the managed contract block")
        if name == "plugin-export":
            sub.description = "Export a native skill, Copilot agent and pinned MCP connection. No host installation or Azure."
            sub.add_argument("--output", type=Path, required=True, help="New bundle directory outside customer data; parent must exist")
            sub.add_argument("--write", action="store_true", help="Explicitly create the reviewed plugin bundle")
        if name in {"workflow-status", "inspect-gateway"}:
            sub.add_argument("--client", default="", help="Client slug, not a path; omit during discovery")
            sub.add_argument("--consumer", choices=["mcp", "rest"], help="Explicit operator consumer choice")
            sub.add_argument("--gateway-mode", choices=["unknown", "new", "existing"], default="unknown")
            sub.add_argument("--selected-profile", choices=["native-mcp", "policy-mcp-consumption", "rest-consumption"])
            sub.add_argument("--external-backend", action="store_true")
            sub.add_argument("--private-network", action="store_true")
        if name == "workflow-status":
            sub.add_argument("--allow-fixed-gateway-cost", action="store_true")
            sub.add_argument("--resource-group-mode", choices=["existing", "new"],
                             help="Proposed existing/new group choice; no Azure inspection or creation")
            sub.add_argument("--resource-group-location",
                             help="Required canonical region for a new resource group, separate from APIM location")
            for flag in ("account", "tenant", "subscription", "resource-group", "apim-name",
                         "location", "publisher-name", "publisher-email"):
                sub.add_argument("--" + flag, help="Proposed new gateway context; never observed evidence")
        if name == "inspect-gateway":
            for flag in ("account", "tenant", "subscription", "resource-group", "apim-name"):
                sub.add_argument("--" + flag, required=True, help="Explicit operator-approved Azure context")
        if name == "guide":
            from .guidance import Workflow
            sub.add_argument("workflow", choices=[item.value for item in Workflow])
        if name == "reference":
            from .guidance import REFERENCES
            sub.add_argument("name", choices=list(REFERENCES))
        parsed = sub.parse_args(arguments)
        if name == "prepare":
            from .progress import begin_step, finish_step
            validate_data_tree(root)
            directory = command("deployment").client_path(root, parsed.client)
            inputs = begin_step(root, directory.name, "prepare")
            invoke("validate", root, ["--profile", parsed.profile, parsed.client])
            from .scenario import require_consistent_spec
            require_consistent_spec(root, directory.name)
            invoke("build", root, [parsed.client])
            if parsed.profile == "policy-mcp-consumption":
                invoke("build-policy", root, [parsed.client])
            finish_step(root, directory.name, "prepare", inputs, profile=parsed.profile)
            print("Local preparation recorded. NOT deployed: inspect workflow-status and complete the approved selective preview.")
        elif name == "init":
            plan = initialization_plan(root)
            if parsed.write:
                for directory in plan["createDirectories"]:
                    safe_data_path(root, root / directory).mkdir(exist_ok=False)
                plan["writes"] = True
            emit(plan)
        elif name == "import-sample":
            validate_data_tree(root)
            variant = command("prepare-variant")
            plan = variant.prepare(root, "sample", parsed.client, source_root=kit_root())
            if parsed.write:
                command("local_python").local_python(root)
                # Parents are customer-owned layout; no kit scripts are copied.
                for directory in ("clients", "apis"):
                    safe_data_path(root, root / directory).mkdir(exist_ok=True)
                variant.apply(root, plan)
            emit({"writes": parsed.write, "files": [str(p.relative_to(root)) for p in plan],
                  "client": parsed.client, "sampleActivated": False})
        elif name == "examples":
            from .examples import list_examples
            emit(list_examples())
        elif name == "import-example":
            from .examples import apply_import, plan_import
            validate_data_tree(root)
            plan = plan_import(root, parsed.scenario, parsed.client)
            if parsed.write:
                command("local_python").local_python(root)
                emit(apply_import(root, plan))
            else:
                emit(plan.public(root))
        elif name == "spec-sync":
            from .scenario import apply_spec_sync, plan_spec_sync
            plan = plan_spec_sync(root, parsed.client)
            result = plan.public(root)
            if parsed.write:
                result["writes"] = apply_spec_sync(root, plan)
            emit(result)
        elif name == "scenario-contract":
            from .scenario import scenario_report
            report = scenario_report(root, parsed.client)
            emit(report)
            return 0 if report["check"]["status"] == "consistent" else 2
        elif name == "vscode-config":
            emit(vscode_config(root))
        elif name == "plugin-export":
            from .plugin import export_plugin
            emit(export_plugin(root, parsed.output, write=parsed.write))
        elif name == "info":
            from .guidance import kit_info
            emit({**kit_info(), "integrity": verify_assets()})
        elif name == "guide":
            from .guidance import workflow_guide
            emit(workflow_guide(parsed.workflow))
        elif name == "reference":
            from .guidance import reference
            emit(reference(parsed.name))
        elif name in {"workflow-status", "inspect-gateway"}:
            from .workspace import WorkspaceReader
            from .workflow import GatewayTarget
            reader = WorkspaceReader(root)
            preferences = {
                "requires_mcp": None if parsed.consumer is None else parsed.consumer == "mcp",
                "gateway_mode": parsed.gateway_mode, "selected_profile": parsed.selected_profile,
                "external_backend": parsed.external_backend, "private_network": parsed.private_network,
            }
            if name == "workflow-status":
                preferences["avoid_fixed_gateway_cost"] = not parsed.allow_fixed_gateway_cost
                fields = ("account", "tenant", "subscription", "resource_group", "apim_name",
                          "location", "publisher_name", "publisher_email",
                          "resource_group_mode", "resource_group_location")
                supplied = {field: getattr(parsed, field) for field in fields if getattr(parsed, field) is not None}
                if supplied:
                    from .workflow import GatewayProvisioningTarget
                    preferences["provisioning_target"] = GatewayProvisioningTarget.model_validate(supplied)
            # Reject bad local inputs before making any cloud request.
            reader.workflow_status(parsed.client, **preferences)
            if name == "inspect-gateway":
                if parsed.gateway_mode == "new":
                    raise ValueError("inspect-gateway is for existing gateways; do not combine it with --gateway-mode new")
                target = GatewayTarget(
                    account=parsed.account, tenant=parsed.tenant, subscription=parsed.subscription,
                    resource_group=parsed.resource_group, apim_name=parsed.apim_name)
                from .consent import confirm_inspection
                confirm_inspection(target, str(root))
                observation = reader.gateway_evidence.inspect(target)
                preferences.update(gateway_mode="existing", evidence_id=observation.evidence_id)
            emit(reader.workflow_status(parsed.client, **preferences).model_dump(mode="json", by_alias=True))
        else:
            from .workspace import WorkspaceReader
            reader = WorkspaceReader(root)
            if name == "target-report":
                emit(reader.target_report(parsed.client))
            elif parsed.write:
                if parsed.source != "workspace":
                    raise ValueError("Starter library is read-only; import-sample explicitly first.")
                from .catalog import write_outputs
                write_outputs(root, root / "catalog" / "generated", root / "catalog" / "metadata.yaml")
            else:
                emit(reader.catalog(parsed.source))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(2, f"mcp-kit: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
