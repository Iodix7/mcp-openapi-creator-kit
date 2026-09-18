#!/usr/bin/env python3
"""Preview/review/apply a generated client on an existing APIM, with or without azd."""
import argparse
import importlib.util
import json
import locale
import os
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

from mcp_openapi_creator_kit.deployment_names import deployment_name

if __package__:
    from .deployment import (check_secrets, client_path, confirm_context, context_from_args,
                            input_fingerprint, inspect_deployments, inspect_resources, plan_token, safe_path,
                            secret_refs, slug, summarize_what_if, template_inventory)
    from .lifecycle import (AzRestClient, ReconcileError, apply_plan, build_plan,
                           desired_state, discover_owned_apis, format_plan)
    from .local_python import local_python
else:
    from deployment import (check_secrets, client_path, confirm_context, context_from_args,
                        input_fingerprint, inspect_deployments, inspect_resources, plan_token, safe_path,
                        secret_refs, slug, summarize_what_if, template_inventory)
    from lifecycle import (AzRestClient, ReconcileError, apply_plan, build_plan,
                       desired_state, discover_owned_apis, format_plan)
    from local_python import local_python

REPO_ROOT = Path(__file__).resolve().parent.parent


def die(message):
    print(f"[deploy-client] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args: list, capture: bool = False) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        raise ReconcileError(f"Command {args[0]} not found")
    environment = os.environ.copy()
    environment.pop("MCP_RECONCILE_APPLY", None)
    process = subprocess.run([executable, *args[1:]], cwd=REPO_ROOT,
                             capture_output=True, env=environment)
    if process.returncode:
        # ARM errors can echo templates, policies and credential values.
        raise ReconcileError(f"Command failed ({args[0]} {args[1]}), exit {process.returncode}. "
                             "No automatic fallback. Check permissions, CLI/Bicep support and "
                             "deployment history privately; raw output is suppressed.")
    encoding = locale.getencoding() if args[0] == "az" and os.name == "nt" else locale.getpreferredencoding(False)
    try:
        output = process.stdout.decode(encoding)
    except UnicodeDecodeError as error:
        raise ReconcileError("Command output could not be decoded; check CLI output encoding. "
                             "Raw output is suppressed.") from error
    if not capture and args[0] != "az":
        print(output, end="")
    return output if capture else ""


def azd_env():
    values = {}
    for line in run(["azd", "env", "get-values"], capture=True).splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            if key.strip() in values:
                raise ReconcileError("Duplicate azd context key")
            values[key.strip()] = value.strip().strip('"')
    return values


def _validate_profile(context, client_dir):
    from mcp_openapi_creator_kit.assets import kit_root
    from mcp_openapi_creator_kit.runtime import command
    # A raw repository wrapper remains compatible, but never loads customer tools.
    if __package__:
        module = command("validate-deployment-profile")
    else:
        spec = importlib.util.spec_from_file_location("deployment_profile", kit_root() / "tools" / "validate-deployment-profile.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.validate(context.profile, [client_dir / "mcp-manifest.yaml"], environment={
        "EXISTING_APIM_NAME": context.apim, "TELEMETRY_MODE": "none",
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", help="clients/<id>")
    parser.add_argument("--subscription")
    parser.add_argument("--tenant")
    parser.add_argument("--resource-group")
    parser.add_argument("--apim-name")
    parser.add_argument("--profile", choices=["native-mcp", "rest-consumption", "policy-mcp-consumption"])
    parser.add_argument("--key-vault-name", help="existing vault; required only for selected secretRefs")
    parser.add_argument("--confirm-subscription", help="approve the displayed complete context non-interactively")
    parser.add_argument("--yes", action="store_true", help="apply after revalidating/displaying the reviewed plan")
    parser.add_argument("--review-token", help="token from the preceding preview, required with --yes")
    parser.add_argument("--recover-detached-tags", action="store_true",
                        help="explicitly prove and preview retry of a failed mock import's detached tags; never deletes tags")
    args = parser.parse_args()
    try:
        python = local_python(REPO_ROOT)
        client_dir = client_path(REPO_ROOT, args.client)
        try:
            manifest = yaml.safe_load((client_dir / "mcp-manifest.yaml").read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ReconcileError("Invalid manifest YAML; fix it locally (contents suppressed)") from error
        if not isinstance(manifest, dict) or manifest.get("client") != client_dir.name:
            raise ReconcileError("Manifest client must match the selected folder")
        if not isinstance(manifest.get("apis"), list) or not manifest["apis"]:
            raise ReconcileError("Manifest apis must be a non-empty list")
        if not all(isinstance(api, dict) for api in manifest["apis"]):
            raise ReconcileError("Each manifest API must be an object")
        if __package__:
            from mcp_openapi_creator_kit.progress import begin_step, finish_step
            progress_inputs = begin_step(REPO_ROOT, client_dir.name, "preview")
        # Check every generator input/output before running code that writes artifacts.
        for directory in ("clients", "apis", "infra", "modules"):
            directory_path = safe_path(REPO_ROOT, REPO_ROOT / directory)
            for path in directory_path.rglob("*"):
                safe_path(REPO_ROOT, path)
        for api in manifest.get("apis", []):
            safe_path(REPO_ROOT, REPO_ROOT / "apis" / slug(api["name"]) / "openapi.yaml")
        context = context_from_args(args, azd_env)
        if secret_refs(manifest) and not context.key_vault:
            raise ReconcileError("Selected secretRefs require --key-vault-name (or existing azd keyVaultName)")
        _validate_profile(context, client_dir)
        from mcp_openapi_creator_kit.runtime import child_command
        run(child_command(REPO_ROOT, "build", args.client) if __package__
            else [python, str(Path(__file__).parent / "build-facade.py"), args.client])
        if context.profile == "policy-mcp-consumption":
            run(child_command(REPO_ROOT, "build-policy", args.client) if __package__
                else [python, str(Path(__file__).parent / "build-policy-mcp.py"), args.client])
        fingerprint = input_fingerprint(REPO_ROOT, client_dir, manifest)
        account = confirm_context(context, args.confirm_subscription, run)
        client = AzRestClient(context.subscription, context.resource_group, context.apim,
                              runner=lambda command: run(command, capture=True))
        state = inspect_resources(client, context, manifest, client_dir, account=account["user"],
                                  recover_detached_tags=args.recover_detached_tags, run=run)
        if state.get("tagRecovery"):
            print("[deploy-client] Detached-tag recovery: verified live failed-import creation evidence; "
                  "retry same client, no tag DELETEs")
            for name in state["tagRecovery"]["tags"]:
                print(f"  reuse {client.base}/tags/{name}")
        state["secrets"] = check_secrets(context, manifest, state["gateway"], run)
        state["deployments"] = inspect_deployments(context, client, manifest, client_dir, state, run)
        desired = desired_state(client_dir, context.profile)
        actual = discover_owned_apis(client, manifest["client"])
        deletion_plan = build_plan(client, desired, actual)
        deletes = format_plan(deletion_plan)
        if state.get("tagRecovery") and deletes:
            raise ReconcileError("Detached-tag recovery cannot authorize reconciliation DELETEs; preview normal lifecycle separately")
        print("[deploy-client] Reconciliation DRY-RUN")
        print("\n".join("  " + line for line in deletes) or "  no orphans")
        deployments = []
        changes = []
        for template, allowed, required in template_inventory(client, manifest, context.profile, client_dir):
            name = deployment_name(
                ("policy-mcp-client-" if template.startswith("policy-mcp/") else "client-") + client_dir.name)
            command = [
                "az", "deployment", "group", "create", "--subscription", context.subscription,
                "--resource-group", context.resource_group, "--name", name,
                "--mode", "Incremental", "--template-file",
                str(client_dir / "generated" / template), "--parameters", f"apimName={context.apim}",
            ]
            if template == "client.bicep":
                command += [f"keyVaultName={context.key_vault}",
                            f"enableNativeMcp={str(context.profile == 'native-mcp').lower()}"]
            preview = command.copy()
            preview[3] = "what-if"
            result = json.loads(run([*preview, "--no-pretty-print", "--result-format",
                                     "ResourceIdOnly", "--output", "json"], capture=True))
            summary = summarize_what_if(result, allowed, required)
            changes.extend(summary)
            deployments.append(command)
        print("[deploy-client] ARM what-if (resource identifiers/types/change types only)")
        for change in changes:
            print(f"  {change['changeType']} {change['resourceType']} {change['resourceId']}")
        token = plan_token(context, fingerprint, state, deletes, changes)
        print(f"[deploy-client] Review token: {token}")
        print("Simulation is not a guarantee: concurrent Azure changes and provider behavior remain possible.")
        if not args.yes:
            if __package__:
                from mcp_openapi_creator_kit.workflow import GatewayTarget
                target = GatewayTarget(subscription=context.subscription, tenant=context.tenant,
                                       resource_group=context.resource_group, apim_name=context.apim,
                                       account=account["user"])
                finish_step(REPO_ROOT, client_dir.name, "preview", progress_inputs,
                            profile=context.profile, target=target.model_dump(mode="json", by_alias=True),
                            plan={"reviewToken": token, "changes": changes, "deletions": deletes})
            print("Preview only. Review both plans; rerun identical context with --yes --review-token <token>.")
            return
        if args.review_token != token:
            raise ReconcileError("Missing/stale review token: review this refreshed plan before applying")
        if input_fingerprint(REPO_ROOT, client_dir, manifest) != fingerprint:
            raise ReconcileError("Local inputs changed during review")
        from mcp_openapi_creator_kit.gateway import verify_active_account
        from mcp_openapi_creator_kit.workflow import GatewayTarget
        verify_active_account(
            GatewayTarget(subscription=context.subscription, tenant=context.tenant,
                          resource_group=context.resource_group, apim_name=context.apim, account=account["user"]),
            lambda command: run(command, capture=True))
        refreshed = inspect_resources(client, context, manifest, client_dir, account=account["user"],
                                      recover_detached_tags=args.recover_detached_tags, run=run)
        refreshed["secrets"] = check_secrets(context, manifest, refreshed["gateway"], run)
        refreshed["deployments"] = inspect_deployments(context, client, manifest, client_dir, refreshed, run)
        refreshed_plan = build_plan(client, desired, discover_owned_apis(client, manifest["client"]))
        if plan_token(context, fingerprint, refreshed, format_plan(refreshed_plan), changes) != token:
            raise ReconcileError("Azure inventory changed during review; preview again")
        if input_fingerprint(REPO_ROOT, client_dir, manifest) != fingerprint:
            raise ReconcileError("Local inputs changed during final inventory check")
        apply_plan(client, deletion_plan)
        for command in deployments:
            run([*command, "--output", "none"])
        print(f"[deploy-client] {client_dir.name}: deployment completed")
        print("Next: obtain the HTTPS gateway origin and product key through your approved APIM process.")
        print("Set MCP_KEY privately in the verifier process environment; never paste the key into commands or logs.")
        verifier = "verify-rest" if context.profile == "rest-consumption" else "verify-mcp"
        profile_flag = "" if context.profile == "rest-consumption" else f" --profile {context.profile}"
        print(f"Run from the customer root with the installed CLI: mcp-kit {verifier} clients/{client_dir.name}"
              f" --gateway-url <approved-https-origin>{profile_flag}")
        print("Explicit verification uses no azd or Azure management discovery. "
              "Subscription-key auth only; REST requires all backends mock. See docs/selective-deployment.md.")
    except (ReconcileError, RuntimeError, ValueError, KeyError, OSError) as error:
        die(str(error))


if __name__ == "__main__":
    main()
