"""Opt-in native calls, manifest-bound private auth, and reviewed external fixtures."""
import copy
import json
import os
import subprocess
import sys

import pytest
import yaml

from test_verification import (
    CONTRACT, KEY, MANIFEST, ORIGIN, cli, explicit_flags, rpc_response, transport,
    vm, vr, workspace,
)
import verification as shared

BEARER = "private.bearer.token-never-log"
SELECTOR = "things/get-thing/200/approved"
EXPECTED = CONTRACT["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
INPUT_SCHEMA = {"type": "object", "properties": {"thingId": {"type": "string"}},
                "required": ["thingId"], "additionalProperties": False}


def save_contract(workspace, contract):
    (workspace.parents[1] / "apis" / "things" / "openapi.yaml").write_text(
        yaml.safe_dump(contract), encoding="utf-8")


def save_manifest(workspace, manifest):
    (workspace / "mcp-manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


@pytest.fixture
def external(workspace):
    manifest, contract = copy.deepcopy(MANIFEST), copy.deepcopy(CONTRACT)
    manifest["apis"][0]["backend"] = {"mode": "external", "url": "https://never-contact-backend.invalid"}
    operation = contract["paths"]["/v1/things/{thingId}"]["get"]
    operation["parameters"][0]["examples"] = {"approved": {"value": "T-1"}}
    operation["responses"]["200"]["content"]["application/json"]["examples"] = {
        "approved": {"value": EXPECTED}}
    save_manifest(workspace, manifest)
    save_contract(workspace, contract)
    return manifest, contract


def set_entra(workspace):
    manifest = yaml.safe_load((workspace / "mcp-manifest.yaml").read_text(encoding="utf-8"))
    manifest["inboundAuth"] = {"mode": "entraJwt", "entraJwt": {
        "tenantId": "11111111-1111-4111-8111-111111111111", "audience": "api://fixture"}}
    save_manifest(workspace, manifest)


def native_response(request, *, result=None, descriptor=None, sse=False):
    rpc = json.loads(request.data)
    method = rpc["method"]
    headers = {"Content-Type": "application/json"}
    if method == "initialize":
        value = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                 "serverInfo": {"name": "offline-fixture", "version": "1"}}
        headers["Mcp-Session-Id"] = "fixture-session"
    elif method == "notifications/initialized":
        return 202, {}, b""
    elif method == "tools/list":
        value = {"tools": [descriptor or {"name": "get-thing", "inputSchema": INPUT_SCHEMA}]}
    else:
        assert method == "tools/call"
        value = result if result is not None else {"content": [], "structuredContent": EXPECTED}
    envelope = {"jsonrpc": "2.0", "id": rpc["id"], "result": value}
    body = json.dumps(envelope)
    if sse:
        notice = json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": BEARER}})
        body = ("event: message\ndata: " + notice + "\n\n: keep-alive\n"
                + "\n".join("data: " + line for line in json.dumps(envelope, indent=2).splitlines())
                + "\n\n" + "data: " + notice + "\n\n")
        headers["Content-Type"] = "text/event-stream"
    return 200, headers, body.encode()


def preview(monkeypatch, capsys, module, *extra):
    cli(monkeypatch, module, *explicit_flags(module), "--fixture", SELECTOR, *extra)
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "fixture-preview"
    assert plan["networkCalls"] == 0
    return plan["reviewToken"]


def business_calls(calls):
    return [request for request in calls if json.loads(request.data)["method"] == "tools/call"]


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("mode", ["entraJwt", "dual"])
def test_explicit_entra_sends_both_private_headers(workspace, monkeypatch, capsys, module, mode):
    set_entra(workspace)
    monkeypatch.setenv("MCP_BEARER_TOKEN", BEARER)
    if module is vm:
        responder = rpc_response
    else:
        cases = iter(vr.iter_cases(yaml.safe_load((workspace / "mcp-manifest.yaml").read_text()),
                                   requested_auth=mode))

        def responder(request):
            case = next(cases)[-1]
            return case[3], {"Content-Type": case[4]}, json.dumps(case[5]).encode()
    calls = transport(monkeypatch, responder)
    cli(monkeypatch, module, *explicit_flags(module), "--auth-mode", mode)
    assert calls
    assert all(request.get_header("Authorization") == f"Bearer {BEARER}" for request in calls)
    assert all(request.get_header("Ocp-apim-subscription-key") == KEY for request in calls)
    captured = capsys.readouterr()
    assert BEARER not in captured.out + captured.err and KEY not in captured.out + captured.err


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("token", [None, "", "Bearer token", "token\ninjection", " spaced", "\u00e9"])
def test_missing_or_invalid_bearer_prevents_network(workspace, monkeypatch, capsys, module, token):
    set_entra(workspace)
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    if token is not None:
        monkeypatch.setenv("MCP_BEARER_TOKEN", token)
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module), "--auth-mode", "entraJwt")
    assert not calls
    assert "MCP_BEARER_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("requested", ["entraJwt", "dual"])
def test_auth_mode_cannot_upgrade_subscription_manifest(workspace, monkeypatch, module, requested):
    monkeypatch.setenv("MCP_BEARER_TOKEN", BEARER)
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module), "--auth-mode", requested)
    assert not calls


@pytest.mark.parametrize("module", [vm, vr])
def test_entra_requires_key_and_cannot_downgrade(workspace, monkeypatch, module):
    set_entra(workspace)
    monkeypatch.setenv("MCP_BEARER_TOKEN", BEARER)
    monkeypatch.delenv("MCP_KEY")
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    for mode in ("entraJwt", "subscriptionKey"):
        with pytest.raises(SystemExit):
            cli(monkeypatch, module, *explicit_flags(module), "--auth-mode", mode)
    assert not calls


def test_credentials_repr_is_private(monkeypatch):
    monkeypatch.setenv("MCP_KEY", KEY)
    monkeypatch.setenv("MCP_BEARER_TOKEN", BEARER)
    credentials = shared.Credentials(KEY, BEARER)
    assert KEY not in repr(credentials) and BEARER not in repr(credentials)
    assert credentials.redact(KEY + BEARER) == "[REDACTED][REDACTED]"


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("flags", [
    ["--auth-mode", "dual"], ["--fixture", SELECTOR], ["--confirm-fixtures", "declined"],
])
def test_new_flags_never_invoke_legacy_management(workspace, monkeypatch, module, flags):
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *flags)


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("confirmation", [None, "declined", "0" * 64])
def test_external_default_preview_or_decline_has_zero_network(external, workspace, monkeypatch, capsys, module, confirmation):
    monkeypatch.delenv("MCP_KEY")
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    if confirmation is None:
        preview(monkeypatch, capsys, module)
    else:
        with pytest.raises(SystemExit):
            cli(monkeypatch, module, *explicit_flags(module), "--fixture", SELECTOR,
                "--confirm-fixtures", confirmation)
    assert not calls


@pytest.mark.parametrize("module", [vm, vr])
def test_confirmed_external_missing_private_env_has_zero_network(external, workspace, monkeypatch, capsys, module):
    token = preview(monkeypatch, capsys, module)
    monkeypatch.delenv("MCP_KEY")
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module), "--fixture", SELECTOR, "--confirm-fixtures", token)
    assert not calls and "MCP_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("change", ["gateway", "contract", "manifest", "auth"])
def test_fixture_token_binds_exact_scope(external, workspace, monkeypatch, capsys, module, change):
    token = preview(monkeypatch, capsys, module)
    manifest, contract = external
    flags = explicit_flags(module)
    if change == "gateway":
        flags[1] = "https://different-approved.example.com"
    elif change == "contract":
        contract["paths"]["/v1/things/{thingId}"]["get"]["parameters"][0]["examples"]["approved"]["value"] = "T-2"
        save_contract(workspace, contract)
    elif change == "manifest":
        manifest["displayName"] = "changed"
        save_manifest(workspace, manifest)
    else:
        set_entra(workspace)
        flags += ["--auth-mode", "dual"]
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *flags, "--fixture", SELECTOR, "--confirm-fixtures", token)
    assert not calls and "review token" in capsys.readouterr().err


@pytest.mark.parametrize("module", [vm, vr])
def test_external_allowlist_calls_exact_fixture_once_even_both_routes(external, workspace, monkeypatch, capsys, module):
    manifest, _ = external
    manifest["mcpExposure"]["mode"] = "both"
    save_manifest(workspace, manifest)
    token = preview(monkeypatch, capsys, module)
    responder = native_response if module is vm else lambda request: (
        200, {"Content-Type": "application/json"}, json.dumps(EXPECTED).encode())
    calls = transport(monkeypatch, responder)
    cli(monkeypatch, module, *explicit_flags(module), "--fixture", SELECTOR, "--confirm-fixtures", token)
    if module is vm:
        invoked = business_calls(calls)
        assert len(invoked) == 1
        assert json.loads(invoked[0].data)["params"] == {"name": "get-thing", "arguments": {"thingId": "T-1"}}
    else:
        assert len(calls) == 1 and calls[0].method == "GET"
        assert calls[0].full_url == ORIGIN + "/demo/agent/v1/things/T-1"
    assert all(request.full_url.startswith(ORIGIN + "/demo/agent") for request in calls)
    assert "never-contact-backend" not in capsys.readouterr().out


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("damage", ["missing_parameter", "missing_response", "external_example", "bad_schema", "unselected", "duplicate"])
def test_incomplete_or_unselected_fixtures_fail_without_calls(external, workspace, monkeypatch, module, damage):
    _, contract = external
    operation = contract["paths"]["/v1/things/{thingId}"]["get"]
    selector = SELECTOR
    extra = []
    if damage == "missing_parameter":
        operation["parameters"][0].pop("examples")
        operation["parameters"][0]["example"] = "unnamed-is-not-authorized"
    elif damage == "missing_response":
        operation["responses"]["200"]["content"]["application/json"].pop("examples")
    elif damage == "external_example":
        operation["parameters"][0]["examples"]["approved"] = {"externalValue": "https://never-read.invalid/"}
    elif damage == "bad_schema":
        operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["size"]["type"] = "boolean"
    elif damage == "unselected":
        selector = "things/delete-everything/200/approved"
    else:
        extra = ["--fixture", selector]
    save_contract(workspace, contract)
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module), "--fixture", selector, *extra)
    assert not calls


@pytest.mark.parametrize("result", [
    {"content": [], "structuredContent": EXPECTED},
    {"content": [{"type": "text", "text": json.dumps(EXPECTED)}]},
    {"content": [{"type": "text", "text": "human-readable summary"}], "structuredContent": EXPECTED},
    {"content": [{"type": "text", "text": json.dumps(EXPECTED)}], "structuredContent": EXPECTED, "isError": False},
])
@pytest.mark.parametrize("sse", [False, True])
def test_native_opt_in_genuine_result_shapes_and_session(workspace, monkeypatch, capsys, result, sse):
    calls = transport(monkeypatch, lambda req: native_response(req, result=result, sse=sse))
    cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert len(business_calls(calls)) == 1
    assert json.loads(business_calls(calls)[0].data)["params"]["arguments"] == {"thingId": "T-test"}
    assert all(request.get_header("Mcp-session-id") == "fixture-session" for request in calls[1:])
    assert all(request.get_header("Mcp-protocol-version") == "2025-06-18" for request in calls[1:])
    output = capsys.readouterr().out
    assert "1 successful native example" in output and "non-2xx branches are not asserted" in output
    assert BEARER not in output


@pytest.mark.parametrize("result", [
    {"content": [{"type": "text", "text": BEARER}], "isError": True},
    {"content": [], "structuredContent": EXPECTED, "isError": "false"},
    {"content": [], "structuredContent": [EXPECTED]},
    {"content": [{"type": "text", "text": BEARER}]},
    {"content": [{"type": "text", "text": "{}"}, {"type": "text", "text": "{}"}]},
    {"content": [{"type": "text", "text": "{}"}], "structuredContent": EXPECTED},
    {"content": [{"type": "image", "data": BEARER, "mimeType": "image/png"}]},
    {"content": [], "structuredContent": {"statusCode": 200, "body": EXPECTED}},
    {"content": [], "structuredContent": {**EXPECTED, "size": True}},
])
def test_unknown_native_formats_and_schema_errors_are_redacted(workspace, monkeypatch, capsys, result):
    calls = transport(monkeypatch, lambda req: native_response(req, result=result))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert len(business_calls(calls)) == 1
    captured = capsys.readouterr()
    assert BEARER not in captured.out + captured.err and KEY not in captured.out + captured.err
    assert "RESULT:" not in captured.out


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"thingId": {"type": "integer"}}, "required": ["thingId"]},
    {"type": "object", "properties": {"other": {"type": "string"}}},
    {"type": "object", "$ref": "https://never-fetch-schema.invalid/schema"},
])
def test_native_input_schema_checked_before_business_calls(workspace, monkeypatch, schema):
    calls = transport(monkeypatch, lambda req: native_response(req, descriptor={
        "name": "get-thing", "inputSchema": schema}))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert len(calls) == 3 and not business_calls(calls)


@pytest.mark.parametrize("output_schema", [
    {"type": "object", "required": ["missing"]},
    {"type": "object", "$ref": "https://never-fetch-schema.invalid/schema"},
])
def test_native_advertised_output_schema_enforced(workspace, monkeypatch, output_schema):
    calls = transport(monkeypatch, lambda req: native_response(req, descriptor={
        "name": "get-thing", "inputSchema": INPUT_SCHEMA, "outputSchema": output_schema}))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert not business_calls(calls)


def test_native_external_cannot_use_mock_opt_in(external, workspace, monkeypatch):
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert not calls


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("header", ["Authorization", "authorization", "OCP-APIM-SUBSCRIPTION-KEY", "Host", "Cookie"])
def test_contract_cannot_override_private_headers(external, workspace, monkeypatch, module, header):
    _, contract = external
    contract["paths"]["/v1/things/{thingId}"]["get"]["parameters"].append({
        "in": "header", "name": header, "schema": {"type": "string"},
        "examples": {"approved": {"value": BEARER}}})
    save_contract(workspace, contract)
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module), "--fixture", SELECTOR)
    assert not calls


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("status", [401, 403, 307])
def test_confirmed_external_failures_do_not_retry_or_echo(external, workspace, monkeypatch, capsys, module, status):
    set_entra(workspace)
    monkeypatch.setenv("MCP_BEARER_TOKEN", BEARER)
    token = preview(monkeypatch, capsys, module, "--auth-mode", "dual")
    calls = transport(monkeypatch, lambda req: (
        status, {"Content-Type": "application/json", "Location": f"https://forbidden.invalid/{BEARER}"},
        json.dumps({"message": BEARER, "key": KEY}).encode()))
    with pytest.raises(SystemExit):
        cli(monkeypatch, module, *explicit_flags(module), "--auth-mode", "dual",
            "--fixture", SELECTOR, "--confirm-fixtures", token)
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert BEARER not in captured.out + captured.err and KEY not in captured.out + captured.err
    assert "forbidden.invalid" not in captured.out + captured.err


@pytest.mark.parametrize("layout", ["flat", "body", "requestBody"])
def test_authorized_native_write_maps_only_declared_body_layout(external, workspace, monkeypatch, capsys, layout):
    _, contract = external
    item = contract["paths"]["/v1/things/{thingId}"]
    operation = item["post"] = item.pop("get")
    body_schema = {"type": "object", "properties": {"amount": {"type": "integer"}},
                   "required": ["amount"], "additionalProperties": False}
    operation["requestBody"] = {"required": True, "content": {"application/json": {
        "schema": body_schema, "examples": {"approved": {"value": {"amount": 7}}}}}}
    save_contract(workspace, contract)
    token = preview(monkeypatch, capsys, vm)
    schema = copy.deepcopy(INPUT_SCHEMA)
    if layout == "flat":
        schema["properties"]["amount"] = {"type": "integer"}
        schema["required"].append("amount")
        expected_args = {"thingId": "T-1", "amount": 7}
    else:
        schema["properties"][layout] = body_schema
        schema["required"].append(layout)
        expected_args = {"thingId": "T-1", layout: {"amount": 7}}
    calls = transport(monkeypatch, lambda req: native_response(req, descriptor={
        "name": "get-thing", "inputSchema": schema}))
    cli(monkeypatch, vm, *explicit_flags(vm), "--fixture", SELECTOR, "--confirm-fixtures", token)
    assert len(business_calls(calls)) == 1
    assert json.loads(business_calls(calls)[0].data)["params"]["arguments"] == expected_args


def test_schema_normalization_preserves_property_names_and_json_types():
    schema = {"type": "object", "required": ["example", "nullable"], "properties": {
        "example": {"type": "integer"}, "nullable": {"type": "string", "nullable": True}}}
    normalized = shared.contract_schema({}, schema)
    assert set(normalized["properties"]) == {"example", "nullable"}
    shared.validate_value({"example": 1, "nullable": None}, normalized)
    with pytest.raises(shared.VerificationFailure):
        shared.validate_value({"example": True, "nullable": None}, normalized)
    assert not shared.exact_json({"a": True}, {"a": 1})


def test_rpc_correlates_exactly_one_response_and_hides_protocol_error():
    for envelope in [
        {"jsonrpc": "2.0", "id": 2, "result": {}},
        {"jsonrpc": "2.0", "id": True, "result": {}},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": BEARER}},
    ]:
        with pytest.raises(shared.VerificationFailure) as error:
            shared.rpc_message(json.dumps(envelope), 1)
        assert BEARER not in str(error.value)
    envelope = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    with pytest.raises(shared.VerificationFailure):
        shared.rpc_message(f"data: {envelope}\n\ndata: {envelope}\n\n", 1)


@pytest.mark.parametrize("module", [vm, vr])
@pytest.mark.parametrize("flag", ["--bearer-token", "--token", "--key", "--auth-mode"])
def test_credentials_cannot_be_passed_or_echoed_as_cli_arguments(workspace, monkeypatch, capsys, module, flag):
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, module, *explicit_flags(module), flag, BEARER)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert BEARER not in captured.out + captured.err


@pytest.mark.parametrize("module", [vm, vr])
def test_private_entra_headers_not_sent_in_default_subscription_mode(workspace, monkeypatch, module):
    monkeypatch.setenv("MCP_BEARER_TOKEN", BEARER)
    if module is vm:
        responder = rpc_response
    else:
        cases = iter(vr.iter_cases(MANIFEST))

        def responder(request):
            case = next(cases)[-1]
            return case[3], {"Content-Type": case[4]}, json.dumps(case[5]).encode()
    calls = transport(monkeypatch, responder)
    cli(monkeypatch, module, *explicit_flags(module))
    assert all(request.get_header("Authorization") is None for request in calls)


def test_native_advertised_output_requires_structured_content(workspace, monkeypatch):
    descriptor = {"name": "get-thing", "inputSchema": INPUT_SCHEMA, "outputSchema": {"type": "object"}}
    calls = transport(monkeypatch, lambda req: native_response(req, descriptor=descriptor, result={
        "content": [{"type": "text", "text": json.dumps(EXPECTED)}]}))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert len(business_calls(calls)) == 1


def test_native_binding_ambiguity_is_not_guessed():
    case = shared.Case("things", "write-thing", "fixture", "post", "/things", (),
                       {}, True, 200, "application/json", {}, {})
    descriptor = {"inputSchema": {"type": "object", "properties": {
        "body": {"type": "object"}, "requestBody": {"type": "object"}}}}
    with pytest.raises(shared.VerificationFailure, match="ambiguous"):
        shared.native_arguments(case, descriptor)


def test_controlled_native_never_drops_required_idempotency_key():
    case = shared.Case("things", "write-thing", "fixture", "post", "/things",
                       (("header", "Idempotency-Key", "approved-id"),), None, False,
                       200, "application/json", {}, {})
    with pytest.raises(shared.VerificationFailure, match="no binding"):
        shared.native_arguments(case, {"inputSchema": {"type": "object", "properties": {}}})


@pytest.mark.parametrize("url", ["//foreign.invalid", "/../other", "/%2e%2e/other"])
def test_fixture_unsafe_contract_paths_are_rejected_before_network(external, workspace, monkeypatch, url):
    _, contract = external
    contract["paths"][url + "/{thingId}"] = contract["paths"].pop("/v1/things/{thingId}")
    save_contract(workspace, contract)
    calls = transport(monkeypatch, lambda req: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vr, *explicit_flags(vr), "--fixture", SELECTOR)
    assert not calls


@pytest.mark.parametrize("change", ["id", "session", "protocol", "capability"])
def test_native_session_and_protocol_errors_block_calls(workspace, monkeypatch, change):
    def respond(request):
        code, headers, body = native_response(request)
        if json.loads(request.data)["method"] == "initialize":
            envelope = json.loads(body)
            if change == "id":
                envelope["id"] = 99
            elif change == "session":
                headers["Mcp-Session-Id"] = "invalid\nsession"
            elif change == "protocol":
                envelope["result"]["protocolVersion"] = "unsupported"
            else:
                envelope["result"]["capabilities"] = {}
            body = json.dumps(envelope).encode()
        return code, headers, body
    calls = transport(monkeypatch, respond)
    with pytest.raises(SystemExit):
        cli(monkeypatch, vm, *explicit_flags(vm), "--exercise-mock")
    assert len(calls) == 1 and not business_calls(calls)


def test_confirmed_rest_write_has_exact_named_body_and_idempotency(external, workspace, monkeypatch, capsys):
    _, contract = external
    item = contract["paths"]["/v1/things/{thingId}"]
    operation = item["post"] = item.pop("get")
    operation["parameters"].append({"in": "header", "name": "Idempotency-Key", "required": True,
                                    "schema": {"type": "string"},
                                    "examples": {"approved": {"value": "approved-fixture-id"}}})
    operation["requestBody"] = {"required": True, "content": {"application/json": {
        "schema": {"type": "object", "required": ["amount"], "properties": {"amount": {"type": "integer"}}},
        "examples": {"approved": {"value": {"amount": 7}}}}}}
    save_contract(workspace, contract)
    token = preview(monkeypatch, capsys, vr)
    calls = transport(monkeypatch, lambda req: (
        200, {"Content-Type": "application/json"}, json.dumps(EXPECTED).encode()))
    cli(monkeypatch, vr, *explicit_flags(vr), "--fixture", SELECTOR, "--confirm-fixtures", token)
    assert len(calls) == 1 and calls[0].method == "POST"
    assert calls[0].get_header("Idempotency-key") == "approved-fixture-id"
    assert json.loads(calls[0].data) == {"amount": 7}


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_ambiguous_nonfinite_json_rejected(text):
    with pytest.raises(shared.VerificationFailure):
        shared.decode_json(text)


def test_installed_dispatch_accepts_fixture_preview_without_credentials(external, workspace):
    env = os.environ.copy()
    for name in ("MCP_KEY", "MCP_BEARER_TOKEN", "REST_KEY"):
        env.pop(name, None)
    for command in ("verify-rest", "verify-mcp"):
        flags = ["--profile", "native-mcp"] if command == "verify-mcp" else []
        result = subprocess.run([
            sys.executable, "-I", "-X", "utf8", "-c",
            "from mcp_openapi_creator_kit.cli import main; raise SystemExit(main())",
            "--workspace", str(workspace.parents[1]), command, "clients/demo",
            "--gateway-url", ORIGIN, *flags, "--fixture", SELECTOR,
        ], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        assert result.returncode == 0, result.stderr
        plan = json.loads(result.stdout)
        assert plan["networkCalls"] == 0 and plan["mode"] == "fixture-preview"


@pytest.mark.parametrize("body", [BEARER.encode(), b"\xffprivate"])
def test_non_json_http_error_reports_only_status(external, workspace, monkeypatch, capsys, body):
    token = preview(monkeypatch, capsys, vr)
    calls = transport(monkeypatch, lambda req: (403, {"Content-Type": "text/plain"}, body))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vr, *explicit_flags(vr), "--fixture", SELECTOR, "--confirm-fixtures", token)
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert "HTTP 403" in captured.err and BEARER not in captured.err


def test_controlled_allowlist_stops_after_first_failed_call(external, workspace, monkeypatch, capsys):
    _, contract = external
    operation = contract["paths"]["/v1/things/{thingId}"]["get"]
    operation["parameters"][0]["examples"]["second"] = {"value": "T-2"}
    operation["responses"]["200"]["content"]["application/json"]["examples"]["second"] = {"value": EXPECTED}
    save_contract(workspace, contract)
    extra = ["--fixture", "things/get-thing/200/second"]
    token = preview(monkeypatch, capsys, vr, *extra)
    calls = transport(monkeypatch, lambda req: (
        500, {"Content-Type": "application/json"}, json.dumps({"message": BEARER}).encode()))
    with pytest.raises(SystemExit):
        cli(monkeypatch, vr, *explicit_flags(vr), "--fixture", SELECTOR, *extra, "--confirm-fixtures", token)
    assert len(calls) == 1
