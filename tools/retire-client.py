#!/usr/bin/env python3
"""Preview/review/retire one mock client on an explicitly selected existing APIM."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import yaml

if __package__:
    from .deployment import Context, client_path, confirm_context, input_fingerprint, plan_token, slug
    from .lifecycle import API_VERSION, MCP_API_VERSION, AzRestClient, ReconcileError
    from .local_python import local_python
else:
    from deployment import Context, client_path, confirm_context, input_fingerprint, plan_token, slug
    from lifecycle import API_VERSION, MCP_API_VERSION, AzRestClient, ReconcileError
    from local_python import local_python

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_READ_WORKERS = 4
CLI_TIMEOUT_SECONDS = 60
CLEANUP_TIMEOUT_SECONDS = 10
SNAPSHOT_TIMEOUT_SECONDS = 300
DELETE_WAIT_SECONDS = 120
MAX_COLLECTION_PAGES = 100
_PROGRESS_LOCK = threading.Lock()
AUDIT_NOTICE = (
    "All resource-group deployment-history records remain as audit records; "
    "they are not inventoried or deleted. APIM, resource group, service diagnostics, "
    "identities, RBAC and local customer data are preserved."
)


def progress(stage: str, resource_id: str = ""):
    with _PROGRESS_LOCK:
        print(f"[retire-client] {stage}" + (f" {resource_id}" if resource_id else ""),
              file=sys.stderr, flush=True)


@dataclass
class ReadBudget:
    deadline: float
    cancelled: threading.Event

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise ReconcileError("Retirement read cancelled or its deadline exceeded; no further operations. Preview again.")
        return remaining


_READ_BUDGET: ContextVar[ReadBudget | None] = ContextVar("retirement_read_budget", default=None)


def windows_creation_time(process):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(
            wintypes.HANDLE(int(process._handle)), ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel_time), ctypes.byref(user_time)):
        raise ReconcileError("Cannot establish the launched process identity for Windows cleanup")
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


_WINDOWS_STOP_TREE = r"""
& {
    param([uint32]$RootPid, [long]$ExpectedStart)
    $ErrorActionPreference = 'Stop'
    if ($RootPid -eq 0 -or $RootPid -eq $PID) { throw 'Invalid cleanup root' }
    function Open-CapturedProcess([uint32]$TargetPid) {
        try { $item = [System.Diagnostics.Process]::GetProcessById($TargetPid) }
        catch [System.ArgumentException] { return $null }
        try {
            $null = $item.Handle
            return [pscustomobject]@{
                Id = $TargetPid
                Started = $item.StartTime.ToUniversalTime().ToFileTimeUtc()
                Process = $item
                Depth = 0
            }
        } catch {
            $gone = $item.HasExited
            $item.Dispose()
            if ($gone) { return $null }
            throw
        }
    }
    $owned = [System.Collections.Generic.Dictionary[uint32,object]]::new()
    try {
        $root = Open-CapturedProcess $RootPid
        if ($null -ne $root -and $root.Started -ne $ExpectedStart) {
            $root.Process.Dispose()
            throw 'Cleanup root identity changed'
        }
        if ($null -eq $root) {
            $root = [pscustomobject]@{ Id=$RootPid; Started=$ExpectedStart; Process=$null; Depth=0 }
        }
        $owned.Add($RootPid, $root)
        for ($round = 0; $round -lt 4; $round++) {
            $rows = @(Get-CimInstance -ClassName Win32_Process -Property ProcessId,ParentProcessId,CreationDate)
            $added = 0
            do {
                $changed = $false
                foreach ($row in $rows) {
                    $id = [uint32]$row.ProcessId
                    $parentId = [uint32]$row.ParentProcessId
                    if ($owned.ContainsKey($id) -or -not $owned.ContainsKey($parentId)) { continue }
                    if ($null -eq $row.CreationDate) { throw 'Missing descendant creation time' }
                    $cimStart = $row.CreationDate.ToUniversalTime().ToFileTimeUtc()
                    $parent = $owned[$parentId]
                    if ($cimStart + 9 -lt $parent.Started) { continue }
                    $child = Open-CapturedProcess $id
                    if ($null -eq $child) { throw 'Descendant disappeared before identity capture' }
                    if ([Math]::Abs($child.Started - $cimStart) -gt 9 -or $child.Started -lt $parent.Started) {
                        $child.Process.Dispose()
                        throw 'Descendant identity changed'
                    }
                    $child.Depth = $parent.Depth + 1
                    $owned.Add($id, $child)
                    $added++
                    $changed = $true
                }
            } while ($changed)
            foreach ($node in @($owned.Values | Sort-Object Depth,Id)) {
                if ($null -eq $node.Process -or $node.Process.HasExited) { continue }
                if ($node.Process.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $node.Started) {
                    throw 'Captured process identity changed'
                }
                try { Stop-Process -Id $node.Id -Force -ErrorAction Stop }
                catch { if (-not $node.Process.HasExited) { throw } }
                if (-not $node.Process.WaitForExit(1000)) { throw 'Captured process did not stop' }
            }
            if ($round -gt 0 -and $added -eq 0) { return }
        }
        throw 'Descendant cleanup did not converge'
    } finally {
        foreach ($node in $owned.Values) {
            if ($null -ne $node.Process) { $node.Process.Dispose() }
        }
    }
}
"""


def terminate_process_tree(process):
    if os.name == "nt":
        # Popen retains the root handle. The script holds each captured child
        # handle through Stop-Process and rescans, preventing PID reuse/adoption.
        started = windows_creation_time(process)
        result = subprocess.run([
            str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            _WINDOWS_STOP_TREE.rstrip() + f" {int(process.pid)} {started}",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=CLEANUP_TIMEOUT_SECONDS)
        if result.returncode:
            raise ReconcileError("Azure CLI timed out; PID-tree cleanup could not be confirmed. Stop and inspect local processes.")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def subprocess_command(executable: str, arguments: list[str], environment: dict):
    if os.name != "nt" or Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return [executable, *arguments]
    # az.cmd forwards %* inside an IF block. list2cmdline leaves metacharacter
    # arguments unquoted. Expand data once from private environment slots into
    # quoted arguments, with delayed expansion disabled; never embed data in cmd.
    tokens = []
    for index, value in enumerate([executable, *arguments]):
        if any(character in value for character in ('"', "\r", "\n", "\0")):
            raise ReconcileError("Windows batch arguments containing quotes or control characters are unsupported")
        if not value:
            tokens.append('""')
            continue
        key = f"MCP_KIT_BATCH_ARG_{index}"
        # The native Python argv parser consumes backslashes before a closing quote.
        trailing = len(value) - len(value.rstrip("\\"))
        environment[key] = value + "\\" * trailing
        tokens.append(f'"%{key}%"')
    shell = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
    return subprocess.list2cmdline([shell]) + ' /D /V:OFF /S /C "' + " ".join(tokens) + '"'


def arm_error_code(stdout: bytes, stderr: bytes, encoding: str) -> str:
    for content in (stderr, stdout):
        match = re.search(rb"(?:^|\r?\n)ERROR:\s+\(([A-Za-z][A-Za-z0-9_.-]{0,79})\)(?:\s|$)", content)
        if match:
            return match.group(1).decode("ascii")
        try:
            text = content.decode(encoding).strip().removeprefix("ERROR:").strip()
            wrapped = re.fullmatch(
                r"(?:Bad Request|Unauthorized|Forbidden|Not Found|Method Not Allowed|Conflict|Too Many Requests)"
                r"\((\{.*\})\)", text, flags=re.DOTALL)
            if wrapped:
                text = wrapped.group(1)
            error = json.loads(text)
        except (UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(error, dict) and isinstance(error.get("error"), dict):
            code = error["error"].get("code")
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", code):
                return code
    return "unavailable"


def run(args: list[str], capture: bool = False) -> str:
    executable = shutil.which(args[0])
    if executable is None:
        raise ReconcileError("Required Azure CLI executable is unavailable")
    environment = os.environ.copy()
    environment.pop("MCP_RECONCILE_APPLY", None)
    invocation = subprocess_command(executable, args[1:], environment)
    # Windows az.cmd pins its bundled interpreter independently of this kit's
    # UTF-8 mode. Python kit children use UTF-8; Azure CLI uses the OS locale.
    encoding = locale.getencoding() if args[0] == "az" else "utf-8"
    # Decode on this thread: Windows text-mode pipe readers can swallow decode
    # failures in a background thread and return None instead of raising.
    resource_id = urlsplit(args[args.index("--uri") + 1]).path if "--uri" in args else ""
    method = args[args.index("--method") + 1] if "--method" in args else None
    stage = (method if method in {"GET", "DELETE", "HEAD"} else
             "account/context" if args[1:3] == ["account", "show"] else "command")
    budget = _READ_BUDGET.get()
    timeout = min(CLI_TIMEOUT_SECONDS, budget.remaining()) if budget else CLI_TIMEOUT_SECONDS
    progress(f"CLI start {stage}", resource_id)
    process = subprocess.Popen(
        invocation, cwd=REPO_ROOT, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    drained = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        drained = True
    except subprocess.TimeoutExpired:
        progress(f"CLI timeout {stage}", resource_id)
        try:
            terminate_process_tree(process)
            process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
            drained = True
        except (OSError, subprocess.TimeoutExpired, ReconcileError) as error:
            try:
                if os.name != "nt":
                    process.kill()
                process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                raise ReconcileError("Azure CLI timeout: local process cleanup failed; "
                                     "no further operations. Inspect local processes before retrying.") from None
            raise ReconcileError("Azure CLI timeout: process-tree cleanup was not confirmed; "
                                 "no further operations. Inspect local processes before retrying.") from error
        raise ReconcileError(f"Azure CLI timed out after {timeout:g}s; process tree stopped. "
                             "No retry. A timed-out DELETE has an unknown Azure outcome; preview again.") from None
    finally:
        # Closing a pipe while a Windows reader still holds its lock can hang
        # if tree cleanup failed. Those daemon readers are left to process exit.
        if drained and process.stdout:
            process.stdout.close()
        if drained and process.stderr:
            process.stderr.close()
    if process.returncode:
        detail = f"CLI failed {stage}: exit {process.returncode}; ARM code {arm_error_code(stdout, stderr, encoding)}"
        progress(detail, resource_id)
        raise ReconcileError(detail + (f"; resource {resource_id}" if resource_id else "") +
                             "; raw Azure output suppressed. "
                             "No fallback or automatic retry of DELETE. Preview again before resuming.")
    progress(f"CLI complete {stage}", resource_id)
    return stdout.decode(encoding) if capture else ""


def validate_manifest(manifest: dict, client_id: str):
    if not isinstance(manifest, dict) or manifest.get("client") != client_id:
        raise ReconcileError("Manifest client must match the selected folder")
    if not isinstance(manifest.get("apis"), list) or not manifest["apis"]:
        raise ReconcileError("Retirement requires a preserved non-empty mock manifest")
    for api in manifest["apis"]:
        if not isinstance(api, dict):
            raise ReconcileError("Each manifest API must be an object")
        slug(api.get("name"))
        backend = api.get("backend")
        if not isinstance(backend, dict) or backend.get("mode") != "mock":
            raise ReconcileError("Retirement supports mock clients only; external/hosted backends are unsupported")
        if set(backend) != {"mode"}:
            raise ReconcileError("Mock retirement does not support outbound auth, secretRefs or backend configuration")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _metadata(value):
    # ETags/timestamps on parent entities may change because of our own child
    # DELETEs. Compare actual configuration and relationships, not those clocks.
    if isinstance(value, dict):
        return {key: _metadata(item) for key, item in value.items()
                if key.casefold() not in {
                    "etag", "createdat", "updatedat", "lastmodifiedat", "lastupdated",
                    "primarykey", "secondarykey",
                }}
    if isinstance(value, list):
        return [_metadata(item) for item in value]
    return value


@dataclass(frozen=True)
class Deletion:
    resource_id: str
    version: str = API_VERSION
    descendants: tuple[str, ...] = ()

    @property
    def ids(self):
        return (self.resource_id, *self.descendants)


@dataclass
class RetirementPlan:
    state: dict[str, str]
    deletions: list[Deletion]
    owned_anchors: set[str]
    tags: set[str]
    full_inventory: bool = True
    unavailable_by_tier: tuple[str, ...] = ()

    def review(self) -> list[dict]:
        return [{"resourceId": item.resource_id, "apiVersion": item.version,
                 "cascadeResourceIds": list(item.descendants)} for item in self.deletions]


class RetirementClient(AzRestClient):
    def records(self, suffix: str, *, version: str = API_VERSION) -> list[dict]:
        uri = f"{self.base}{suffix}?api-version={version}"
        # Subscription List is metadata-only (never listSecrets). Projection also
        # ensures an unexpected key field cannot enter captured CLI output.
        projection = (
            "{value:value[].{id:id,name:name,properties:{scope:properties.scope,"
            "state:properties.state,ownerId:properties.ownerId,displayName:properties.displayName,"
            "tags:properties.tags,keyVault:properties.keyVault,secret:properties.secret}},nextLink:nextLink,count:count}"
        ) if suffix in {"/subscriptions", "/namedValues"} else None
        values, seen = [], set()
        total = None
        while uri:
            budget = _READ_BUDGET.get()
            if budget:
                budget.remaining()
            self.validate_uri(uri)
            if urlsplit(uri).path.casefold() != (self.base + suffix).casefold():
                raise ReconcileError("Retirement pagination escaped its collection")
            if uri in seen or len(seen) >= MAX_COLLECTION_PAGES:
                raise ReconcileError("ARM pagination cycle or retirement page limit exceeded")
            seen.add(uri)
            if projection:
                response = json.loads(self.runner([
                    "az", "rest", "--method", "GET", "--subscription", self.subscription,
                    "--uri", uri, "--query", projection, "--output", "json",
                ]))
            else:
                response = self.request("GET", uri)
            if not isinstance(response, dict) or not isinstance(response.get("value"), list):
                raise ReconcileError("Incomplete retirement metadata inventory")
            count = response.get("count")
            if count is not None:
                if type(count) is not int or count < 0 or (total is not None and total != count):
                    raise ReconcileError("Malformed or changing retirement collection count")
                total = count
            values.extend(response["value"])
            uri = response.get("nextLink")
            if uri is not None and not isinstance(uri, str):
                raise ReconcileError("Malformed retirement pagination link")
        if total is not None and len(values) != total:
            raise ReconcileError("Incomplete retirement collection: count differs from collected records")
        return values

    def delete_reviewed(self, deletion: Deletion):
        uri = f"{deletion.resource_id}?api-version={deletion.version}"
        suffix = deletion.resource_id.removeprefix(self.base).split("/")
        if len(suffix) == 3 and suffix[1] == "products":
            uri += "&deleteSubscriptions=false"
        elif len(suffix) == 3 and suffix[1] == "apis":
            uri += "&deleteRevisions=false"
        self.validate_uri(uri)
        progress("APPLY DELETE", deletion.resource_id)
        self.runner(["az", "rest", "--method", "DELETE", "--subscription", self.subscription,
                     "--uri", uri, "--headers", "If-Match=*", "--output", "none"])
        # API DELETE can return 202. Wait for absence before deleting a source
        # API, product or tag; never interpret a failed read as absence.
        parent, _, name = deletion.resource_id.rpartition("/")
        token = _READ_BUDGET.set(ReadBudget(time.monotonic() + DELETE_WAIT_SECONDS, threading.Event()))
        try:
            for attempt in range(31):
                progress("WAIT absence", deletion.resource_id)
                records = self.records(parent.removeprefix(self.base), version=deletion.version)
                if name.casefold() not in {str(item.get("name", "")).casefold() for item in records}:
                    return
                if attempt != 30:
                    time.sleep(2)
        finally:
            _READ_BUDGET.reset(token)
        raise ReconcileError("DELETE has not reached observed absence; stop and preview again later")


class SnapshotReads:
    """Four bounded workers; results live in one inspection only, never across DELETEs."""

    def __init__(self, client):
        self.client = client
        self.values = {}
        self.budget = ReadBudget(time.monotonic() + SNAPSHOT_TIMEOUT_SECONDS, threading.Event())

    def load(self, requests, stage):
        pending = iter(sorted(set(requests) - self.values.keys()))
        self.budget.remaining()
        progress("inventory " + stage, self.client.base)

        def read(key):
            token = _READ_BUDGET.set(self.budget)
            try:
                self.budget.remaining()
                suffix, version = key
                return (self.client.records(suffix, version=version) if suffix else
                        self.client.request("GET", f"{self.client.base}?api-version={version}"))
            finally:
                _READ_BUDGET.reset(token)

        with ThreadPoolExecutor(max_workers=MAX_READ_WORKERS) as executor:
            active = {}

            def submit():
                self.budget.remaining()
                key = next(pending, None)
                if key is not None:
                    progress("READ begin", self.client.base + key[0])
                    active[executor.submit(read, key)] = key

            try:
                for _ in range(MAX_READ_WORKERS):
                    submit()
                while active:
                    completed, _ = wait(active, timeout=self.budget.remaining(), return_when=FIRST_COMPLETED)
                    self.budget.remaining()
                    # Validate every finished future before scheduling more work.
                    results = [(future, future.result()) for future in completed]
                    for future, result in results:
                        key = active.pop(future)
                        self.values[key] = result
                        progress("READ complete", self.client.base + key[0])
                    for _ in results:
                        submit()
            except BaseException:
                self.budget.cancelled.set()
                for future in active:
                    future.cancel()
                raise

    def get(self, suffix, version=API_VERSION):
        return self.values[(suffix, version)]


def inspect_retirement(client: RetirementClient, client_id: str, *,
                       account: str, authorized_tags: set[str] | None = None,
                       allow_absent_fast_path: bool = True) -> RetirementPlan:
    """Build an exact graph, including reverse references throughout this APIM.

    authorized_tags is in-memory continuation authority from the initial tagged
    anchor, never a flag, a receipt or customer-provided ownership evidence.
    """
    cid = slug(client_id)
    prefix = cid + "-"
    state = {"operator": _digest(account.casefold())}
    base = client.base
    reads = SnapshotReads(client)
    reads.load([(suffix, API_VERSION) for suffix in (
        "", "/apis", "/products", "/tags", "/subscriptions", "/namedValues", "/backends", "/tagResources",
    )], "service")
    gateway = reads.get("")
    if not isinstance(gateway, dict) or str(gateway.get("id", "")).casefold() != base.casefold():
        raise ReconcileError("Gateway inventory does not identify the explicitly selected APIM")
    state[base] = _digest(_metadata(gateway))
    sku = gateway.get("sku")
    product_groups_supported = not (
        isinstance(sku, dict) and isinstance(sku.get("name"), str)
        and sku["name"].casefold() == "consumption")
    unavailable_by_tier = []

    def records(suffix, *, version=API_VERSION, canonical=None):
        result = {}
        for record in reads.get(suffix, version):
            if not isinstance(record, dict):
                raise ReconcileError("Malformed retirement inventory")
            name = record.get("name")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.;-]+", name)
                    or name.casefold() in result):
                raise ReconcileError("Unsafe or ambiguous retirement inventory name")
            rid = base + suffix + "/" + name
            actual = record.get("id")
            allowed = {rid.casefold()}
            if canonical:
                allowed.add((base + canonical + "/" + name).casefold())
            if not isinstance(actual, str) or actual.casefold() not in allowed:
                raise ReconcileError("Retirement resource ID does not match its inventory scope")
            result[name.casefold()] = record
            state[rid] = _digest(_metadata(record))
        return result

    apis = records("/apis")
    products = records("/products")
    tags = records("/tags")
    subscriptions = records("/subscriptions")
    named_values = records("/namedValues")
    backends = records("/backends")
    for item in [*named_values.values(), *backends.values()]:
        if item["name"].startswith(prefix) or cid in (item.get("properties") or {}).get("tags", []):
            raise ReconcileError("Client named values/backends remain: mock retirement cannot safely clean external resources")
    target_tags = {cid, cid + "-mock"}
    for item in tags.values():
        if item["name"].startswith(prefix) and item["name"] not in target_tags:
            raise ReconcileError("Unsupported client tag (possible previous external deployment); retirement refused")

    # TagResource/ListByService is the complete, paginated reverse relationship
    # inventory for API, operation and product tags. Its names are display names;
    # only its scoped resource IDs establish identity.
    tag_collections = {}
    tagged_operations = set()
    for entry in reads.get("/tagResources"):
        if not isinstance(entry, dict) or not isinstance(entry.get("tag"), dict):
            raise ReconcileError("Malformed tag association inventory")
        kinds = [kind for kind in ("api", "operation", "product") if entry.get(kind) is not None]
        if len(kinds) != 1:
            raise ReconcileError("Ambiguous tag association resource")
        kind = kinds[0]
        target = entry[kind]
        if not isinstance(target, dict):
            raise ReconcileError("Malformed tag association target")

        def relative_id(value):
            if not isinstance(value, str):
                raise ReconcileError("Missing tag association resource ID")
            if value.casefold().startswith(base.casefold() + "/"):
                value = value[len(base):]
            if not re.fullmatch(r"/(?:[A-Za-z0-9_.;-]+/)*[A-Za-z0-9_.;-]+", value):
                raise ReconcileError("Unsafe tag association resource ID")
            return value

        tag_id = relative_id(entry["tag"].get("id"))
        tag_parts = tag_id.split("/")
        if len(tag_parts) != 3 or tag_parts[1] != "tags" or tag_parts[2].casefold() not in tags:
            raise ReconcileError("Tag association does not match the complete service tag inventory")
        tag = tags[tag_parts[2].casefold()]
        if entry["tag"].get("name") != (tag.get("properties") or {}).get("displayName"):
            raise ReconcileError("Inconsistent tag association display name")
        parent = relative_id(target.get("id"))
        parts = parent.split("/")
        if kind == "product":
            valid = len(parts) == 3 and parts[1] == "products" and parts[2].casefold() in products
        else:
            valid = (len(parts) == (5 if kind == "operation" else 3) and parts[1] == "apis"
                     and parts[2].casefold() in apis and (kind != "operation" or parts[3] == "operations"))
        if not valid:
            raise ReconcileError("Tag association parent is absent or outside the selected APIM")
        if kind == "operation":
            tagged_operations.add(base + parent)
        suffix = parent + "/tags"
        collection = tag_collections.setdefault(suffix, {})
        if tag["name"].casefold() in collection:
            raise ReconcileError("Duplicate tag association in paginated inventory")
        collection[tag["name"].casefold()] = tag
        state[base + suffix + "/" + tag["name"]] = _digest(_metadata(entry))

    def tag_records(suffix):
        return tag_collections.get(suffix, {})

    native_names = {api["name"] for api in apis.values()
                    if (api.get("properties") or {}).get("type", "http").lower() == "mcp"}
    reads.load([("/apis/" + name + "/tools", MCP_API_VERSION) for name in native_names], "native references")
    tools = {name: records("/apis/" + name + "/tools", version=MCP_API_VERSION) for name in native_names}
    for entries in tools.values():
        for item in entries.values():
            reference = (item.get("properties") or {}).get("operationId")
            if not isinstance(reference, str) or not re.fullmatch(
                    re.escape(base) + r"/apis/[A-Za-z0-9_.;-]+/operations/[A-Za-z0-9_.;-]+",
                    reference, flags=re.IGNORECASE):
                raise ReconcileError("Native tool source reference is missing or unsupported")
            if reference.casefold().startswith((base + "/apis/" + prefix).casefold()):
                api_name = reference[len(base + "/apis/"):].split("/", 1)[0]
                if api_name.casefold() not in apis:
                    raise ReconcileError("Native tool references an absent client API; retirement cannot prove absence")
    for item in subscriptions.values():
        scope = (item.get("properties") or {}).get("scope")
        if not isinstance(scope, str) or not scope.startswith("/"):
            raise ReconcileError("Subscription scope metadata is incomplete")
        for collection, inventory in (("apis", apis), ("products", products)):
            scope_prefix = base + "/" + collection + "/"
            if scope.casefold().startswith((scope_prefix + prefix).casefold()):
                if scope[len(scope_prefix):].casefold() not in inventory:
                    raise ReconcileError("Subscription references an absent client resource; retirement cannot prove absence")
    candidates = any(
        item["name"].casefold().startswith(prefix) or item["name"].casefold() == cid
        for inventory in (apis, products, tags, subscriptions) for item in inventory.values())
    if allow_absent_fast_path and not candidates:
        # Without either service tag, no tag association can identify this
        # client; the complete association table above independently verifies it.
        # Native references, scoped subscriptions and external leftovers were
        # checked as well. No descendant can survive an absent API/product.
        progress("inventory complete: proven absent", base)
        return RetirementPlan(state, [], set(), set(), full_inventory=False)

    potential_owned = {api["name"] for api in apis.values() if api["name"].startswith(prefix)
                       or cid in tag_records("/apis/" + api["name"] + "/tags")}
    requests = [("/apis/" + api["name"] + "/operations", API_VERSION) for api in apis.values()]
    requests.extend(("/products/" + product["name"] + "/apis", API_VERSION) for product in products.values())
    for name in potential_owned:
        requests.extend(("/apis/" + name + "/" + child, API_VERSION) for child in
                        ("schemas", "policies", "releases", "diagnostics"))
        if name not in native_names:
            requests.append(("/apis/" + name + "/revisions", API_VERSION))
    if cid + "-product" in products:
        children = ("policies", "groups") if product_groups_supported else ("policies",)
        requests.extend(("/products/" + cid + "-product/" + child, API_VERSION) for child in children)
    reads.load(requests, "API and product relationships")
    all_operations = {api["name"]: records("/apis/" + api["name"] + "/operations") for api in apis.values()}
    reads.load([
        ("/apis/" + name + "/operations/" + operation["name"] + "/policies", API_VERSION)
        for name in potential_owned
        for operation in all_operations[name].values()
    ], "operation policies")
    owned, anchors, native = set(), set(), set()
    api_tags, operations = {}, {}
    for api in apis.values():
        name = api["name"]
        rid = base + "/apis/" + name
        api_tags[name] = tag_records("/apis/" + name + "/tags")
        tag_names = {tag["name"] for tag in api_tags[name].values()}
        if name.startswith(prefix) or cid in tag_names:
            if not (name.startswith(prefix) and cid in tag_names):
                raise ReconcileError("API ownership requires BOTH exact client prefix and client tag")
            owned.add(name)
            anchors.add(rid)
            if not tag_names <= target_tags:
                raise ReconcileError("Owned API has foreign tags; shared resources require separate review")
            properties = api.get("properties") or {}
            if (";" in name or properties.get("apiVersionSetId") or properties.get("apiVersion")
                    or str(properties.get("apiRevision", "1")) != "1" or properties.get("isCurrent") is False):
                raise ReconcileError("Versioned or revised APIs are not supported by mock retirement")
            if properties.get("type", "http").lower() not in {"http", "mcp"}:
                raise ReconcileError("Only HTTP mocks and native MCP APIs are supported")
        elif target_tags.intersection(tag_names):
            raise ReconcileError("Client tag is attached to an unowned API")
        is_native = (api.get("properties") or {}).get("type", "http").lower() == "mcp"
        if is_native:
            if name in owned:
                native.add(name)
        # Operation tags can refer to a client tag even when their API does not.
        operations[name] = all_operations[name]
        for operation in operations[name].values():
            suffix = "/apis/" + name + "/operations/" + operation["name"] + "/tags"
            op_tags = tag_records(suffix)
            names = {tag["name"] for tag in op_tags.values()}
            if target_tags.intersection(names) and name not in owned:
                raise ReconcileError("Client tag is attached to an unowned API operation")
            if name in owned:
                if not names <= target_tags:
                    raise ReconcileError("Owned operation has foreign tags")
                records(suffix.removesuffix("/tags") + "/policies")

    if not tagged_operations <= state.keys():
        raise ReconcileError("Tag association operation is absent from the complete operation inventory")
    for api_name, entries in tools.items():
        for item in entries.values():
            reference = (item.get("properties") or {}).get("operationId")
            if not isinstance(reference, str):
                # Cannot prove that an unknown tool kind does not reference us.
                raise ReconcileError("Native tool source reference is missing or unsupported")
            sources = {name for name in owned if reference.casefold().startswith(
                (base + "/apis/" + name + "/operations/").casefold())}
            if sources and api_name not in owned:
                raise ReconcileError("Unowned native MCP tool references a retiring API")
            if api_name in owned and not sources:
                raise ReconcileError("Owned native MCP tool references an unowned or absent API")
            if api_name in owned and not any(
                    reference.casefold() == (base + "/apis/" + name + "/operations/" + op["name"]).casefold()
                    for name in sources for op in operations.get(name, {}).values()):
                raise ReconcileError("Native MCP tool source operation is not present in the reviewed inventory")

    product_name = cid + "-product"
    product_id = base + "/products/" + product_name
    product_present = product_name in products
    product_links = []
    for product in products.values():
        name = product["name"]
        suffix = "/products/" + name
        p_tags = tag_records(suffix + "/tags")
        names = {tag["name"] for tag in p_tags.values()}
        members = records(suffix + "/apis", canonical="/apis")
        member_names = {item["name"] for item in members.values()}
        if name == product_name:
            if cid not in names:
                raise ReconcileError("Product ownership requires BOTH exact client prefix and client tag")
            if names != {cid} or not member_names <= owned:
                raise ReconcileError("Product has foreign tags or unowned API memberships")
            anchors.add(product_id)
            product_links = [base + suffix + "/apis/" + api for api in sorted(member_names)]
            records(suffix + "/policies")
            if product_groups_supported:
                records(suffix + "/groups", canonical="/groups")
            else:
                unavailable_by_tier.append(base + suffix + "/groups")
                progress("UNAVAILABLE-BY-TIER (observed Consumption): product groups", base + suffix + "/groups")
        elif name.startswith(prefix) or target_tags.intersection(names) or owned.intersection(member_names):
            raise ReconcileError("Additional product/client tag membership would affect shared or unowned resources")

    for subscription in subscriptions.values():
        name = subscription["name"]
        scope = (subscription.get("properties") or {}).get("scope")
        if not isinstance(scope, str) or not scope.startswith("/"):
            raise ReconcileError("Subscription scope metadata is incomplete")
        is_product_scope = scope.casefold() == product_id.casefold()
        is_api_scope = any(scope.casefold() == (base + "/apis/" + api).casefold() for api in owned)
        if name == cid + "-pilot":
            if not product_present or not is_product_scope:
                raise ReconcileError("Pilot subscription must have the exact verified owned product scope")
        elif is_product_scope or is_api_scope or name.startswith(prefix):
            raise ReconcileError("Additional subscription references client resources; retirement refused")

    present_tags = set()
    for name in target_tags:
        item = tags.get(name)
        if item:
            if (item.get("properties") or {}).get("displayName") != name:
                raise ReconcileError("Client service tag display name differs from its exact kit name")
            if not anchors and name not in (authorized_tags or set()):
                raise ReconcileError("Detached client tag has no live prefix+tag ownership anchor; "
                                     "separate operator investigation is required, not a force-delete")
            present_tags.add(name)

    for name in sorted(owned):
        suffix = "/apis/" + name
        if name not in native:
            revisions = reads.get(suffix + "/revisions")
            if len(revisions) > 1 or any(
                    str((r.get("properties") or {}).get("apiRevision", "1")) != "1" for r in revisions):
                raise ReconcileError("Multiple API revisions require a separate retirement procedure")
        records(suffix + "/schemas")
        records(suffix + "/policies")
        if records(suffix + "/releases"):
            raise ReconcileError("API release history is outside supported mock retirement")
        if records(suffix + "/diagnostics"):
            raise ReconcileError("API-specific diagnostics are not kit mock resources; retirement refused")

    deletions = []
    removed = set()

    def delete(rid, version=API_VERSION, *, cascade=False):
        descendants = tuple(sorted(key for key in state if key.startswith(rid + "/") and key not in removed))
        if not cascade and descendants:
            raise ReconcileError("Unexpected children on retirement leaf resource")
        deletion = Deletion(rid, version, descendants)
        deletions.append(deletion)
        removed.update(deletion.ids)

    for name in sorted(native):
        for tool in sorted(tools[name].values(), key=lambda item: item["name"]):
            delete(base + "/apis/" + name + "/tools/" + tool["name"], MCP_API_VERSION)
    if cid + "-pilot" in subscriptions:
        delete(base + "/subscriptions/" + cid + "-pilot")
    for rid in product_links:
        delete(rid)
    for name in sorted(owned, key=lambda value: (value not in native, value)):
        delete(base + "/apis/" + name,
               MCP_API_VERSION if name in native else API_VERSION, cascade=True)
    if product_present:
        delete(product_id, cascade=True)
    for name in sorted(present_tags):
        delete(base + "/tags/" + name)
    progress("inventory complete: full graph", base)
    return RetirementPlan(state, deletions, anchors, present_tags,
                          unavailable_by_tier=tuple(unavailable_by_tier))


def apply_retirement(client, plan, *, client_id, account, check_local, check_account):
    expected = plan.state.copy()
    for deletion in plan.deletions:
        check_local()
        check_account()
        progress("revalidate before DELETE", deletion.resource_id)
        current = inspect_retirement(client, client_id, account=account, authorized_tags=plan.tags,
                                     allow_absent_fast_path=not plan.full_inventory)
        if current.state != expected:
            raise ReconcileError("Azure inventory changed during retirement; no further DELETEs. Preview again.")
        check_local()
        if deletion not in current.deletions:
            raise ReconcileError("Reviewed DELETE no longer matches the exact current retirement plan")
        client.delete_reviewed(deletion)
        for rid in deletion.ids:
            expected.pop(rid)
    check_local()
    check_account()
    progress("verify final absence", client.base)
    final = inspect_retirement(client, client_id, account=account, authorized_tags=plan.tags,
                               allow_absent_fast_path=not plan.full_inventory)
    if final.state != expected or final.deletions:
        raise ReconcileError("Retirement postcondition not confirmed; preview again. Do not assume complete cleanup.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", help="Preserved clients/<id>; no local data is deleted or generated")
    for flag in ("subscription", "tenant", "resource-group", "apim-name"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--profile", required=True,
                        choices=["native-mcp", "rest-consumption", "policy-mcp-consumption"])
    parser.add_argument("--confirm-subscription", help="Approve displayed complete context non-interactively")
    parser.add_argument("--yes", action="store_true", help="Apply only the matching reviewed retirement plan")
    parser.add_argument("--review-token", help="Exact token from the preceding retirement preview")
    args = parser.parse_args()
    try:
        local_python(REPO_ROOT)
        directory = client_path(REPO_ROOT, args.client)
        try:
            manifest = yaml.safe_load((directory / "mcp-manifest.yaml").read_text("utf-8"))
        except yaml.YAMLError as error:
            raise ReconcileError("Invalid manifest YAML; contents suppressed") from error
        validate_manifest(manifest, directory.name)
        context = Context(args.subscription, args.tenant, args.resource_group, args.apim_name, args.profile)
        context.validate()
        fingerprint = input_fingerprint(REPO_ROOT, directory, manifest)
        account = confirm_context(context, args.confirm_subscription, run)["user"]
        client = RetirementClient(context.subscription, context.resource_group, context.apim,
                                  runner=lambda command: run(command, capture=True))
        plan = inspect_retirement(client, directory.name, account=account)
        token = plan_token(context, fingerprint, plan.state, plan.review(), ["retirement-v1", AUDIT_NOTICE])
        print("[retire-client] Retirement DRY-RUN (every direct and cascading resource deletion)")
        for collection in plan.unavailable_by_tier:
            print("  UNAVAILABLE-BY-TIER (observed Consumption): " + collection)
        for deletion in plan.deletions:
            print("  DELETE " + deletion.resource_id)
            for rid in deletion.descendants:
                print("    CASCADE DELETE " + rid)
        if not plan.deletions:
            print("  No owned client resources remain.")
        print(AUDIT_NOTICE)
        print("[retire-client] Review token: " + token)
        print("Not a transaction: concurrent changes after checks remain possible. "
              "Failure stops without rollback; preview the partial state again.")
        if not args.yes:
            print("Preview only. Repeat identical context with --yes --review-token <token> after review.")
            return
        if args.review_token != token:
            raise ReconcileError("Missing/stale retirement review token; review the refreshed plan first")

        def check_local():
            if input_fingerprint(REPO_ROOT, directory, manifest) != fingerprint:
                raise ReconcileError("Local inputs changed during retirement; preview again")

        def check_account():
            from mcp_openapi_creator_kit.gateway import verify_active_account
            from mcp_openapi_creator_kit.workflow import GatewayTarget
            verify_active_account(
                GatewayTarget(subscription=context.subscription, tenant=context.tenant,
                              resource_group=context.resource_group, apim_name=context.apim, account=account),
                lambda command: run(command, capture=True))

        apply_retirement(client, plan, client_id=directory.name, account=account,
                         check_local=check_local, check_account=check_account)
        print("[retire-client] Owned APIM mock resources retired and absence verified. " + AUDIT_NOTICE)
    except (ReconcileError, RuntimeError, ValueError, KeyError, TypeError, OSError):
        # Even JSON/YAML/provider exceptions can include credential/policy text.
        error = sys.exception()
        message = str(error) if isinstance(error, ReconcileError) else "Invalid retirement input/response; details suppressed"
        print("[retire-client] ERROR: " + message, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
