"""Read-only, narrowly scoped evidence for retrying an interrupted mock import.

A deployment name, its parameters, or a local receipt is not ownership. Require
a trusted compiled structure (with bounded contract-data corrections only)
and successful ARM *creation* of every surviving tag. Never extend this
exception to APIs, products or DELETEs.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import unquote, urlsplit

from mcp_openapi_creator_kit.deployment_names import deployment_name

if __package__:
    from .lifecycle import API_VERSION, ReconcileError
    from .deployment_recovery_payloads import PayloadMismatch, payload_value, templates_match
else:
    from lifecycle import API_VERSION, ReconcileError
    from deployment_recovery_payloads import PayloadMismatch, payload_value, templates_match


DEPLOYMENT_VERSION = "2022-09-01"


def _fail(reason):
    raise ReconcileError(
        f"Detached-tag recovery refused: {reason}. No ownership was adopted and no changes "
        "were applied. Keep the same client data and have the operator investigate the "
        "failed ARM deployment privately; do not delete/adopt tags or edit local receipts. "
        "Retry recovery only when complete matching live evidence is available.")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EvidenceReader:
    """GET-only ARM reader with exact collection scope, including every page."""

    def __init__(self, context, run):
        self.context = context
        self.run = run
        self.group = f"/subscriptions/{context.subscription}/resourceGroups/{context.resource_group}"
        self.base = self.group + "/providers/Microsoft.Resources/deployments"

    def get(self, uri, scope):
        parsed = urlsplit(uri)
        if ((parsed.scheme or parsed.netloc) and
                (parsed.scheme != "https" or parsed.netloc.casefold() != "management.azure.com")):
            _fail("evidence pagination escaped the ARM endpoint")
        if (parsed.path.casefold() != scope.casefold() or parsed.fragment or
                unquote(parsed.path) != parsed.path):
            _fail("evidence request escaped its exact approved collection")
        result = json.loads(self.run([
            "az", "rest", "--method", "GET", "--subscription", self.context.subscription,
            "--uri", uri, "--output", "json",
        ], capture=True))
        if not isinstance(result, dict) or result.get("error"):
            _fail("missing or malformed ARM evidence")
        return result

    def detail(self, rid):
        return self.get(f"{rid}?api-version={DEPLOYMENT_VERSION}", rid)

    def paged(self, scope, version=DEPLOYMENT_VERSION):
        uri = f"{scope}?api-version={version}"
        seen, records, total = set(), [], None
        while uri:
            if not isinstance(uri, str) or uri in seen:
                _fail("invalid or cyclic evidence pagination")
            seen.add(uri)
            page = self.get(uri, scope)
            if not isinstance(page.get("value"), list) or not all(isinstance(x, dict) for x in page["value"]):
                _fail("incomplete evidence list")
            count = page.get("count")
            if count is not None:
                if type(count) is not int or count < 0 or (total is not None and count != total):
                    _fail("inconsistent evidence list count")
                total = count
            records.extend(page["value"])
            uri = page.get("nextLink")
            if uri is not None and (not isinstance(uri, str) or not uri):
                _fail("malformed evidence nextLink")
        if total is not None and total != len(records):
            _fail("truncated evidence list")
        return records


def _parameters(template, supplied):
    definitions = template.get("parameters", {})
    if not isinstance(definitions, dict) or not isinstance(supplied, dict) or set(supplied) - set(definitions):
        _fail("unexpected deployment parameters")
    result = {}
    for name, definition in definitions.items():
        entry = supplied.get(name)
        if entry is not None:
            if not isinstance(entry, dict) or set(entry) != {"value"}:
                _fail("unresolved or secret deployment parameters")
            result[name] = entry["value"]
        elif "defaultValue" in definition:
            result[name] = definition["defaultValue"]
        else:
            _fail("missing deployment parameter")
    return result


def prove_detached_tags(context, client, manifest, client_dir, state, run):
    if run is None:
        _fail("no live evidence reader")
    cid = manifest["client"]
    if any(api.get("backend") != {"mode": "mock"} for api in manifest["apis"]) or context.key_vault:
        _fail("only credential-free mock imports are supported")
    reader = EvidenceReader(context, run)
    expected_tags = {cid, cid + "-mock"}
    for collection in ("apis", "products", "subscriptions", "tags"):
        records = reader.paged(f"{client.base}/{collection}", API_VERSION)
        by_name = {}
        for record in records:
            name = record.get("name")
            if not isinstance(name, str) or not name or name.casefold() in by_name:
                _fail("ambiguous service inventory")
            by_name[name.casefold()] = record
        if by_name != state[collection]:
            _fail("service inventory changed or was incomplete during proof collection")
    # No mixed partial client resource graph: normal prefix+tag checks still
    # apply to all APIs/products, even when this recovery flag is supplied.
    for collection in ("apis", "products", "subscriptions"):
        for name in state[collection]:
            if name == cid or name.startswith(cid + "-"):
                _fail("a client API/product/subscription remains (mixed partial state)")
    if any(expected_tags.intersection(tags) for tags in state["apiTags"].values()):
        _fail("a foreign API uses a recovery tag")
    product_scope = f"{client.base}/products/{cid}-product".casefold()
    for subscription in state["subscriptions"].values():
        if str((subscription.get("properties") or {}).get("scope", "")).casefold() == product_scope:
            _fail("a foreign subscription refers to the client product")
    extras = {}
    for collection in ("namedValues", "backends"):
        entries = reader.paged(f"{client.base}/{collection}", API_VERSION)
        extras[collection] = entries
        for entry in entries:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                _fail("incomplete external-resource inventory")
            if name.casefold().startswith(cid + "-") or cid in (entry.get("properties") or {}).get("tags", []):
                _fail("client external resources remain")
    surviving = {}
    for name, tag in state["tags"].items():
        if name == cid or name.startswith(cid + "-"):
            if (name not in expected_tags or tag.get("name") != name or
                    tag.get("id", "").casefold() != f"{client.base}/tags/{name}".casefold() or
                    (tag.get("properties") or {}) != {"displayName": name}):
                _fail("unexpected, lookalike or malformed client tag")
            surviving[name] = tag
    if not surviving:
        _fail("no detached tags remain")
    associations = reader.paged(f"{client.base}/tagResources", API_VERSION)
    for entry in associations:
        tag = entry.get("tag")
        if not isinstance(tag, dict) or not isinstance(tag.get("id"), str):
            _fail("incomplete tag association graph")
        tag_id = tag["id"]
        if tag_id.casefold().startswith(client.base.casefold() + "/"):
            tag_id = tag_id[len(client.base):]
        match = re.fullmatch(r"/tags/([A-Za-z0-9_.;-]+)", tag_id)
        if not match or match[1].casefold() not in state["tags"]:
            _fail("tag association outside the service tag inventory")
        if match[1].casefold() in expected_tags:
            _fail("a recovery tag is shared or still attached")
        kinds = [kind for kind in ("api", "operation", "product") if entry.get(kind) is not None]
        if len(kinds) != 1 or not isinstance(entry[kinds[0]], dict):
            _fail("unknown tag association state")
        parent = entry[kinds[0]].get("id")
        if not isinstance(parent, str):
            _fail("missing tag association target")
        if parent.casefold().startswith(client.base.casefold() + "/"):
            parent = parent[len(client.base):]
        parts = parent.split("/")
        kind = kinds[0]
        if kind == "product":
            valid = len(parts) == 3 and parts[1] == "products" and parts[2].casefold() in state["products"]
        else:
            valid = (len(parts) == (5 if kind == "operation" else 3) and parts[1] == "apis" and
                     parts[2].casefold() in state["apis"] and
                     (kind != "operation" or (parts[3] == "operations" and bool(parts[4]))))
        if not valid:
            _fail("unknown or foreign tag association target")

    if __package__:
        from .deployment import template_inventory
    else:
        from deployment import template_inventory
    _, allowed, _ = template_inventory(client, manifest, context.profile, client_dir)[0]
    root_name = deployment_name("client-" + cid)
    root_id = reader.base + "/" + root_name
    expected_histories = {rid.casefold() for rid in allowed if rid.startswith(reader.base + "/")} | {root_id.casefold()}
    # The entire live collection is read, not a caller-supplied history receipt.
    records = reader.paged(reader.base)
    occupied = {}
    for record in records:
        rid, name = record.get("id"), record.get("name")
        if not isinstance(name, str) or not isinstance(rid, str) or rid.casefold() != (reader.base + "/" + name).casefold():
            _fail("malformed deployment inventory")
        if rid.casefold() in occupied:
            _fail("duplicate deployment inventory")
        occupied[rid.casefold()] = record
    if root_id.casefold() not in occupied:
        _fail("the exact failed client deployment is absent from live ARM history")
    compiled = json.loads(run([
        "az", "bicep", "build", "--file", str(client_dir / "generated" / "client.bicep"), "--stdout",
    ], capture=True))
    if not isinstance(compiled, dict) or not isinstance(compiled.get("resources"), list):
        _fail("trusted Bicep compilation is unavailable or unsupported")
    expected_parameters = {"apimName": context.apim, "keyVaultName": "",
                           "enableNativeMcp": context.profile == "native-mcp"}
    expected_parameters = _parameters(compiled, {k: {"value": v} for k, v in expected_parameters.items()})
    checked, created, visited = {}, set(), set()

    def inspect(rid, template, parameters, *, root=False):
        key = rid.casefold()
        if key not in expected_histories or key not in occupied or key in visited:
            _fail("unlinked, duplicate or out-of-scope nested deployment")
        visited.add(key)
        detail = reader.detail(rid)
        props = detail.get("properties") or {}
        if (str(detail.get("id", "")).casefold() != key or
                props.get("provisioningState") not in ({"Failed"} if root else {"Failed", "Succeeded", "Canceled"}) or
                props.get("mode") != "Incremental" or not props.get("correlationId") or not props.get("timestamp")):
            _fail("deployment identity/state is incomplete, active or not a failed import")
        if _digest(_parameters(template, props.get("parameters"))) != _digest(parameters):
            _fail("deployment inputs do not match this exact client and target")
        exported = json.loads(run([
            "az", "deployment", "group", "export", "--subscription", context.subscription,
            "--resource-group", context.resource_group, "--name", rid.rsplit("/", 1)[-1],
            "--output", "json",
        ], capture=True))
        try:
            templates_match(template, exported, manifest, root=root)
        except PayloadMismatch as error:
            _fail(f"historical compiled template differs outside supported contract-data corrections ({error}); "
                  "resource/module names, scope, authentication, backends and module templates must remain identical")
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            _fail("historical compiled template differs outside supported contract-data corrections; "
                  "resource/module names, scope, authentication, backends and module templates must remain identical")
        operations = reader.paged(rid + "/operations")
        seen_operations = set()
        children = {}
        for resource in exported["resources"]:
            if resource.get("type", "").casefold() == "microsoft.resources/deployments":
                name = resource.get("name")
                if not isinstance(name, str) or "[" in name:
                    _fail("nested deployment name is not a trusted literal")
                children[(reader.base + "/" + name).casefold()] = resource
        for operation in operations:
            opid = operation.get("operationId")
            data = operation.get("properties") or {}
            if not isinstance(opid, str) or not opid or opid in seen_operations:
                _fail("incomplete or duplicate deployment operation")
            seen_operations.add(opid)
            if data.get("provisioningState") not in {"Succeeded", "Failed", "Canceled"}:
                _fail("an operation has an active or unknown outcome")
            target = data.get("targetResource")
            if target is None and data.get("provisioningOperation") == "EvaluateDeploymentOutput":
                continue
            target_id = (target or {}).get("id", "")
            if not isinstance(target_id, str) or target_id.casefold() not in {x.casefold() for x in allowed}:
                _fail("an operation targets an unexpected resource")
            if data.get("provisioningOperation") != "Create":
                _fail("an operation is not a reviewed resource creation")
            target_key = target_id.casefold()
            if target_key in children:
                child = children[target_key]["properties"]
                # Resolve only the tiny parameter language emitted by trusted kit
                # modules; never evaluate arbitrary ARM expressions from history.
                supplied = {}
                for name, entry in child.get("parameters", {}).items():
                    value = entry.get("value")
                    if name in {"specValue", "policyXml"}:
                        try:
                            value, _ = payload_value(exported, value)
                        except PayloadMismatch:
                            _fail("unsupported historical payload argument")
                    if isinstance(value, str) and value.startswith("["):
                        match = re.fullmatch(r"\[parameters\('([^']+)'\)\]", value)
                        boolean = re.fullmatch(r"\[and\(parameters\('([^']+)'\), (true|false)(?:\(\))?\)\]", value)
                        if match and match[1] in parameters:
                            value = parameters[match[1]]
                        elif boolean and type(parameters.get(boolean[1])) is bool:
                            value = parameters[boolean[1]] and boolean[2] == "true"
                        else:
                            _fail("unsupported nested parameter expression")
                    supplied[name] = {"value": value}
                nested = child.get("template")
                if not isinstance(nested, dict):
                    _fail("nested template is not immutable and inline")
                inspect(target_id, nested, _parameters(nested, supplied))
            elif target_key.startswith((client.base + "/tags/").casefold()):
                name = target_id.rsplit("/", 1)[-1]
                if name in surviving:
                    if (data.get("provisioningState") != "Succeeded" or
                            data.get("statusCode") not in ("Created", "201", 201) or
                            name in created):
                        _fail("tag creation is unproven (a PUT/OK or deployment name is not creation evidence)")
                    created.add(name)
            elif data.get("provisioningState") == "Succeeded":
                _fail("non-tag resources succeeded; this is not a tag-only interrupted import")
        # Bind immutable inputs plus terminal operations, not just history names.
        checked[rid.rsplit("/", 1)[-1]] = {
            "deployment": _digest(detail), "template": _digest(exported),
            "operations": _digest(sorted(operations, key=lambda x: x["operationId"])),
        }

    inspect(root_id, compiled, expected_parameters, root=True)
    if set(surviving) != created:
        _fail("not every detached tag has successful 201 creation evidence")
    if (set(occupied) & expected_histories) != visited:
        _fail("occupied nested deployment histories are not linked by the failed import")
    return {"tags": sorted(surviving), "deployments": checked,
            "associations": _digest(associations), "externalInventory": _digest(extras)}
