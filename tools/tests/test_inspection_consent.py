"""Exercise the product consent boundary with actual MCP request roundtrips."""
import sys
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.types import ElicitResult

from mcp_openapi_creator_kit import gateway
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.server import create_server
from offline_scenarios import SyntheticAzure
from test_workflow import target


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("action,content", [
    ("decline", None), ("cancel", None), ("accept", {"inspect": False}),
    ("accept", {"inspect": "true"}), ("accept", {"inspect": 1}), ("accept", None),
])
async def test_refused_cancelled_or_invalid_form_does_not_read(tmp_path, monkeypatch, mode, action, content):
    azure = SyntheticAzure(tmp_path)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    async def answer(_ctx, params):
        assert not azure.calls
        assert target().resource_id in params.message
        return ElicitResult(action=action, content=content)
    async with Client(create_server(tmp_path).mcp, mode=mode, elicitation_callback=answer) as client:
        result = await client.call_tool("inspect-gateway", {"target": target().model_dump(by_alias=True)})
        assert result.is_error
    assert azure.calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_unsupported_client_fails_before_identity_reads(tmp_path, monkeypatch, mode):
    azure = SyntheticAzure(tmp_path)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    async with Client(create_server(tmp_path).mcp, mode=mode) as client:
        listing = await client.list_tools()
        tool = next(t for t in listing.tools if t.name == "inspect-gateway")
        assert set(tool.input_schema["properties"]) == {"target"}
        result = await client.call_tool("inspect-gateway", {"target": target().model_dump(by_alias=True)})
        assert result.is_error and "interactive terminal" in result.content[0].text
    assert azure.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["legacy"])
async def test_accepted_consent_is_single_use_and_bound_to_new_target(tmp_path, monkeypatch, mode):
    azure = SyntheticAzure(tmp_path)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    messages = []
    async def answer(_ctx, params):
        messages.append(params.message)
        if len(messages) == 1:
            assert azure.calls == []
            return ElicitResult(action="accept", content={"inspect": True})
        assert "different-apim" in params.message
        return ElicitResult(action="decline")
    async with Client(create_server(tmp_path).mcp, mode=mode, elicitation_callback=answer) as client:
        first = await client.call_tool("inspect-gateway", {"target": target().model_dump(by_alias=True)})
        assert not first.is_error
        changed = target().model_copy(update={"apim_name": "different-apim"})
        second = await client.call_tool("inspect-gateway", {"target": changed.model_dump(by_alias=True)})
        assert second.is_error
    assert len(messages) == 2 and len(azure.calls) == 3


@pytest.mark.anyio
async def test_timed_out_form_never_proceeds_to_inspection(tmp_path, monkeypatch):
    from mcp_openapi_creator_kit import consent
    from mcp.server.mcpserver.exceptions import ToolError
    async def elicit(*_args):
        pytest.fail("Timed out consent must not finish")
    async def timeout(coroutine, *, timeout):
        assert timeout == 120
        coroutine.close()
        raise TimeoutError
    monkeypatch.setattr(consent.asyncio, "wait_for", timeout)
    ctx = SimpleNamespace(session=SimpleNamespace(check_client_capability=lambda _: True), elicit=elicit)
    with pytest.raises(ToolError, match="No Azure call"):
        await consent.request_inspection(ctx, target(), str(tmp_path))


@pytest.mark.parametrize("interactive,answer", [(False, ""), (True, "yes"), (True, "wrong-target")])
def test_cli_inspection_noninteractive_or_wrong_confirmation_never_reads(tmp_path, monkeypatch, interactive, answer):
    azure = SyntheticAzure(tmp_path)
    monkeypatch.setattr(gateway, "run_azure", azure.run)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr("builtins.input", lambda: answer)
    t = target()
    with pytest.raises(SystemExit):
        main(["--workspace", str(tmp_path), "inspect-gateway", "--account", t.account,
              "--tenant", t.tenant, "--subscription", t.subscription,
              "--resource-group", t.resource_group, "--apim-name", t.apim_name])
    assert azure.calls == []
