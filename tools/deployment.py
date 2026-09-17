"""Fail-closed, read-only preflight and redacted ARM planning for one client.

REST shapes follow APIM 2024-05-01/2025-09-01-preview. Never request listSecrets.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from mcp_openapi_creator_kit.workflow import (
    GatewayFacts, GatewayObservation, GatewayTarget, diagnostic_violations,
    evaluate_workflow, gateway_violations,
)

if __package__:
    from .lifecycle import API_VERSION, AzRestClient, ReconcileError, desired_state
else:
    from lifecycle import API_VERSION, AzRestClient, ReconcileError, desired_state

PROFILES = {"native-mcp", "rest-consumption", "policy-mcp-consumption"}


def slug(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", value):
        raise ReconcileError("Names must be lowercase kebab-case slugs, at most 80 characters")
    return value


def safe_path(root: Path, path: Path) -> Path:
    root = root.resolve()
    candidate = path.absolute()
    if not candidate.is_relative_to(root):
        raise ReconcileError("Path must remain within the repository")
    for parent in [candidate, *candidate.parents]:
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ReconcileError("Symlink/junction inputs and outputs are not supported")
    if not candidate.resolve().is_relative_to(root):
        raise ReconcileError("Path escapes the repository")
    if candidate.is_file() and candidate.stat().st_nlink != 1:
        raise ReconcileError("Hard-linked inputs/outputs are not supported")
    return candidate


def client_path(root: Path, raw: str) -> Path:
    path = safe_path(root, root / raw)
    if path.parent != root / "clients":
        raise ReconcileError("Select exactly clients/<client-id>")
    slug(path.name)
    safe_path(root, path / "mcp-manifest.yaml")
    if not (path / "mcp-manifest.yaml").is_file():
        raise ReconcileError("Client manifest is missing")
    return path


@dataclass(frozen=True)
class Context:
    subscription: str
    tenant: str
    resource_group: str
    apim: str
    profile: str
    key_vault: str = ""
    azd_environment: str = "not used (explicit Azure CLI context; azd auth not checked)"

    def validate(self):
        for name in ("subscription", "tenant"):
            try:
                uuid.UUID(getattr(self, name))
            except (ValueError, AttributeError) as error:
                raise ReconcileError(f"{name} must be a complete UUID") from error
        if not re.fullmatch(r"[A-Za-z0-9_().-]{1,90}", self.resource_group) or self.resource_group.endswith("."):
            raise ReconcileError("Invalid resource group name")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,49}", self.apim):
            raise ReconcileError("Invalid APIM name")
        if self.key_vault and not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]", self.key_vault):
            raise ReconcileError("Invalid existing Key Vault name")
        if self.profile not in PROFILES:
            raise ReconcileError("Unknown gateway profile")


def normalized_environment(values: dict) -> dict:
    normalized = {}
    for key, value in values.items():
        name = key.replace("_", "").lower()
        if name in normalized and normalized[name] != value:
            raise ReconcileError("Ambiguous azd output aliases; fix the environment")
        normalized[name] = value
    return normalized


def context_from_args(args, azd_env) -> Context:
    fields = (args.subscription, args.tenant, args.resource_group, args.apim_name, args.profile)
    if any(fields) or args.key_vault_name:
        if not all(fields):
            raise ReconcileError("Explicit context requires --subscription, --tenant, "
                                 "--resource-group, --apim-name and --profile; no azd fallback")
        context = Context(*fields, key_vault=args.key_vault_name or "")
    else:
        env = normalized_environment(azd_env())
        required = ("azuresubscriptionid", "azuretenantid", "azureresourcegroup",
                    "apimname", "gatewayprofile", "azureenvname")
        if not all(env.get(name) for name in required):
            raise ReconcileError("Incomplete azd outputs/context. Supply complete explicit "
                                 "flags for an existing APIM without running azd up.")
        context = Context(*(env[name] for name in required[:5]),
                          key_vault=env.get("keyvaultname", ""),
                          azd_environment=env["azureenvname"] + " (outputs only; azd auth not used/checked)")
    context.validate()
    return context


def confirm_context(context: Context, confirmation: str | None, run):
    # This reads the active CLI account, not a target selected with --subscription.
    # Every resource request below is additionally pinned to the approved subscription.
    account = json.loads(run(["az", "account", "show", "--query",
                              "{name:name,user:user.name,tenantId:tenantId,id:id}",
                              "--output", "json"], capture=True))
    if (str(account.get("id", "")).casefold() != context.subscription.casefold() or
            str(account.get("tenantId", "")).casefold() != context.tenant.casefold()):
        raise ReconcileError("Active Azure CLI tenant/subscription differs from requested context; "
                             "align it yourself before continuing")
    if not account.get("user"):
        raise ReconcileError("Active Azure CLI account identity is missing; cannot approve an incomplete context")
    print("[deploy-client] Context (no resource preview before approval)")
    for name, value in {
        "Account": account["user"],
        "Tenant": context.tenant, "Subscription": context.subscription,
        "azd environment": context.azd_environment, "Resource group": context.resource_group,
        "APIM": context.apim, "GATEWAY_PROFILE": context.profile,
        "Existing Key Vault": context.key_vault or "(not supplied)",
    }.items():
        print(f"  {name}: {value}")
    if confirmation is None:
        confirmation = input("Approve this context by retyping the subscription ID: ").strip()
    if confirmation.casefold() != context.subscription.casefold():
        raise ReconcileError("Context confirmation does not match subscription")
    return account


def existing_apim_violations(profile: str, apim: dict, network: str) -> list[str]:
    try:
        facts = GatewayFacts.from_arm(apim)
    except ValueError as error:
        return [str(error)]
    return gateway_violations(profile, facts, network)


def _diagnostics_safe(items: list[dict]):
    violations = diagnostic_violations(items)
    if violations:
        raise ReconcileError("; ".join(violations))


def _by_name(items: list[dict]) -> dict:
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ReconcileError("Incomplete ARM resource inventory")
    result = {}
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name or name.casefold() in result:
            raise ReconcileError("Incomplete or ambiguous ARM resource inventory")
        result[name.casefold()] = item
    return result


def inspect_resources(client: AzRestClient, context: Context, manifest: dict,
                      client_dir: Path, *, account: str) -> dict:
    """Check occupancy of every generated named resource, not only DELETE targets.

    Child policy/tool/link ownership derives from an explicitly tagged parent.
    Untagged service tags require an existing owned product/API as an anchor.
    Subscription ownership derives from the exact owned product scope.
    """
    cid = manifest["client"]
    desired = desired_state(client_dir, context.profile)
    apim = client.request("GET", f"{client.base}?api-version={API_VERSION}")
    target = GatewayTarget(subscription=context.subscription, tenant=context.tenant,
                           resource_group=context.resource_group, apim_name=context.apim, account=account)
    if not isinstance(apim, dict) or str(apim.get("id", "")).casefold() != target.resource_id.casefold():
        raise ReconcileError("APIM response does not identify the requested gateway")
    state = {"gateway": apim, "operatorAccount": account.casefold()}
    diagnostics = []
    if context.profile != "rest-consumption":
        diagnostics = client.paged(f"{client.base}/diagnostics?api-version={API_VERSION}")
        state["diagnostics"] = diagnostics
    observation = GatewayObservation.issue(str(client_dir.parent.parent.resolve()), target, apim, diagnostics)
    workflow = evaluate_workflow(
        manifest, requires_mcp=context.profile != "rest-consumption", gateway_mode="existing",
        selected_profile=context.profile, observation=observation)
    if workflow.status != "ready-for-preview":
        raise ReconcileError("; ".join(workflow.blockers) or workflow.next_action)
    # Ephemeral IDs/timestamps must not enter the deterministic plan fingerprint.
    # Each preview and final inventory read independently acquires fresh facts.

    apis = _by_name(client.list_apis())
    owned = set()
    api_tags = {}
    api_diagnostics = {}
    tools = {}
    selected_tools = set().union(*desired.native_tools.values()) if desired.native_tools else set()
    for key, api in apis.items():
        name = api["name"]
        tags = client.list_api_tags(name)
        api_tags[key] = sorted(tags)
        if name.startswith(cid + "-") and cid in tags:
            owned.add(key)
        if (api.get("properties") or {}).get("type", "").lower() == "mcp":
            records = client.list_tool_records(name)
            current = {record["name"] for record in records}
            current.update((record.get("properties") or {}).get("displayName", "") for record in records)
            tools[key] = sorted(records, key=lambda record: record["name"])
            collision = selected_tools.intersection(current)
            if collision and key not in owned:
                raise ReconcileError("Native MCP tool name collision on another/unowned API; "
                                     "prepare an isolated client variant before import")
        if key in {n.casefold() for n in desired.apis} and context.profile != "rest-consumption":
            api_diagnostics[key] = client.paged(
                f"{client.base}/apis/{client.segment(name)}/diagnostics?api-version={API_VERSION}")
            _diagnostics_safe(api_diagnostics[key])
    for name in desired.apis:
        if name.casefold() in apis and name.casefold() not in owned:
            raise ReconcileError(f"API occupancy blocked: {name} lacks prefix AND client tag")

    products = _by_name(client.paged(f"{client.base}/products?api-version={API_VERSION}"))
    product_name = f"{cid}-product"
    product = products.get(product_name.casefold())
    product_tags = []
    if product:
        product_tags = client.paged(
            f"{client.base}/products/{product_name}/tags?api-version={API_VERSION}")
        if cid not in {t["name"] for t in product_tags}:
            raise ReconcileError("Product occupancy blocked: existing product has no client tag")
    tags = _by_name(client.paged(f"{client.base}/tags?api-version={API_VERSION}"))
    tag_names = {cid, *(f"{cid}-{api['backend']['mode']}" for api in manifest["apis"])}
    for name in tag_names:
        item = tags.get(name.casefold())
        if item and (not (product or owned) or (item.get("properties") or {}).get("displayName") != name):
            raise ReconcileError(f"Tag occupancy blocked: {name}; no trustworthy owned parent anchor")
    subscriptions = _by_name(client.paged(f"{client.base}/subscriptions?api-version={API_VERSION}"))
    subscription = subscriptions.get(f"{cid}-pilot".casefold())
    if subscription:
        scope = (subscription.get("properties") or {}).get("scope", "")
        if not product or scope.casefold() != f"{client.base}/products/{product_name}".casefold():
            raise ReconcileError("Pilot subscription occupancy blocked: not scoped to owned product")
    refs = secret_refs(manifest)
    named = {}
    if refs:
        named = _by_name(client.paged(f"{client.base}/namedValues?api-version={API_VERSION}"))
        for ref in refs:
            if not ref.startswith(cid + "-"):
                raise ReconcileError("Selective secretRef names must start with <client>- to prevent shared named-value adoption")
            item = named.get(ref.casefold())
            if item and cid not in (item.get("properties") or {}).get("tags", []):
                raise ReconcileError("Named value occupancy blocked: missing explicit client tag")
    state.update(apis=apis, apiTags=api_tags, apiDiagnostics=api_diagnostics, tools=tools, products=products,
                 productTags=product_tags, tags=tags, subscriptions=subscriptions, namedValues=named)
    return state


def secret_refs(manifest: dict) -> list[str]:
    refs = set()
    for api in manifest["apis"]:
        backend = api.get("backend")
        if not isinstance(backend, dict):
            raise ReconcileError("Each manifest API requires a backend object")
        auth = backend.get("outboundAuth") or {}
        if not isinstance(auth, dict):
            raise ReconcileError("outboundAuth must be an object")
        ref = auth.get("secretRef")
        if ref is not None:
            if not isinstance(ref, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,127}", ref):
                raise ReconcileError("secretRef must be a Key Vault secret name, not a path or value")
            refs.add(ref)
    return sorted(refs)


def check_secrets(context: Context, manifest: dict, gateway: dict, run) -> dict:
    refs = secret_refs(manifest)
    if not refs:
        return {}
    if not context.key_vault:
        raise ReconcileError("Selected manifest contains secretRefs: supply --key-vault-name for an existing vault")
    vault = json.loads(run(["az", "keyvault", "show", "--subscription", context.subscription,
                           "--name", context.key_vault, "--output", "json"], capture=True))
    properties = vault.get("properties") or {}
    if not str(vault.get("id", "")).casefold().startswith(
            f"/subscriptions/{context.subscription}/".casefold()):
        raise ReconcileError("Existing Key Vault is outside the approved subscription")
    if str(properties.get("tenantId", "")).casefold() != context.tenant.casefold():
        raise ReconcileError("Existing Key Vault tenant differs from approved tenant")
    principal = (gateway.get("identity") or {}).get("principalId")
    if not principal:
        raise ReconcileError("APIM system identity principalId is missing")
    metadata = json.loads(run([
        "az", "keyvault", "secret", "list", "--subscription", context.subscription,
        "--vault-name", context.key_vault, "--output", "json",
    ], capture=True))
    by_name = {entry["id"].rstrip("/").rsplit("/", 1)[-1]: entry for entry in metadata}
    for ref in refs:
        attributes = (by_name.get(ref) or {}).get("attributes") or {}
        if attributes.get("enabled") is not True:
            raise ReconcileError(f"Secret metadata missing/disabled for {ref}; create/enable it outside this tool")
        for field, expired in (("expires", True), ("notBefore", False)):
            value = attributes.get(field)
            if value:
                instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if (instant <= datetime.now(timezone.utc)) == expired:
                    raise ReconcileError(f"Secret {ref} is expired or not yet valid")
    permission = False
    roles = []
    if properties.get("enableRbacAuthorization"):
        roles = json.loads(run([
            "az", "role", "assignment", "list", "--subscription", context.subscription,
            "--assignee-object-id", principal, "--scope", vault["id"],
            "--include-inherited", "--fill-principal-name", "false",
            "--fill-role-definition-name", "false", "--output", "json",
        ], capture=True))
        # Conservative direct, unconditional built-in grants only. Do not guess
        # custom roles, group membership, PIM activation or conditional grants.
        allowed = {"4633458b-17de-408a-b874-0445c86b69e6", "00482a5a-887f-4fb3-b363-3b7fe8e74483"}
        permission = any(
            role.get("principalId", "").casefold() == principal.casefold() and
            role.get("roleDefinitionId", "").rsplit("/", 1)[-1].lower() in allowed and
            not role.get("condition") and
            (vault["id"].casefold() == role.get("scope", "").casefold() or
             vault["id"].casefold().startswith(role.get("scope", "").casefold().rstrip("/") + "/"))
            for role in roles if role.get("scope"))
    else:
        permission = any(
            entry.get("objectId", "").casefold() == principal.casefold() and
            {"get", "list"} <= {p.lower() for p in entry.get("permissions", {}).get("secrets", [])}
            for entry in properties.get("accessPolicies", []))
    if not permission:
        raise ReconcileError("Cannot prove APIM Key Vault get/list permission. Grant an unconditional "
                             "direct Key Vault Secrets User role (RBAC) or get/list access policy "
                             "through your approved process; custom/conditional grants need manual review.")
    if properties.get("networkAcls", {}).get("defaultAction") == "Deny" and (
            properties.get("networkAcls", {}).get("bypass") != "AzureServices"):
        raise ReconcileError("Cannot prove APIM access through Key Vault network restrictions")
    return {"vault": vault, "metadata": metadata, "roles": roles}


def input_fingerprint(root: Path, client_dir: Path, manifest: dict) -> str:
    paths = {client_dir / "mcp-manifest.yaml"}
    directories = ([client_dir / "generated"] if __package__ else
                   [root / "modules", root / "tools", client_dir / "generated"])
    for directory in directories:
        safe_path(root, directory)
        paths.update(p for p in directory.rglob("*") if p.is_file() and
                     "__pycache__" not in p.parts and "tests" not in p.parts)
    paths.update(root / "apis" / slug(api["name"]) / "openapi.yaml" for api in manifest["apis"])
    digest = hashlib.sha256()
    if __package__:
        from mcp_openapi_creator_kit.assets import verify_assets
        digest.update(json.dumps(verify_assets(), sort_keys=True).encode())
    for path in sorted(paths):
        safe_path(root, path)
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def plan_token(context: Context, inputs: str, state: dict, deletes: list, changes: list) -> str:
    return hashlib.sha256(json.dumps(
        [asdict(context), inputs, state, deletes, changes],
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def summarize_what_if(result: dict, allowed_ids: set[str], required_ids: set[str]) -> list[dict]:
    if not isinstance(result, dict) or result.get("status") != "Succeeded" or result.get("error"):
        raise ReconcileError("ARM what-if failed or was incomplete; no changes were applied")
    changes = result.get("changes")
    if not isinstance(changes, list):
        raise ReconcileError("ARM what-if omitted changes")
    allowed = {value.casefold() for value in allowed_ids}
    seen = set()
    summary = []
    for change in changes:
        if not isinstance(change, dict):
            raise ReconcileError("Malformed ARM what-if change entry")
        rid = change.get("resourceId", "")
        if not isinstance(rid, str):
            raise ReconcileError("Malformed ARM what-if resource identifier")
        kind = change.get("changeType")
        if kind == "Ignore" and rid.casefold() not in allowed:
            continue
        if rid.casefold() not in allowed or kind not in {"Create", "Modify", "NoChange", "Deploy"}:
            raise ReconcileError("ARM what-if contains unsupported, unresolved, destructive or out-of-scope changes")
        if change.get("error") or change.get("unsupportedReason"):
            raise ReconcileError("ARM what-if reports unsupported resource evaluation")
        seen.add(rid.casefold())
        # Never print before/after, deltas, parameter values, or error payloads.
        parts = rid.split("/providers/", 1)[-1].split("/")
        resource_type = "/".join([parts[0], *parts[1::2]])
        summary.append({"resourceId": rid, "resourceType": resource_type, "changeType": kind})
    if not {value.casefold() for value in required_ids} <= seen:
        raise ReconcileError("ARM what-if did not expand all selected resources; cannot safely review this deployment")
    return sorted(summary, key=lambda item: item["resourceId"].casefold())


def template_inventory(client: AzRestClient, manifest: dict, profile: str,
                       client_dir: Path) -> list[tuple[str, set[str], set[str]]]:
    """Exact allowed child IDs from the same naming rules as the kit modules.

    Requiring expansion means provider short-circuits cannot be silently accepted.
    """
    cid = manifest["client"]
    base = client.base
    group = base.split("/providers/", 1)[0]
    ids = set()
    deployments = set()
    mode = manifest.get("mcpExposure", {}).get("mode", "perApi")
    def module(name):
        deployments.add(f"{group}/providers/Microsoft.Resources/deployments/{name}")

    def api(name, tags, native_tools=None):
        rid = f"{base}/apis/{name}"
        ids.add(rid)
        ids.update(f"{rid}/tags/{tag}" for tag in tags)
        ids.add(f"{base}/products/{cid}-product/apis/{name}")
        if native_tools is None:
            ids.add(f"{rid}/policies/policy")
        else:
            ids.update(f"{rid}/tools/{tool}" for tool in native_tools)

    tags = {cid, *(f"{cid}-{item['backend']['mode']}" for item in manifest["apis"])}
    ids.update(f"{base}/tags/{tag}" for tag in tags)
    for item in manifest["apis"]:
        name = f"{cid}-{item['name']}"
        api_tags = [cid, f"{cid}-{item['backend']['mode']}"]
        api(name, api_tags)
        module(f"api-{name}")
        if profile == "native-mcp" and mode != "facade":
            api(name + "-mcp", api_tags, item["mcpTools"])
    if mode != "perApi":
        name = f"{cid}-{manifest.get('mcpExposure', {}).get('facadeName', 'agent')}"
        api(name, [cid])
        module(f"facade-{cid}")
        if profile == "native-mcp":
            api(name + "-mcp", [cid], [tool for item in manifest["apis"] for tool in item["mcpTools"]])
    ids.update({f"{base}/products/{cid}-product",
                f"{base}/products/{cid}-product/policies/policy",
                f"{base}/products/{cid}-product/tags/{cid}",
                f"{base}/subscriptions/{cid}-pilot"})
    module(f"product-{cid}")
    refs = secret_refs(manifest)
    if refs:
        ids.update(f"{base}/namedValues/{ref}" for ref in refs)
        module(f"namedvalues-{cid}")
    result = [("client.bicep", ids | deployments, ids.copy())]
    if profile == "policy-mcp-consumption":
        servers = json.loads((client_dir / "generated" / "policy-mcp" / "servers.json").read_text(encoding="utf-8"))["servers"]
        ids = set()
        deployments = set()
        for server in servers:
            api(server["resourceName"], [cid])
            module("policy-mcp-" + server["resourceName"])
        result.append(("policy-mcp/client.bicep", ids | deployments, ids.copy()))
    return result


def inspect_deployments(context: Context, client: AzRestClient, manifest: dict,
                        client_dir: Path, state: dict, run) -> dict:
    """Deployment-history records also must not overwrite an unrelated name."""
    cid = manifest["client"]
    names = {f"client-{cid}"}
    if context.profile == "policy-mcp-consumption":
        names.add(f"policy-mcp-client-{cid}")
    for _, allowed, _ in template_inventory(client, manifest, context.profile, client_dir):
        names.update(rid.rsplit("/", 1)[-1] for rid in allowed
                     if "/providers/Microsoft.Resources/deployments/" in rid)
    records = json.loads(run([
        "az", "deployment", "group", "list", "--subscription", context.subscription,
        "--resource-group", context.resource_group, "--query", "[].{name:name}",
        "--output", "json",
    ], capture=True))
    occupied = _by_name(records)
    anchor = any(name.startswith(cid + "-") and cid in state["apiTags"].get(name, [])
                 for name in state["apis"]) or bool(state["productTags"])
    checked = {}
    for name in sorted(names):
        if name.casefold() not in occupied:
            continue
        details = json.loads(run([
            "az", "deployment", "group", "show", "--subscription", context.subscription,
            "--resource-group", context.resource_group, "--name", name,
            "--query", "{apim:properties.parameters.apimName.value,client:properties.parameters.clientId.value,clientTag:properties.parameters.clientTag.value}",
            "--output", "json",
        ], capture=True))
        if (not anchor or str(details.get("apim", "")).casefold() != context.apim.casefold()
                or details.get("client") not in {None, cid}
                or details.get("clientTag") not in {None, cid}):
            raise ReconcileError(f"Deployment-history occupancy blocked: {name}; "
                                 "requires matching APIM parameters and an explicitly owned client resource")
        checked[name] = details
    return checked
