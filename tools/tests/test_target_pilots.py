"""Local protocol boundary tests, not evidence of a live AI Gateway pilot."""
import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest
import yaml
from mcp import Client
from mcp.server import MCPServer

from mcp_openapi_creator_kit.consumer_export import load_client, preview_plan
from mcp_openapi_creator_kit.server import create_server

ROOT = Path(__file__).resolve().parents[2]


def pilot(name):
    manifest, specs = load_client(ROOT, "sample")
    manifest["apis"][0]["mcpTools"] = ["get-customer-context"]
    manifest["targets"] = yaml.safe_load((ROOT / "docs" / "pilots" / f"{name}.yaml").read_text("utf-8"))
    return manifest, specs


@contextmanager
def local_rest_fixture(expected_path, example):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            calls.append(self.path)
            if self.headers.get("Ocp-Apim-Subscription-Key") != "fictional-test-value":
                self.send_error(401)
                return
            if self.path != expected_path:
                self.send_error(404)
                return
            body = json.dumps(example).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def read_fixture(base, path, authorized=True):
    headers = {"Ocp-Apim-Subscription-Key": "fictional-test-value"} if authorized else {}
    with urlopen(Request(base + path, headers=headers), timeout=5) as response:
        return json.load(response)


@pytest.mark.anyio
async def test_gateway_pilot_local_mcp_openapi_roundtrip():
    manifest, specs = pilot("ai-gateway-preview")
    artifacts = preview_plan(manifest, specs)
    plan = json.loads(artifacts["ai-gateway-plan.json"])
    assert not plan["applyAllowed"]
    spec = json.loads(artifacts[plan["sources"][0]["file"]])
    path, item = next(iter(spec["paths"].items()))
    route = urlsplit(spec["servers"][0]["url"]).path + path
    operation = item["get"]
    example = operation["responses"]["200"]["content"]["application/json"]["example"]
    local_mcp = MCPServer("local-pilot-adapter")
    with local_rest_fixture(route, example) as (origin, calls):
        with pytest.raises(HTTPError) as error:
            read_fixture(origin, route, authorized=False)
        assert error.value.code == 401
        @local_mcp.tool(name=operation["operationId"], description=operation["description"])
        async def read_customer() -> dict[str, str]:
            result = await asyncio.to_thread(read_fixture, origin, route)
            return {"customerId": result["customerId"], "source": result["source"]}

        async with Client(local_mcp, mode="legacy") as client:
            tools = (await client.list_tools()).tools
            assert [t.name for t in tools] == ["get-customer-context"]
            response = await client.call_tool(tools[0].name, {})
            assert response.structured_content["customerId"] == "CUST-SME-001"
            assert response.structured_content["source"] == "mock"
        assert calls == [route, route]
    # The adapter is just a local contract consumer, not an AI Gateway emulator.
    assert plan["liveVerified"] is False


@pytest.mark.anyio
async def test_companion_and_dashboard_target_reports_remain_read_only(tmp_path):
    import shutil
    for name in ("AGENTS.md", "skills", "apis", "catalog", "clients"):
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, tmp_path / name, ignore=shutil.ignore_patterns("generated"))
        else:
            shutil.copy(source, tmp_path / name)
    manifest, _ = pilot("ai-gateway-preview")
    path = tmp_path / "clients" / "sample" / "mcp-manifest.yaml"
    path.write_text(yaml.safe_dump(manifest), "utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    runtime = create_server(tmp_path)
    async with Client(runtime.mcp, mode="legacy") as client:
        tools = (await client.list_tools()).tools
        assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
        response = await client.call_tool("target-report", {"client": "sample"})
        assert response.structured_content["offlineReady"]
        names = [a["name"] for a in response.structured_content["artifacts"]]
        assert set(names) == {"ai-gateway-plan.json", "gateway-openapi-customer-care.json"}
        info = await client.call_tool("dashboard-get-url", {})
        with urlopen(info.structured_content["url"], timeout=5) as http:
            html = http.read().decode()
            assert "Consumer targets" in html
            assert "ai-gateway-plan.json" in html
            assert "connect-src 'none'" in http.headers["Content-Security-Policy"]
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
