import json
import importlib.util
import sys
from pathlib import Path

import yaml

_TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS))

from lifecycle import (ActualApi, DesiredState, apply_plan, build_plan,
                       desired_state, discover_owned_apis, format_plan)

_reconcile_spec = importlib.util.spec_from_file_location(
    "reconcile_all", _TOOLS / "reconcile-all.py")
reconcile_all = importlib.util.module_from_spec(_reconcile_spec)
_reconcile_spec.loader.exec_module(reconcile_all)

_reconcile_client_spec = importlib.util.spec_from_file_location(
    "reconcile_client", _TOOLS / "reconcile-client.py")
reconcile_client = importlib.util.module_from_spec(_reconcile_client_spec)
_reconcile_client_spec.loader.exec_module(reconcile_client)


class FakeClient:
    def __init__(self, apis=None, tags=None, tools=None):
        self.apis = apis or []
        self.tags = tags or {}
        self.tools = tools or {}
        self.actions = []

    def list_apis(self):
        return self.apis

    def list_api_tags(self, api_name):
        return set(self.tags.get(api_name, []))

    def list_tools(self, api_name):
        return set(self.tools.get(api_name, []))

    def delete_tool(self, api_name, tool_name):
        self.actions.append(("tool", api_name, tool_name))

    def delete_api(self, api_name):
        self.actions.append(("api", api_name))


def write_client(tmp_path, profile_mode="facade"):
    client_dir = tmp_path / "clients" / "acme"
    client_dir.mkdir(parents=True)
    manifest = {
        "client": "acme", "displayName": "Acme",
        "mcpExposure": {"mode": profile_mode, "facadeName": "agent"},
        "apis": [
            {"name": "orders", "backend": {"mode": "mock"},
             "mcpTools": ["get-order", "create-order"]},
            {"name": "stock", "backend": {"mode": "mock"},
             "mcpTools": ["get-stock"]},
        ],
    }
    (client_dir / "mcp-manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return client_dir


def test_desired_native_facade_contains_rest_and_native_tools(tmp_path):
    client_dir = write_client(tmp_path)

    state = desired_state(client_dir, "native-mcp")

    assert state.apis == {
        "acme-orders", "acme-stock", "acme-agent", "acme-agent-mcp"}
    assert state.native_tools == {"acme-agent-mcp": {
        "get-order", "create-order", "get-stock"}}


def test_desired_policy_uses_generated_shards(tmp_path):
    client_dir = write_client(tmp_path)
    generated = client_dir / "generated" / "policy-mcp"
    generated.mkdir(parents=True)
    (generated / "servers.json").write_text(json.dumps({"servers": [
        {"resourceName": "acme-agent-policy-mcp-1"},
        {"resourceName": "acme-agent-policy-mcp-2"},
    ]}), encoding="utf-8")

    state = desired_state(client_dir, "policy-mcp-consumption")

    assert state.apis == {
        "acme-orders", "acme-stock", "acme-agent",
        "acme-agent-policy-mcp-1", "acme-agent-policy-mcp-2"}
    assert state.native_tools == {}


def test_discovery_requires_prefix_and_ownership_tag():
    client = FakeClient(
        apis=[
            {"name": "acme-orders", "properties": {"type": "http"}},
            {"name": "acme-manual", "properties": {"type": "http"}},
            {"name": "other-orders", "properties": {"type": "http"}},
        ],
        tags={
            "acme-orders": ["acme"],
            "acme-manual": ["manual"],
            "other-orders": ["acme"],
        })

    owned = discover_owned_apis(client, "acme")

    assert set(owned) == {"acme-orders"}


def test_plan_removes_tools_then_native_server_then_rest_api():
    desired = DesiredState(
        apis={"acme-orders", "acme-agent", "acme-agent-mcp"},
        native_tools={"acme-agent-mcp": {"get-order"}})
    actual = {
        "acme-orders": ActualApi("acme-orders", "http"),
        "acme-agent": ActualApi("acme-agent", "http"),
        "acme-agent-mcp": ActualApi("acme-agent-mcp", "mcp"),
        "acme-old-mcp": ActualApi("acme-old-mcp", "mcp"),
        "acme-old": ActualApi("acme-old", "http"),
    }
    client = FakeClient(tools={
        "acme-agent-mcp": ["get-order", "renamed-order"],
        "acme-old-mcp": ["old-tool"],
    })

    plan = build_plan(client, desired, actual)

    assert plan.orphan_tools == [
        ("acme-agent-mcp", "renamed-order"),
        ("acme-old-mcp", "old-tool"),
    ]
    assert [api.name for api in plan.orphan_apis] == ["acme-old-mcp", "acme-old"]
    assert format_plan(plan) == [
        "DELETE tool acme-agent-mcp/renamed-order",
        "DELETE tool acme-old-mcp/old-tool",
        "DELETE API acme-old-mcp (mcp)",
        "DELETE API acme-old (http)",
    ]

    apply_plan(client, plan)

    assert client.actions == [
        ("tool", "acme-agent-mcp", "renamed-order"),
        ("tool", "acme-old-mcp", "old-tool"),
        ("api", "acme-old-mcp"),
        ("api", "acme-old"),
    ]


def test_noop_plan_is_empty():
    desired = DesiredState(apis={"acme-orders"})
    actual = {"acme-orders": ActualApi("acme-orders", "http")}

    plan = build_plan(FakeClient(), desired, actual)

    assert plan.empty


def test_removed_clients_file_is_empty_for_clean_public_snapshot():
    removed = reconcile_all.removed_client_ids({"sample"})

    assert removed == []


def test_removed_client_empty_state_deletes_only_owned_resources():
    client = FakeClient(tools={"retired-agent-mcp": ["get-context"]})
    actual = {
        "retired-agent-mcp": ActualApi("retired-agent-mcp", "mcp"),
        "retired-agent": ActualApi("retired-agent", "http"),
    }

    plan = build_plan(client, DesiredState(), actual)

    assert plan.orphan_tools == [("retired-agent-mcp", "get-context")]
    assert [api.name for api in plan.orphan_apis] == [
        "retired-agent-mcp", "retired-agent"]


def test_reconcile_all_runs_active_clients_in_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile_all, "azd_env", lambda: {
        "apimName": "demo-apim",
        "AZURE_SUBSCRIPTION_ID": "sub",
        "AZURE_RESOURCE_GROUP": "rg",
        "GATEWAY_PROFILE": "native-mcp",
    })
    monkeypatch.setattr(reconcile_all, "run", lambda args, capture=False: calls.append(args) or "")
    monkeypatch.delenv(reconcile_all.APPLY_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["reconcile-all.py", "--apply-if-env"])

    reconcile_all.main()

    assert all("--removed-client" not in call for call in calls)
    assert any("clients\\sample" in " ".join(call) or
               "clients/sample" in " ".join(call) for call in calls)
    assert all("--apply" not in call for call in calls)


def test_reconcile_all_apply_requires_explicit_environment_opt_in(monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile_all, "azd_env", lambda: {
        "apimName": "demo-apim",
        "AZURE_SUBSCRIPTION_ID": "sub",
        "AZURE_RESOURCE_GROUP": "rg",
    })
    monkeypatch.setattr(reconcile_all, "run", lambda args, capture=False: calls.append(args) or "")
    monkeypatch.setenv(reconcile_all.APPLY_ENV, "true")
    monkeypatch.setattr(sys, "argv", ["reconcile-all.py", "--apply-if-env"])

    reconcile_all.main()

    assert calls
    assert all("--apply" in call for call in calls)


def test_reconcile_client_explicit_target_does_not_require_azd(
        tmp_path, monkeypatch):
    client_dir = write_client(tmp_path)
    monkeypatch.setattr(reconcile_client, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        reconcile_client, "azd_env",
        lambda: (_ for _ in ()).throw(AssertionError("azd must not run")))
    monkeypatch.setattr(reconcile_client, "AzRestClient", lambda *args: FakeClient())
    monkeypatch.setattr(sys, "argv", [
        "reconcile-client.py", str(client_dir.relative_to(tmp_path)),
        "--profile", "native-mcp", "--subscription", "sub",
        "--resource-group", "rg", "--apim-name", "apim",
    ])

    reconcile_client.main()
