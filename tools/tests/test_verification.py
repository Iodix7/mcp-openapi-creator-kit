"""Offline endpoint verification: real urllib routing with mocked HTTPS transport."""
import copy
import io
import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from urllib.response import addinfourl

import pytest
import yaml

from test_build_facade import CONTRACT, MANIFEST, vm, vr
import verification

ORIGIN = "https://approved.example.com"
KEY = "test-only-key-do-not-log"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    client = tmp_path / "clients" / "demo"
    client.mkdir(parents=True)
    api = tmp_path / "apis" / "things"
    api.mkdir(parents=True)
    (client / "mcp-manifest.yaml").write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")
    (api / "openapi.yaml").write_text(yaml.safe_dump(CONTRACT), encoding="utf-8")
    for module in (vm, vr):
        monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(module, "azd_env", lambda: pytest.fail("azd called"))
        monkeypatch.setattr(module, "run", lambda *a, **k: pytest.fail("CLI/management called"))
    monkeypatch.setattr(vm, "get_pilot_key", lambda *a: pytest.fail("listSecrets fallback"))
    monkeypatch.setattr(vr, "pilot_key", lambda *a: pytest.fail("listSecrets fallback"))
    monkeypatch.setenv("MCP_KEY", KEY)
    monkeypatch.setenv("REST_KEY", "legacy-key-must-not-be-used")
    return client


def cli(monkeypatch, module, *flags):
    monkeypatch.setattr(sys, "argv", [module.__file__, "clients/demo", *flags])
    module.main()


def explicit_flags(module):
    return ["--gateway-url", ORIGIN, *(["--profile", "native-mcp"] if module is vm else [])]


def transport(monkeypatch, responder):
    calls = []

    class FakeHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            calls.append(request)
            code, headers, body = responder(request)
            message = Message()
            for name, value in headers.items():
                message[name] = value
            response = addinfourl(io.BytesIO(body), message, request.full_url, code)
            response.msg = "fixture response"
            return response

    monkeypatch.setattr(urllib.request, "HTTPSHandler", FakeHTTPS)
    return calls


def rpc_response(request, tools=("get-thing",), sse=False):
    payload = json.loads(request.data)
    method = payload["method"]
    if method == "notifications/initialized":
        return 202, {}, b""
    result = ({"protocolVersion": "2024-11-05", "capabilities": {}}
              if method == "initialize" else {"tools": [{"name": t} for t in tools]})
    body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result})
    if sse:
        body = f"event: message\ndata: {body}\n\n"
    return 200, {"Mcp-Session-Id": "fixture-session"}, body.encode()


@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("backend", ["mock", "external"])
def test_explicit_native_discovery_only_no_azd_or_management(workspace, monkeypatch, capsys, sse, backend):
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["backend"]["mode"] = backend
    (workspace / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    calls = transport(monkeypatch, lambda req: rpc_response(req, sse=sse))
    cli(monkeypatch, vm, *explicit_flags(vm))
    payloads = [json.loads(req.data) for req in calls]
    assert [p["method"] for p in payloads] == [
        "initialize", "notifications/initialized", "tools/list"]
    assert payloads[0]["params"]["protocolVersion"] == "2024-11-05"
    assert all(req.full_url == ORIGIN + "/demo/agent-mcp/mcp" for req in calls)
    assert all(req.get_header("Ocp-apim-subscription-key") == KEY for req in calls)
    assert calls[1].get_header("Mcp-session-id") == "fixture-session"
    assert KEY not in capsys.readouterr().out


def test_mcp_discovery_includes_later_pages(workspace, monkeypatch, capsys):
    def respond(request):
        payload = json.loads(request.data)
        if payload["method"] != "tools/list":
            return rpc_response(request)
        if "params" not in payload:
            result = {"tools": [{"name": "get-thing"}], "nextCursor": "page-2"}
        else:
            assert payload["params"] == {"cursor": "page-2"}
            assert request.get_header("Mcp-session-id") == "fixture-session"
            result = {"tools": [{"name": "orphan-tool"}]}
        return 200, {}, json.dumps({"result": result}).encode()

    calls = transport(monkeypatch, respond)
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, vm, *explicit_flags(vm))
    assert error.value.code == 1
    assert len(calls) == 4
    assert "orphan-tool" in capsys.readouterr().out


@pytest.mark.parametrize("cursor", ["repeat", "", 42])
def test_mcp_rejects_invalid_or_repeated_cursor(monkeypatch, cursor):
    pages = 0

    def rpc(url, key, payload, sid=None):
        nonlocal pages
        if payload["method"] == "initialize":
            return {"result": {}}, "session"
        if payload["method"] == "notifications/initialized":
            return None, None
        pages += 1
        return {"result": {"tools": [], "nextCursor": cursor}}, None

    monkeypatch.setattr(vm, "mcp_rpc", rpc)
    with pytest.raises(RuntimeError, match="pagination cursor"):
        vm.list_tools(ORIGIN, KEY)
    assert pages == (2 if cursor == "repeat" else 1)


def test_rest_no_cases_cannot_report_success(workspace, monkeypatch, capsys):
    contract_path = workspace.parents[1] / "apis" / "things" / "openapi.yaml"
    contract = copy.deepcopy(CONTRACT)
    contract["paths"] = {}
    contract_path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    monkeypatch.setattr(vr, "open_request", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, vr, *explicit_flags(vr))
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "No REST verification cases" in captured.err
    assert "all REST mocks comply" not in captured.out


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("key", [None, "", " ", "a\nb"])
def test_missing_or_invalid_key_fails_before_any_discovery(workspace, monkeypatch, capsys, module, key):
    if key is None:
        monkeypatch.delenv("MCP_KEY")
    else:
        monkeypatch.setenv("MCP_KEY", key)
    monkeypatch.setattr(module, "open_request", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module))
    assert "MCP_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [
    ["--gateway-url", ORIGIN], ["--profile", "native-mcp"],
    ["--gateway-url", ORIGIN, "--profile", "rest-consumption"],
    ["--gateway-url", ORIGIN, "--profile", "unknown"],
])
def test_mcp_partial_or_unsupported_flags_never_fall_back(workspace, monkeypatch, flags):
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *flags)


@pytest.mark.parametrize("url", [
    "http://approved.example.com", "https://", "//approved.example.com",
    "https://approved.example.com/api", "https://approved.example.com//",
    "https://user:password@approved.example.com",
    "https://approved.example.com?key=secret", "https://approved.example.com#secret",
    "https://approved.example.com?", "https://approved.example.com#",
    "https://approved.example.com:0", "https://approved.example.com:99999",
    "https://approved.example.com:bad", "https://approved.example.com:",
    "https://approved.example.com\\@other.example.com",
    "https://approved.example.com\n", " https://approved.example.com",
    "https://%61pproved.example.com", "https://[bad-host]",
    "https://bad_host.example.com", "https://[::1]other.example.com",
])
@pytest.mark.parametrize("module", [vm, vr])
def test_invalid_gateway_fails_closed_without_echo(workspace, monkeypatch, capsys, module, url):
    flags = explicit_flags(module)
    flags[1] = url
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *flags)
    error = capsys.readouterr().err
    assert "HTTPS origin" in error
    if url != "https://":
        assert url not in error


@pytest.mark.parametrize("url", [ORIGIN, ORIGIN + "/", ORIGIN + ":8443", "https://[::1]:443/"])
def test_supported_https_origins(url):
    assert verification.gateway_origin(url) == url.rstrip("/")


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("mode", ["entraJwt", "dual", "none"])
def test_unsupported_auth_does_not_send_key(workspace, monkeypatch, capsys, module, mode):
    manifest = copy.deepcopy(MANIFEST)
    manifest["inboundAuth"] = {"mode": mode}
    (workspace / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(module, "open_request", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module))
    assert "subscriptionKey only" in capsys.readouterr().err


@pytest.mark.parametrize("legacy", [False, True])
def test_rest_external_manifest_rejected_before_even_reads(workspace, monkeypatch, legacy):
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["backend"]["mode"] = "external"
    (workspace / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SystemExit):
        cli(monkeypatch, vr, *([] if legacy else explicit_flags(vr)))
    with pytest.raises(ValueError, match="external backends"):
        list(vr.iter_cases(manifest))


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", ["/elsewhere", "https://other.example.com/collect", "http://approved.example.com/collect"])
@pytest.mark.parametrize("module", [vm, vr])
def test_redirects_never_forward_credentials(workspace, monkeypatch, capsys, code, location, module):
    calls = transport(monkeypatch, lambda req: (code, {"Location": location}, KEY.encode()))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module))
    expected_calls = 1 if module is vm else len(list(vr.iter_cases(MANIFEST)))
    assert len(calls) == expected_calls
    assert all(req.full_url.startswith(ORIGIN + "/demo/agent") for req in calls)
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
    assert location not in output.out


def test_tool_comparison_reports_missing_extra_without_printing_key(workspace, monkeypatch, capsys):
    transport(monkeypatch, lambda req: rpc_response(req, tools=("orphan-tool", KEY)))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm))
    out = capsys.readouterr().out
    assert "get-thing" in out and "orphan-tool" in out and "missing" in out and "extra" in out
    assert KEY not in out


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("code,body", [(401, KEY.encode()), (200, b'{"error":"' + KEY.encode() + b'"}')])
def test_provider_bodies_not_logged(workspace, monkeypatch, capsys, module, code, body):
    transport(monkeypatch, lambda req: (code, {"Content-Type": "application/json"}, body))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module))
    output = capsys.readouterr()
    assert KEY not in output.out + output.err


def test_rest_explicit_preserves_refs_branches_and_mock_writes(workspace, monkeypatch, capsys):
    contract = copy.deepcopy(CONTRACT)
    operation = contract["paths"]["/v1/things/{thingId}"].pop("get")
    contract["paths"]["/v1/things/{thingId}"]["post"] = operation
    contract["components"] = {
        "parameters": {"Thing": operation["parameters"][0]},
        "responses": {"Ok": operation["responses"]["200"]},
        "requestBodies": {"Input": {"content": {"application/json": {"example": {"input": "fixture"}}}}},
    }
    operation["parameters"] = [{"$ref": "#/components/parameters/Thing"}]
    operation["responses"]["200"] = {"$ref": "#/components/responses/Ok"}
    operation["requestBody"] = {"$ref": "#/components/requestBodies/Input"}
    (workspace.parents[1] / "apis" / "things" / "openapi.yaml").write_text(
        yaml.safe_dump(contract), encoding="utf-8")
    cases = list(vr.iter_cases(MANIFEST))
    remaining = iter(cases)

    def respond(request):
        _, _, _, method, case = next(remaining)
        path, _, body, status, media, expected = case
        assert request.full_url == ORIGIN + "/demo/agent" + path
        assert request.method == method.upper() == "POST"
        assert json.loads(request.data) == body == {"input": "fixture"}
        assert request.get_header("Ocp-apim-subscription-key") == KEY
        return status, {"Content-Type": media}, json.dumps(expected).encode()

    calls = transport(monkeypatch, respond)
    cli(monkeypatch, vr, *explicit_flags(vr))
    assert len(calls) == 2
    assert KEY not in capsys.readouterr().out


@pytest.mark.parametrize("field,value", [("status", 201), ("media", "text/plain"), ("payload", {"wrong": True})])
def test_rest_comparison_still_fails(workspace, monkeypatch, field, value):
    case = list(vr.iter_cases(MANIFEST))[0][-1]
    response = {"status": case[3], "media": case[4], "payload": case[5]}
    response[field] = value
    transport(monkeypatch, lambda req: (response["status"], {"Content-Type": response["media"]},
                                       json.dumps(response["payload"]).encode()))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vr, *explicit_flags(vr))


@pytest.mark.parametrize("module", [vm, vr])
def test_legacy_cli_still_discovers_from_azd(workspace, monkeypatch, module):
    calls = []
    monkeypatch.setattr(module, "azd_env", lambda: calls.append("azd") or {
        "APIM_GATEWAY_URL": ORIGIN, "GATEWAY_PROFILE": "native-mcp"})
    monkeypatch.setattr(module, "get_pilot_key" if module is vm else "pilot_key",
                        lambda *a: calls.append("key") or KEY)
    if module is vm:
        transport(monkeypatch, rpc_response)
    else:
        cases = iter(vr.iter_cases(MANIFEST))

        def respond(req):
            case = next(cases)[-1]
            return case[3], {"Content-Type": case[4]}, json.dumps(case[5]).encode()
        transport(monkeypatch, respond)
    cli(monkeypatch, module)
    assert calls == ["azd", "key"]


def test_policy_explicit_checks_tool_call_examples(workspace, monkeypatch):
    from mcp_policy import build_client_plan, write_client_plan, load_client, sample_tool_calls
    plan = build_client_plan(workspace.parents[1], workspace)
    write_client_plan(workspace, plan)
    _, definitions = load_client(workspace.parents[1], workspace)
    examples = iter(sample_tool_calls(definitions["things"][0]))

    def respond(request):
        rpc = json.loads(request.data)
        if rpc["method"] != "tools/call":
            return rpc_response(request)
        arguments, expected, is_error = next(examples)
        assert rpc["params"]["arguments"] == arguments
        return 200, {}, json.dumps({"jsonrpc": "2.0", "id": rpc["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(expected)}],
            "isError": is_error}}).encode()

    calls = transport(monkeypatch, respond)
    cli(monkeypatch, vm, "--gateway-url", ORIGIN, "--profile", "policy-mcp-consumption")
    assert len(calls) == 5
    assert all("/demo/agent-policy-mcp/mcp" in req.full_url for req in calls)


@pytest.mark.parametrize("backend,network", [("external", "public"), ("mock", "isolated"), ("hosted", "public")])
def test_policy_rejects_unsupported_manifests_before_network(workspace, monkeypatch, backend, network):
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["backend"]["mode"] = backend
    manifest["networkProfile"] = network
    (workspace / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, "--gateway-url", ORIGIN, "--profile", "policy-mcp-consumption")


def test_policy_stale_index_cannot_report_success(workspace, monkeypatch, capsys):
    from mcp_policy import build_client_plan, write_client_plan
    plan = build_client_plan(workspace.parents[1], workspace)
    plan["servers"] = []
    write_client_plan(workspace, plan)
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, "--gateway-url", ORIGIN, "--profile", "policy-mcp-consumption")
    assert "rebuild" in capsys.readouterr().err


@pytest.mark.parametrize("path", [
    "//other.example.com", "https://other.example.com/mcp", "../other/mcp",
    "demo/%2e%2e/other/mcp", "demo\\other/mcp", "demo/agent/mcp#secret",
])
def test_unsafe_derived_endpoint_paths(path):
    with pytest.raises(ValueError):
        verification.endpoint_url(ORIGIN, path)


def test_legacy_env_credentials_remain_compatible(monkeypatch):
    monkeypatch.setenv("MCP_KEY", "legacy-mcp-key")
    monkeypatch.setenv("REST_KEY", "legacy-rest-key")
    monkeypatch.setattr(vm, "run", lambda *a, **k: pytest.fail("management called"))
    monkeypatch.setattr(vr, "run", lambda *a, **k: pytest.fail("management called"))
    assert vm.get_pilot_key("demo", {}) == "legacy-mcp-key"
    assert vr.pilot_key("demo", {}) == "legacy-rest-key"
