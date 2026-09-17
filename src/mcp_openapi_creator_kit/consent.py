"""Client-mediated, per-invocation inspection consent; not a model argument."""
from __future__ import annotations

import asyncio
import sys

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import NoBackChannelError
from mcp.types import ClientCapabilities, ElicitationCapability, FormElicitationCapability
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .workflow import GatewayTarget


class InspectionConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inspect: StrictBool = Field(description="Allow this one read-only inspection of the displayed Azure target.")


def inspection_message(target: GatewayTarget, workspace: str) -> str:
    return (
        "Approve ONE read-only Azure inspection?\n"
        f"Workspace: {workspace}\nAccount: {target.account}\nTenant: {target.tenant}\n"
        f"Subscription: {target.subscription}\nResource group: {target.resource_group}\n"
        f"APIM: {target.apim_name}\nResource: {target.resource_id}\n"
        "azd: not used. GATEWAY_PROFILE: not selected; inspection determines compatibility.\n"
        "Reads: active Azure CLI identity, this APIM's metadata and global diagnostics.\n"
        "Does NOT authorize local file writes, preview, deployment, account changes or apply.\n"
        "Decline or cancel to continue offline. Do not enter credentials."
    )


async def request_inspection(ctx: Context, target: GatewayTarget, workspace: str) -> None:
    capability = ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))
    if not ctx.session.check_client_capability(capability):
        raise ToolError(
            "Inspection consent requires a client supporting MCP form elicitation. No Azure call was made. "
            "Continue offline, or have the operator run mcp-kit inspect-gateway with the explicit target "
            "in an interactive terminal. A model-supplied approval flag is not accepted.")
    try:
        result = await asyncio.wait_for(
            ctx.elicit(inspection_message(target, workspace), InspectionConsent), timeout=120)
    except TimeoutError:
        raise ToolError("Inspection consent timed out. No Azure call was made; request again or continue offline.") from None
    except NoBackChannelError:
        raise ToolError("This transport cannot deliver MCP form consent. No Azure call was made. "
                        "Use a client with a server-to-client elicitation channel, or have the operator "
                        "run mcp-kit inspect-gateway in an interactive terminal.") from None
    except ValueError:
        raise ToolError("Invalid inspection consent response. No Azure call was made.") from None
    if result.action != "accept" or not result.data.inspect:
        raise ToolError("Inspection was declined or cancelled. No Azure call was made; continue offline.")


def confirm_inspection(target: GatewayTarget, workspace: str) -> None:
    if not sys.stdin.isatty():
        raise ValueError("Inspection requires operator confirmation in an interactive terminal. "
                         "No Azure call was made; there is no noninteractive approval flag.")
    print(inspection_message(target, workspace), file=sys.stderr)
    print("To approve, type the complete APIM resource ID: ", end="", file=sys.stderr, flush=True)
    try:
        answer = input().strip()
    except EOFError:
        raise ValueError("Inspection consent was cancelled; no Azure call was made.") from None
    if answer != target.resource_id:
        raise ValueError("Inspection not approved: resource ID did not match. No Azure call was made.")
