#!/usr/bin/env python3
"""
verify-mcp.py - MCP protocol smoke test for a deployed client.

Usage: mcp-kit verify-mcp clients/<clientId>

What it does (pre-demo validation, run after EVERY deploy and ALWAYS before
any demo):
  1. derives expectations FROM MANIFEST: which MCP servers exist
      (perApi: one per API; facade: one; both: both) and which tools each
      must expose (mcpTools) - no hardcoded expectations
  2. with --gateway-url and --profile, uses that HTTPS origin and private
      environment credentials (no azd or management calls); without flags,
      retains azd discovery
  3. for each server: initialize -> tools/list -> EXACT comparison of actual
      tools against manifest (missing AND extra; orphaned incremental deploy
      leftovers are detected here)
  4. native --exercise-mock opts into successful mock examples and schemas;
      --fixture selects a preview-only named allowlist, requiring a matching
      --confirm-fixtures token before business calls

See docs/extended-verification.md for private Entra/dual auth and fixture limits.

Exit code 0 = fully compliant; 1 = at least one server non-compliant or
unreachable. Output prints per-server diffs.
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from mcp_openapi_creator_kit.policy import load_client, sample_tool_calls
from mcp_openapi_creator_kit._commands.verification import (
    Credentials, PrivateArgumentParser, VerificationFailure, add_extended_arguments, endpoint_url, explicit_credentials,
    fixture_cases, fixture_endpoint, gateway_origin, mock_native_cases, native_arguments,
    open_request, read_response, review_fixtures, rpc_message, safe_error, tool_payload, validate_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_ERRORS = (urllib.error.HTTPError, urllib.error.URLError, RuntimeError,
                 KeyError, ValueError, TypeError, AttributeError, OSError, TimeoutError,
                 http.client.HTTPException, yaml.YAMLError)


def die(msg: str):
    print(f"[verify-mcp] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(args: list, capture: bool = False) -> str:
    exe = shutil.which(args[0])
    if exe is None:
        die(f"command '{args[0]}' not found in PATH")
    proc = subprocess.run([exe, *args[1:]], cwd=REPO_ROOT,
                          capture_output=capture, text=True)
    if proc.returncode != 0:
        die("command failed; inspect CLI diagnostics privately (raw output suppressed)")
    return proc.stdout if capture else ""


def azd_env() -> dict:
    out = run(["azd", "env", "get-values"], capture=True)
    values = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip().strip('"')
    return values


def expected_servers(manifest: dict) -> dict:
    """From manifest: MCP server name -> (relative path, expected tool set).
    Mirrors emit_client_bicep logic (perApi/facade/both)."""
    client = manifest["client"]
    exposure = manifest.get("mcpExposure") or {}
    mode = exposure.get("mode", "perApi")
    facade_name = exposure.get("facadeName", "agent")
    per_api = mode != "facade"
    facade = mode != "perApi"
    servers = {}
    if per_api:
        for api in manifest.get("apis", []):
            name = api["name"]
            servers[f"{client}-{name}-mcp"] = (
                f"{client}/{name}-mcp/mcp", set(api.get("mcpTools", [])))
    if facade:
        all_tools = {t for api in manifest.get("apis", [])
                     for t in api.get("mcpTools", [])}
        servers[f"{client}-{facade_name}-mcp"] = (
            f"{client}/{facade_name}-mcp/mcp", all_tools)
    return servers


def expected_policy_servers(client_dir: Path) -> dict:
    """From generated artifacts: policy MCP server -> path and expected tools."""
    index_path = client_dir / "generated" / "policy-mcp" / "servers.json"
    if not index_path.exists():
        die(f"{index_path.relative_to(REPO_ROOT)} not found: run "
            "mcp-kit build-policy clients/<client>")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    return {
        server["resourceName"]: (
            f"{server['path']}/mcp", set(server.get("tools", [])))
        for server in index.get("servers", [])
    }


def mcp_rpc(url: str, key: str | Credentials, payload: dict, sid=None, *, strict=False, protocol=None):
    credentials = key if isinstance(key, Credentials) else Credentials(key)
    hdr = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
    hdr = credentials.headers(hdr)
    if sid:
        hdr["Mcp-Session-Id"] = sid
    if protocol:
        hdr["Mcp-Protocol-Version"] = protocol
    req = urllib.request.Request(url, json.dumps(payload).encode(), hdr)
    with open_request(req, timeout=30) as r:
        new_sid = r.headers.get("Mcp-Session-Id")
        body = read_response(r)
        if strict:
            if new_sid and any(ord(c) < 33 or ord(c) > 126 for c in new_sid):
                raise VerificationFailure("MCP returned an invalid session identifier")
            if new_sid and sid and new_sid != sid:
                raise VerificationFailure("MCP session changed during verification; no automatic retry")
            if "id" in payload and r.status != 200:
                raise VerificationFailure("MCP request did not return HTTP 200")
    if strict:
        return rpc_message(body, payload.get("id")), new_sid
    data = None
    for line in body.splitlines():
        if line.startswith("data:"):
            data = json.loads(line[5:])
    if data is None and body.strip():
        data = json.loads(body)
    return data, new_sid


def discover_tools(url: str, key: str | Credentials, *, extended=False):
    options = {"strict": True} if extended else {}
    init, sid = mcp_rpc(url, key, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18" if extended else "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "verify-mcp", "version": "1.0"}}}, **options)
    if not isinstance(init, dict) or not isinstance(init.get("result"), dict):
        raise RuntimeError("initialize without result")
    protocol = None
    if extended:
        protocol = init["result"].get("protocolVersion")
        if (protocol not in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
                or not isinstance(init["result"].get("capabilities", {}).get("tools"), dict)):
            raise VerificationFailure("MCP did not negotiate a supported tools protocol")
        options["protocol"] = protocol
    mcp_rpc(url, key, {"jsonrpc": "2.0",
                       "method": "notifications/initialized"}, sid, **options)
    descriptors = {}
    cursors = set()
    cursor = None
    for page in range(1000):
        request = {"jsonrpc": "2.0", "id": page + 2, "method": "tools/list"}
        if cursor is not None:
            request["params"] = {"cursor": cursor}
        tools, _ = mcp_rpc(url, key, request, sid, **options)
        result = tools.get("result") if isinstance(tools, dict) else None
        entries = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(entries, list) or any(
                not isinstance(t, dict) or not isinstance(t.get("name"), str)
                or not t["name"] for t in entries):
            raise RuntimeError("tools/list without valid tool names")
        for tool in entries:
            if tool["name"] in descriptors:
                raise RuntimeError("tools/list returned duplicate tool names")
            descriptors[tool["name"]] = tool
        cursor = result.get("nextCursor")
        if cursor is None:
            return descriptors, sid, protocol
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise RuntimeError("tools/list returned an invalid or repeated pagination cursor")
        cursors.add(cursor)
    raise RuntimeError("tools/list exceeded the 1000-page verification limit")


def list_tools(url: str, key: str | Credentials) -> set:
    return set(discover_tools(url, key)[0])


def verify_native_examples(gateway, credentials, manifest, servers, cases, *, controlled=False):
    prepared = []
    for name, (path, expected) in sorted(servers.items()):
        selected = [case for case in cases if case.operation in expected
                    and (not controlled or fixture_endpoint(manifest, case, mcp=True) == path)]
        if controlled and not selected:
            continue
        url = endpoint_url(gateway, path)
        descriptors, sid, protocol = discover_tools(url, credentials, extended=True)
        if set(descriptors) != expected:
            raise VerificationFailure("Native tool inventory differs from the manifest; no business calls were sent")
        for case in selected:
            descriptor = descriptors[case.operation]
            prepared.append((url, sid, protocol, case, descriptor, native_arguments(case, descriptor)))
    if not prepared or controlled and len(prepared) != len(cases):
        raise VerificationFailure("Selected fixture routes do not resolve exactly once")
    for index, (url, sid, protocol, case, descriptor, arguments) in enumerate(prepared, start=10000):
        response, _ = mcp_rpc(url, credentials, {
            "jsonrpc": "2.0", "id": index, "method": "tools/call",
            "params": {"name": case.operation, "arguments": arguments}},
            sid, strict=True, protocol=protocol)
        case.assert_payload(tool_payload(response, descriptor))
        print(f"  [OK] {case.label}: MCP JSON payload, contract schema and example")
    print(f"[verify-mcp] RESULT: {len(prepared)} successful native example calls verified; "
          "HTTP status and non-2xx branches are not asserted by MCP")


def verify_policy_tool_calls(url: str, key: str, tool_names: set,
                             definitions: dict):
    request_id = 100
    for name in sorted(tool_names):
        definition = definitions[name]
        calls = sample_tool_calls(definition)
        for branch, (arguments, expected_payload, expected_error) in enumerate(
                calls, start=1):
            request_id += 1
            response, _ = mcp_rpc(url, key, {
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}})
            result = response.get("result") if isinstance(response, dict) else None
            label = f"{name} x-mock branch {branch}/{len(calls)}"
            if not result or not result.get("content"):
                raise RuntimeError(
                    f"{label}: tools/call without result.content")
            payload = json.loads(result["content"][0]["text"])
            if payload != expected_payload or result.get("isError") != expected_error:
                raise RuntimeError(
                    f"{label}: result does not match example or expected isError")


def get_pilot_key(client_id: str, env: dict) -> str:
    if os.environ.get("MCP_KEY"):
        return os.environ["MCP_KEY"]
    norm = {k.replace("_", "").lower(): v for k, v in env.items()}
    sub = norm.get("azuresubscriptionid")
    rg = norm.get("azureresourcegroup")
    apim = norm.get("apimname")
    if not (sub and rg and apim):
        die("subscription/RG/apimName not found in azd environment: "
            "run `azd up`, or pass key via MCP_KEY")
    out = run(["az", "rest", "--method", "POST", "--uri",
               f"/subscriptions/{sub}/resourceGroups/{rg}/providers/"
               f"Microsoft.ApiManagement/service/{apim}/subscriptions/"
               f"{client_id}-pilot/listSecrets?api-version=2024-06-01-preview",
               "--query", "primaryKey", "-o", "tsv"], capture=True)
    return out.strip()


def main():
    parser = PrivateArgumentParser(prog="mcp-kit verify-mcp", description=__doc__)
    parser.add_argument("client", help="client directory")
    parser.add_argument("--gateway-url", help="HTTPS gateway origin; private environment credentials, no azd/Azure discovery")
    parser.add_argument("--profile", choices=("native-mcp", "policy-mcp-consumption"),
                        help="required with --gateway-url; native MCP performs discovery only by default")
    add_extended_arguments(parser)
    parser.add_argument("--exercise-mock", action="store_true",
                        help="opt in to successful native mock examples and schema assertions")
    args = parser.parse_args()
    explicit = args.gateway_url is not None or args.profile is not None
    if explicit and (args.gateway_url is None or args.profile is None):
        die("explicit endpoint mode requires both --gateway-url and --profile; no azd fallback")
    extended = bool(args.auth_mode or args.fixture or args.confirm_fixtures is not None or args.exercise_mock)
    if extended and not explicit:
        die("extended verification requires --gateway-url and --profile; no azd fallback")
    if args.confirm_fixtures is not None and not args.fixture:
        die("--confirm-fixtures requires an explicit --fixture allowlist")
    if args.exercise_mock and args.fixture:
        die("--exercise-mock and --fixture are mutually exclusive")
    if (args.exercise_mock or args.fixture) and args.profile != "native-mcp":
        die("extended example selection requires --profile native-mcp; policy MCP keeps its mock-only checks")
    try:
        if explicit:
            gateway = gateway_origin(args.gateway_url)
    except ValueError as error:
        die(str(error))
    client_dir = REPO_ROOT / args.client
    if __package__:
        from .deployment import client_path
        client_dir = client_path(REPO_ROOT, args.client)
    manifest_path = client_dir / "mcp-manifest.yaml"
    if not manifest_path.exists():
        die(f"{args.client}: mcp-manifest.yaml not found")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    client_id = manifest.get("client", client_dir.name)

    gateway_profile = args.profile
    if not explicit:
        env = azd_env()
        norm = {k.replace("_", "").lower(): v for k, v in env.items()}
        gateway = norm.get("apimgatewayurl") or (
            f"https://{norm['apimname']}.azure-api.net" if norm.get("apimname")
            else die("apimGatewayUrl/apimName not found in azd environment"))
        gateway_profile = norm.get("gatewayprofile", "native-mcp")
    if gateway_profile == "rest-consumption":
        die("GATEWAY_PROFILE=rest-consumption does not expose MCP; use verify-rest.py")
    if gateway_profile not in ("native-mcp", "policy-mcp-consumption"):
        die("unsupported gateway profile; use native-mcp or policy-mcp-consumption")
    try:
        gateway = gateway_origin(gateway)
        validate_manifest(manifest, mock_only=gateway_profile == "policy-mcp-consumption",
                          consumption=gateway_profile == "policy-mcp-consumption", requested_auth=args.auth_mode)
    except ValueError as error:
        die(str(error))
    if args.fixture or args.exercise_mock:
        try:
            cases = (fixture_cases(REPO_ROOT, manifest, args.fixture, mcp=True) if args.fixture else
                     mock_native_cases(REPO_ROOT, manifest))
            if args.fixture and not review_fixtures(
                    REPO_ROOT, manifest, gateway, args.auth_mode, cases, args.confirm_fixtures, mcp=True):
                return
            credentials = explicit_credentials(manifest, args.auth_mode)
            verify_native_examples(gateway, credentials, manifest, expected_servers(manifest), cases,
                                   controlled=bool(args.fixture))
        except VERIFY_ERRORS as error:
            die(safe_error(error))
        return
    try:
        key = explicit_credentials(manifest, args.auth_mode) if explicit else Credentials(get_pilot_key(client_id, env))
    except ValueError as error:
        die(str(error))
    servers = (expected_policy_servers(client_dir)
               if gateway_profile == "policy-mcp-consumption"
               else expected_servers(manifest))
    transport = "policy" if gateway_profile == "policy-mcp-consumption" else "native"
    policy_definitions = {}
    if gateway_profile == "policy-mcp-consumption":
        _, by_api = load_client(REPO_ROOT, client_dir)
        policy_definitions = {tool.name: tool for tools in by_api.values() for tool in tools}
        if not servers or set().union(*(tools for _, tools in servers.values())) != set(policy_definitions):
            die("policy MCP server index differs from manifest; rebuild with build-policy-mcp.py before verification")
    print(f"[verify-mcp] {client_id}: {len(servers)} expected MCP {transport} servers "
          f"on {gateway}")
    failed = False
    for name, (path, expected) in sorted(servers.items()):
        try:
            url = endpoint_url(gateway, path)
            actual = list_tools(url, key)
        except VERIFY_ERRORS as e:
            print(f"  [FAIL] {name}: UNREACHABLE ({safe_error(e)})")
            failed = True
            continue
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if not missing and not extra:
            if gateway_profile == "policy-mcp-consumption":
                try:
                    verify_policy_tool_calls(url, key, expected, policy_definitions)
                except VERIFY_ERRORS as e:
                    print(f"  [FAIL] {name}: tools/call not compliant ({safe_error(e)})")
                    failed = True
                    continue
            print(f"  [OK]   {name}: {len(actual)} tools, compliant with manifest"
                  + (" and examples" if gateway_profile == "policy-mcp-consumption" else ""))
        else:
            failed = True
            print(f"  [FAIL] {name}: not compliant")
            if missing:
                print(f"       missing (in manifest, not on server): {missing}")
            if extra:
                safe_extra = [key.redact(tool) for tool in extra]
                print(f"       extra (on server, not in manifest - "
                      f"orphaned resources to clean up?): {safe_extra}")
    if failed:
        print("[verify-mcp] RESULT: not compliant - see details above "
              "(orphan cleanup: skills/lifecycle.md)")
        sys.exit(1)
    print("[verify-mcp] RESULT: all servers comply with manifest")


if __name__ == "__main__":
    main()
