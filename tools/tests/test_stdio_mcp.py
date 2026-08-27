import os
import sys
import urllib.request
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.anyio
async def test_stdio_legacy_protocol_and_real_dashboard_fetch():
    environment = os.environ.copy()
    source = str(REPO_ROOT / "src")
    environment["PYTHONPATH"] = (
        source + os.pathsep + environment["PYTHONPATH"]
        if environment.get("PYTHONPATH") else source
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "mcp_openapi_creator_kit",
            "--workspace",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=environment,
    )
    async with Client(stdio_client(parameters), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.call_tool("dashboard-get-url", {})
        url = result.structured_content["url"]
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode()
            assert response.status == 200
            assert "Capability catalog" in body
