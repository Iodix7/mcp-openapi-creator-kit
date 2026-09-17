import os
import json
import urllib.request
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.anyio
async def test_stdio_legacy_protocol_and_real_dashboard_fetch(mcp_workspace):
    config_name = "mcp.json" if os.name == "nt" else "mcp.posix.json"
    config = json.loads((REPO_ROOT / ".vscode" / config_name).read_text())["servers"]["mcp-openapi-creator"]
    command = config["command"].replace("${workspaceFolder}", str(REPO_ROOT))
    arguments = [value.replace("${workspaceFolder}", str(mcp_workspace)) for value in config["args"]]
    parameters = StdioServerParameters(
        command=command,
        args=arguments,
        cwd=REPO_ROOT,
    )
    async with Client(stdio_client(parameters), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        browse = await client.call_tool("catalog-search", {})
        assert not browse.is_error
        assert browse.structured_content["total"] > 0
        result = await client.call_tool("dashboard-get-url", {})
        url = result.structured_content["url"]
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode()
            assert response.status == 200
            assert "Capability catalog" in body
