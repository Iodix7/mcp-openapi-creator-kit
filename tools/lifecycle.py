"""Safe APIM lifecycle reconciliation for resources owned by one client."""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

API_VERSION = "2024-06-01-preview"
MCP_API_VERSION = "2025-09-01-preview"


class ReconcileError(RuntimeError):
    pass


@dataclass
class DesiredState:
    apis: set[str] = field(default_factory=set)
    native_tools: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class ActualApi:
    name: str
    api_type: str

    @property
    def is_native_mcp(self) -> bool:
        return self.api_type.lower() == "mcp"


@dataclass
class ReconcilePlan:
    orphan_tools: list[tuple[str, str]] = field(default_factory=list)
    orphan_apis: list[ActualApi] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.orphan_tools and not self.orphan_apis


class AzRestClient:
    def __init__(self, subscription: str, resource_group: str, apim_name: str,
                 runner=None):
        self.subscription = subscription
        self.resource_group = resource_group
        self.apim_name = apim_name
        self.runner = runner or self._run
        self.base = (f"/subscriptions/{subscription}/resourceGroups/{resource_group}/"
                     f"providers/Microsoft.ApiManagement/service/{apim_name}")

    @staticmethod
    def _run(args: list[str]) -> str:
        executable = shutil.which(args[0])
        if executable is None:
            raise ReconcileError(f"command '{args[0]}' not found in PATH")
        process = subprocess.run([executable, *args[1:]],
                                 capture_output=True, text=True)
        if process.returncode != 0:
            raise ReconcileError(
                f"command failed: {' '.join(args)}\n{process.stderr.strip()}")
        return process.stdout

    def request(self, method: str, uri: str) -> dict:
        output = self.runner(["az", "rest", "--method", method,
                              "--uri", uri, "-o", "json"])
        if not output.strip():
            return {}
        return json.loads(output)

    def paged(self, uri: str) -> list[dict]:
        values = []
        next_uri = uri
        while next_uri:
            response = self.request("GET", next_uri)
            values.extend(response.get("value", []))
            next_uri = response.get("nextLink")
        return values

    @staticmethod
    def segment(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    def list_apis(self) -> list[dict]:
        return self.paged(f"{self.base}/apis?api-version={API_VERSION}")

    def list_api_tags(self, api_name: str) -> set[str]:
        values = self.paged(
            f"{self.base}/apis/{self.segment(api_name)}/tags?api-version={API_VERSION}")
        return {value["name"] for value in values}

    def list_tools(self, api_name: str) -> set[str]:
        values = self.paged(
            f"{self.base}/apis/{self.segment(api_name)}/tools?"
            f"api-version={MCP_API_VERSION}")
        return {value["name"] for value in values}

    def delete_tool(self, api_name: str, tool_name: str):
        uri = (f"{self.base}/apis/{self.segment(api_name)}/tools/"
               f"{self.segment(tool_name)}?api-version={MCP_API_VERSION}")
        self.runner(["az", "rest", "--method", "DELETE", "--headers",
                     "If-Match=*", "--uri", uri, "--output", "none"])

    def delete_api(self, api_name: str):
        uri = f"{self.base}/apis/{self.segment(api_name)}?api-version={API_VERSION}"
        self.runner(["az", "rest", "--method", "DELETE", "--headers",
                     "If-Match=*", "--uri", uri, "--output", "none"])


def desired_state(client_dir: Path, gateway_profile: str) -> DesiredState:
    manifest = yaml.safe_load(
        (client_dir / "mcp-manifest.yaml").read_text(encoding="utf-8"))
    client = manifest["client"]
    exposure = manifest.get("mcpExposure") or {}
    mode = exposure.get("mode", "perApi")
    facade_name = exposure.get("facadeName", "agent")
    state = DesiredState()

    for api in manifest.get("apis", []):
        state.apis.add(f"{client}-{api['name']}")
    if mode != "perApi":
        state.apis.add(f"{client}-{facade_name}")

    if gateway_profile == "native-mcp":
        if mode != "facade":
            for api in manifest.get("apis", []):
                server = f"{client}-{api['name']}-mcp"
                state.apis.add(server)
                state.native_tools[server] = set(api.get("mcpTools", []))
        if mode != "perApi":
            server = f"{client}-{facade_name}-mcp"
            state.apis.add(server)
            state.native_tools[server] = {
                tool for api in manifest.get("apis", [])
                for tool in api.get("mcpTools", [])}
    elif gateway_profile == "policy-mcp-consumption":
        index_path = client_dir / "generated" / "policy-mcp" / "servers.json"
        if not index_path.exists():
            raise ReconcileError(
                f"{index_path} not found: generate policy MCP artifacts first")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        state.apis.update(server["resourceName"] for server in index.get("servers", []))
    elif gateway_profile != "rest-consumption":
        raise ReconcileError(f"unknown gateway profile: {gateway_profile}")
    return state


def discover_owned_apis(client: AzRestClient, client_id: str) -> dict[str, ActualApi]:
    owned = {}
    prefix = f"{client_id}-"
    for value in client.list_apis():
        name = value.get("name", "")
        if not name.startswith(prefix):
            continue
        tags = client.list_api_tags(name)
        if client_id not in tags:
            continue
        api_type = ((value.get("properties") or {}).get("type") or "http")
        owned[name] = ActualApi(name, api_type)
    return owned


def build_plan(client: AzRestClient, desired: DesiredState,
               actual: dict[str, ActualApi]) -> ReconcilePlan:
    plan = ReconcilePlan()
    for name, api in actual.items():
        if not api.is_native_mcp:
            continue
        expected = desired.native_tools.get(name, set())
        current = client.list_tools(name)
        plan.orphan_tools.extend((name, tool) for tool in sorted(current - expected))

    orphan_names = set(actual) - desired.apis
    plan.orphan_apis = sorted(
        (actual[name] for name in orphan_names),
        key=lambda api: (not api.is_native_mcp, api.name))
    return plan


def apply_plan(client: AzRestClient, plan: ReconcilePlan):
    for api_name, tool_name in plan.orphan_tools:
        client.delete_tool(api_name, tool_name)
    for api in plan.orphan_apis:
        client.delete_api(api.name)


def format_plan(plan: ReconcilePlan) -> list[str]:
    lines = []
    lines.extend(f"DELETE tool {api_name}/{tool_name}"
                 for api_name, tool_name in plan.orphan_tools)
    lines.extend(f"DELETE API {api.name} ({api.api_type})" for api in plan.orphan_apis)
    return lines
