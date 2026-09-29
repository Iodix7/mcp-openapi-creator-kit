"""Compile real generated C# expressions; this is not an APIM gateway emulator."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import pytest

from test_rest_runtime_v2 import CORRELATION, agreement_v2, compile_v2
from mcp_openapi_creator_kit.rest_runtime_v2 import build_operation_policies
from test_rest_runtime import bf


def policies_and_cases():
    spec, manifest = agreement_v2()
    policies = {"multi": compile_v2(spec, manifest)}
    limited_manifest = copy.deepcopy(manifest)
    limited_manifest["apis"][0]["runtime"]["operations"] = {
        "post-thing": {"rateLimit": {"calls": 3, "renewalPeriod": 10}}}
    policies["limited"] = compile_v2(spec, limited_manifest)
    operation_policies = {"limited": build_operation_policies(
        spec, limited_manifest["apis"][0], limited_manifest, bf)}
    spec["paths"].pop("/v1/things/{thingId}")
    manifest["apis"][0]["mcpTools"] = ["get-thing-status", "post-thing"]
    op = spec["paths"]["/v1/things"]["post"]
    spec["x-kit-runtime"]["operations"] = {
        "post-thing": {"correlation": {"header": "X-Trace-Id", "bodyProperty": "traceId"},
                       "errors": copy.deepcopy(spec["x-kit-runtime"]["errors"])}}
    spec["x-kit-runtime"]["operations"]["post-thing"]["errors"]["badRequest"]["example"] = "post-invalid"
    next(p for p in op["parameters"] if p["name"] == "X-Correlation-Id")["name"] = "X-Trace-Id"
    for response in op["responses"].values():
        response["headers"]["X-Trace-Id"] = response["headers"].pop("X-Correlation-Id")
        media = next(iter(response["content"].values()))
        schema = media["schema"]
        schema["properties"]["traceId"] = schema["properties"].pop("correlationId")
        schema["required"][schema["required"].index("correlationId")] = "traceId"
        examples = [media["example"]] if "example" in media else [e["value"] for e in media["examples"].values()]
        for example in examples:
            example["traceId"] = example.pop("correlationId")
    media = op["responses"]["400"]["content"]["application/problem+json"]
    example = media.pop("example")
    media["examples"] = {"default": {"value": example},
                         "post-invalid": {"value": {**example, "code": "POST_INVALID"}}}
    for status, response in op["responses"].items():
        response["headers"]["X-Source-Time"] = {"schema": {"type": "string"},
            "example": "2025-01-02T12:34:56+02:00" if status == "200" else "2025-02-03T23:45:01-03:30"}
    header_spec, header_manifest = copy.deepcopy(spec), copy.deepcopy(manifest)
    header_spec["paths"] = {"/v1/things": header_spec["paths"]["/v1/things"]}
    header_manifest["apis"][0]["mcpTools"] = ["post-thing"]
    policies["headers"] = compile_v2(header_spec, header_manifest)
    for response in op["responses"].values():
        response["headers"].pop("X-Source-Time")
    op["x-mock"].insert(0, {"when": {"all": [
        {"body": "/urgent", "equals": True},
        {"any": [{"body": "/lines/0/quantity", "equals": 3},
                 {"body": "/customer/id", "equals": "C-2", "caseSensitive": True}]}]},
        "respond": {"status": 200, "example": "T-2"}})
    policies["rules"] = compile_v2(spec, manifest)
    manifest["apis"][0]["runtime"]["operations"] = {"post-thing": {"simulate": {"status": 503}}}
    policies["simulation"] = compile_v2(spec, manifest)
    manifest["apis"][0]["runtime"].pop("operations")
    spec["paths"] = {"/v1/things": spec["paths"]["/v1/things"]}
    manifest["apis"][0]["mcpTools"] = ["post-thing"]
    op["requestBody"]["required"] = False
    op["x-mock"].insert(0, {"when": {"body": "/customer/id", "missing": True},
                          "respond": {"status": 200, "example": "T-3"}})
    policies["optional"] = compile_v2(spec, manifest)
    sample_body = op["requestBody"]["content"]["application/json"]["example"]
    cases = []

    def add(name, operation="get-thing-status", policy="multi", **changes):
        case = {"id": name, "policy": policy, "operationId": operation, "subscriptionId": "demo-pilot",
                "headers": {"x-api-key": ["synthetic-key"], "X-Correlation-Id": [CORRELATION],
                            "Idempotency-Key": [CORRELATION], "Content-Type": ["application/json"]},
                "parameters": {"thingId": "T-1"}, "body": json.dumps(sample_body)}
        if policy not in {"multi", "limited"}:
            case["headers"]["X-Trace-Id"] = case["headers"].pop("X-Correlation-Id")
        case.update(changes)
        cases.append(case)
        return case

    for method in ("get-thing-status", "post-thing", "put-thing", "patch-thing", "delete-thing"):
        add(method, method)
    add("limited-post", "post-thing", "limited")
    add("limited-get", policy="limited")
    add("limited-api-rate", "post-thing", "limited", rateError=True)
    add("limited-operation-rate", "post-thing", "limited", rateError=True, rateErrorScope="operation")
    add("limited-invalid", "post-thing", "limited")["headers"].pop("Idempotency-Key")
    add("limited-body-error", "post-thing", "limited", bodyError=True)
    add("case-sensitive", parameters={"thingId": "t-1"})
    add("bad-id", parameters={"thingId": "a" * 65})
    add("missing-key", subscriptionId=None)["headers"].pop("x-api-key")
    add("duplicate-key")["headers"]["x-api-key"] *= 2
    add("forbidden", subscriptionId="unapproved")
    add("missing-correlation")["headers"].pop("X-Correlation-Id")
    add("invalid-correlation")["headers"]["X-Correlation-Id"] = ["not-a-uuid"]
    add("duplicate-correlation")["headers"]["X-Correlation-Id"] *= 2
    add("absent-body", "post-thing", body=None)
    add("empty-body", "post-thing", body="")
    add("wrong-content-type", "post-thing")["headers"]["Content-Type"] = ["text/plain"]
    add("body-validator-error", "post-thing", bodyError=True)
    add("rate-limit", rateError=True)
    add("override-rate-limit", "post-thing", "rules", rateError=True)
    add("override-body-error", "post-thing", "rules", bodyError=True)
    for reason in ("SubscriptionKeyNotFound", "SubscriptionKeyInvalid"):
        add(reason, "post-thing", "rules",
            initialError={"Source": "authorization", "Reason": reason, "PolicyId": None})
    add("unknown-error", initialError={"Source": "unrelated", "Reason": "Unexpected", "PolicyId": "inherited"})
    add("unrouted", operation=None, initialError={"Source": "authorization", "Reason": "SubscriptionKeyInvalid"})
    add("unknown-operation", operation="unknown")
    body = copy.deepcopy(sample_body)
    body["urgent"], body["lines"][0]["quantity"] = True, 3
    add("composite-match", "post-thing", "rules", body=json.dumps(body))
    body["lines"][0]["quantity"], body["customer"]["id"] = 2, "C-2"
    add("alternative-match", "post-thing", "rules", body=json.dumps(body))
    body["customer"]["id"] = "c-2"
    add("composite-fallback", "post-thing", "rules", body=json.dumps(body))
    add("operation-simulation", "post-thing", "simulation")
    add("simulation-get-unchanged", policy="simulation")["headers"]["X-Correlation-Id"] = [CORRELATION]
    add("optional-missing-body", "post-thing", "optional", body=None)
    add("optional-empty-body", "post-thing", "optional", body="")
    add("header-preservation", "post-thing", "headers")
    add("error-header-preservation", "post-thing", "headers", bodyError=True)
    return policies, operation_policies, cases


def test_generated_csharp_execution(tmp_path):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("Optional generated-expression acceptance requires an existing .NET SDK")
    listing = subprocess.run([dotnet, "--list-sdks"], capture_output=True, text=True, check=True).stdout
    candidates = [(int(v.split(".")[0]), Path(p) / v / "Newtonsoft.Json.dll")
                  for v, p in re.findall(r"^(\S+) \[([^\]]+)\]", listing, re.MULTILINE)]
    candidates = [(major, dll) for major, dll in candidates if major >= 8 and dll.is_file()]
    if not candidates:
        pytest.skip("Existing .NET 8+ SDK with bundled JSON.NET required; no packages are downloaded")
    major, dll = max(candidates, key=lambda value: value[0])
    policies, operation_policies, cases = policies_and_cases()
    expressions = set()
    all_policies = [*policies.values(), *(text for group in operation_policies.values() for text in group.values())]
    for policy in all_policies:
        for node in ET.fromstring(policy).iter():
            for value in [*node.attrib.values(), node.text or ""]:
                if value.startswith(("@(", "@{")):
                    expressions.add(value)
    methods, entries = [], []
    for index, expression in enumerate(sorted(expressions)):
        body = ("return " + expression[2:-1] + ";" if expression.startswith("@(") else expression[2:-1])
        methods.append(f"static object E{index}(Context context) {{{body}}}")
        key = base64.b64encode(expression.encode()).decode()
        entries.append(f'[System.Text.Encoding.UTF8.GetString(Convert.FromBase64String("{key}"))] = E{index}')
    declarations = "\n".join(methods) + (
        "\nstatic Dictionary<string, Func<Context, object>> Expressions = new Dictionary<string, Func<Context, object>> {"
        + ",\n".join(entries) + "};")
    source = Path(__file__).with_name("runtime_v2_probe.cs").read_text("utf-8").replace("/* EXPRESSIONS */", declarations)
    (tmp_path / "Program.cs").write_text(source, "utf-8")
    (tmp_path / "Probe.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        f'<TargetFramework>net{major}.0</TargetFramework><NuGetAudit>false</NuGetAudit>'
        '</PropertyGroup><ItemGroup><Reference Include="Newtonsoft.Json">'
        f'<HintPath>{escape(str(dll))}</HintPath></Reference></ItemGroup></Project>', "utf-8")
    packages = tmp_path / "offline-packages"
    packages.mkdir()
    environment = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
                   "DOTNET_CLI_HOME": str(tmp_path / "dotnet-home")}
    built = subprocess.run([dotnet, "build", "--configuration", "Release", "--nologo", "--verbosity", "quiet",
                            f"--property:RestoreSources={packages}"],
                           cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([dotnet, str(tmp_path / "bin" / "Release" / f"net{major}.0" / "Probe.dll")],
                            input=json.dumps({"policies": policies, "operationPolicies": operation_policies,
                                              "cases": cases}), cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    responses = {r["id"]: r for r in json.loads(result.stdout)}
    errors = {"case-sensitive": 404, "bad-id": 400, "missing-key": 401, "duplicate-key": 401, "forbidden": 403,
              "missing-correlation": 400, "invalid-correlation": 400, "duplicate-correlation": 400,
              "absent-body": 400, "empty-body": 400, "wrong-content-type": 400, "body-validator-error": 400,
              "rate-limit": 429, "override-rate-limit": 429, "override-body-error": 400,
              "SubscriptionKeyNotFound": 401, "SubscriptionKeyInvalid": 401, "operation-simulation": 503,
              "error-header-preservation": 400}
    errors.update({"limited-api-rate": 429, "limited-operation-rate": 429,
                   "limited-invalid": 400, "limited-body-error": 400})
    for case in cases:
        response = responses[case["id"]]
        if case["id"] in {"unknown-error", "unrouted", "unknown-operation"}:
            assert "status" not in response, response
            continue
        status = errors.get(case["id"], 200)
        assert response["status"] == status, response
        headers, body = response["headers"], response["body"]
        assert headers["Cache-Control"] == "no-store", response
        assert headers["Content-Type"] == ("application/json" if status == 200 else "application/problem+json")
        override = case["policy"] not in {"multi", "limited"} and case["operationId"] == "post-thing"
        correlation = headers["X-Trace-Id" if override else "X-Correlation-Id"]
        assert body["traceId" if override else "correlationId"] == correlation, response
        assert re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", correlation)
        if case["id"] not in {"missing-correlation", "invalid-correlation", "duplicate-correlation"}:
            assert correlation == CORRELATION, response
        assert headers.get("Retry-After") == ("23" if status == 429 else None), response
        if status in (400, 401, 403):
            assert not response["limiterVisited"], response
        if status == 200:
            assert body["updatedAt"] == "2025-01-02T12:34:56+02:00", response
        if case["policy"] == "headers":
            assert headers["X-Source-Time"] == (
                "2025-01-02T12:34:56+02:00" if status == 200 else "2025-02-03T23:45:01-03:30"), response
    assert responses["composite-match"]["body"]["thingId"] == "T-2"
    assert responses["alternative-match"]["body"]["thingId"] == "T-2"
    assert responses["composite-fallback"]["body"]["thingId"] == "T-1"
    assert responses["optional-missing-body"]["body"]["thingId"] == "T-3"
    assert responses["optional-empty-body"]["body"]["thingId"] == "T-3"
    assert responses["limited-post"]["visitedLimits"] == ["api", "operation"]
    assert responses["limited-get"]["visitedLimits"] == ["api"]
    assert responses["limited-api-rate"]["visitedLimits"] == ["api"]
    assert responses["limited-operation-rate"]["visitedLimits"] == ["api", "operation"]
    assert responses["limited-invalid"]["visitedLimits"] == []
    assert responses["limited-body-error"]["visitedLimits"] == []
    assert responses["override-body-error"]["body"]["code"] == "POST_INVALID"
