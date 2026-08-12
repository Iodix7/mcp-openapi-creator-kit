#!/usr/bin/env python3
"""
verify-mcp.py - MCP protocol smoke test for a deployed client.

Usage: python tools/verify-mcp.py clients/<clientId>

What it does (pre-demo validation, run after EVERY deploy and ALWAYS before
any demo):
  1. derives expectations FROM MANIFEST: which MCP servers exist
      (perApi: one per API; facade: one; both: both) and which tools each
      must expose (mcpTools) - no hardcoded expectations
  2. reads gateway and key from current azd environment (like deploy-client:
      requires a completed `azd up`); alternatively key can be provided via
      MCP_KEY environment variable
  3. for each server: initialize -> tools/list -> EXACT comparison of actual
      tools against manifest (missing AND extra; orphaned incremental deploy
      leftovers are detected here)

Exit code 0 = fully compliant; 1 = at least one server non-compliant or
unreachable. Output prints per-server diffs.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from mcp_policy import load_client, sample_tool_calls

REPO_ROOT = Path(__file__).resolve().parent.parent


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
        detail = (proc.stderr or "").strip() if capture else ""
        die(f"command failed: {' '.join(args)}" + (f"\n{detail}" if detail else ""))
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
            "python tools/build-policy-mcp.py <client>")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    return {
        server["resourceName"]: (
            f"{server['path']}/mcp", set(server.get("tools", [])))
        for server in index.get("servers", [])
    }


def mcp_rpc(url: str, key: str, payload: dict, sid=None):
    hdr = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream",
           "Ocp-Apim-Subscription-Key": key}
    if sid:
        hdr["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, json.dumps(payload).encode(), hdr)
    with urllib.request.urlopen(req, timeout=30) as r:
        new_sid = r.headers.get("Mcp-Session-Id")
        body = r.read().decode()
    data = None
    for line in body.splitlines():
        if line.startswith("data:"):
            data = json.loads(line[5:])
    if data is None and body.strip():
        data = json.loads(body)
    return data, new_sid


def list_tools(url: str, key: str) -> set:
    init, sid = mcp_rpc(url, key, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "verify-mcp", "version": "1.0"}}})
    if "result" not in (init or {}):
        raise RuntimeError(f"initialize without result: {init}")
    mcp_rpc(url, key, {"jsonrpc": "2.0",
                       "method": "notifications/initialized"}, sid)
    tools, _ = mcp_rpc(url, key, {"jsonrpc": "2.0", "id": 2,
                                  "method": "tools/list"}, sid)
    return {t["name"] for t in tools["result"]["tools"]}


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
                    f"{label}: tools/call without result.content: {response}")
            payload = json.loads(result["content"][0]["text"])
            if payload != expected_payload or result.get("isError") != expected_error:
                raise RuntimeError(
                    f"{label}: result does not match example "
                    f"(isError={result.get('isError')}, expected={expected_error})")


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
    if len(sys.argv) != 2:
        die("usage: verify-mcp.py clients/<clientId>")
    client_dir = REPO_ROOT / sys.argv[1]
    manifest_path = client_dir / "mcp-manifest.yaml"
    if not manifest_path.exists():
        die(f"{sys.argv[1]}: mcp-manifest.yaml not found")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    client_id = manifest.get("client", client_dir.name)

    env = azd_env()
    norm = {k.replace("_", "").lower(): v for k, v in env.items()}
    gateway = norm.get("apimgatewayurl") or (
        f"https://{norm['apimname']}.azure-api.net" if norm.get("apimname")
        else die("apimGatewayUrl/apimName not found in azd environment"))
    key = get_pilot_key(client_id, env)

    gateway_profile = norm.get("gatewayprofile", "native-mcp")
    if gateway_profile == "rest-consumption":
        die("GATEWAY_PROFILE=rest-consumption does not expose MCP; use verify-rest.py")
    servers = (expected_policy_servers(client_dir)
               if gateway_profile == "policy-mcp-consumption"
               else expected_servers(manifest))
    transport = "policy" if gateway_profile == "policy-mcp-consumption" else "native"
    policy_definitions = {}
    if gateway_profile == "policy-mcp-consumption":
        _, by_api = load_client(REPO_ROOT, client_dir)
        policy_definitions = {tool.name: tool for tools in by_api.values() for tool in tools}
    print(f"[verify-mcp] {client_id}: {len(servers)} expected MCP {transport} servers "
          f"su {gateway}")
    failed = False
    for name, (path, expected) in sorted(servers.items()):
        url = f"{gateway}/{path}"
        try:
            actual = list_tools(url, key)
        except (urllib.error.HTTPError, urllib.error.URLError,
                RuntimeError, KeyError, TimeoutError) as e:
            print(f"  [FAIL] {name}: UNREACHABLE ({e})")
            failed = True
            continue
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if not missing and not extra:
            if gateway_profile == "policy-mcp-consumption":
                try:
                    verify_policy_tool_calls(url, key, expected, policy_definitions)
                except (RuntimeError, KeyError, ValueError, json.JSONDecodeError) as e:
                    print(f"  [FAIL] {name}: tools/call not compliant ({e})")
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
                print(f"       extra (on server, not in manifest - "
                      f"orphaned resources to clean up?): {extra}")
    if failed:
        print("[verify-mcp] RESULT: not compliant - see details above "
              "(orphan cleanup: skills/lifecycle.md)")
        sys.exit(1)
    print("[verify-mcp] RESULT: all servers comply with manifest")


if __name__ == "__main__":
    main()
