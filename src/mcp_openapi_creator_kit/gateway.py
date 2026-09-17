"""Bounded Azure read adapter and process-local evidence registry. No disk evidence import."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from .runtime import command
from .workflow import GatewayObservation, GatewayTarget


def run_azure(arguments: list[str]) -> str:
    account_read = arguments[:3] == ["az", "account", "show"]
    resource_read = arguments[:4] == ["az", "rest", "--method", "GET"]
    if not (account_read or resource_read):
        raise ValueError("Gateway inspection permits only account show and scoped ARM GET")
    executable = shutil.which("az")
    if executable is None:
        raise RuntimeError("Azure CLI is missing; install/authenticate separately. Offline planning remains available.")
    try:
        result = subprocess.run([executable, *arguments[1:]], capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Azure inspection timed out; no fallback or context change was attempted") from None
    if result.returncode:
        raise RuntimeError("Azure inspection failed; check authentication/permissions privately. "
                           "Raw Azure output is suppressed; no fallback or context change was attempted.")
    return result.stdout


def verify_active_account(target: GatewayTarget, runner) -> None:
    account = json.loads(runner(["az", "account", "show", "--query",
                                 "{user:user.name,tenantId:tenantId,id:id}", "--output", "json"]))
    expected = {"user": target.account, "tenantId": target.tenant, "id": target.subscription}
    if not isinstance(account, dict) or any(
            not isinstance(account.get(key), str) or account[key].casefold() != value.casefold()
            for key, value in expected.items()):
        raise ValueError("Active Azure CLI account/tenant/subscription differs from the approved target; "
                         "align it explicitly before inspection. No resource was read.")


def inspect_gateway(workspace: Path, target: GatewayTarget, *, runner=None) -> GatewayObservation:
    """The expected account/target must come from operator-approved context, never defaults."""
    runner = runner or run_azure
    verify_active_account(target, runner)
    lifecycle = command("lifecycle")
    client = lifecycle.AzRestClient(target.subscription, target.resource_group,
                                    target.apim_name, runner=runner)
    apim = client.request("GET", f"{client.base}?api-version={lifecycle.API_VERSION}")
    if not isinstance(apim, dict) or str(apim.get("id", "")).casefold() != target.resource_id.casefold():
        raise ValueError("APIM response does not identify the requested gateway; no evidence issued")
    diagnostics = client.paged(f"{client.base}/diagnostics?api-version={lifecycle.API_VERSION}")
    return GatewayObservation.issue(str(workspace.resolve()), target, apim, diagnostics)


class GatewayEvidence:
    """Opaque handles are meaningful only in this process and this pinned workspace."""
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self._records: dict[str, GatewayObservation] = {}

    def inspect(self, target: GatewayTarget) -> GatewayObservation:
        observation = inspect_gateway(self.workspace, target)
        # Bound memory; old handles fail explicitly rather than resolving another target.
        if len(self._records) >= 64:
            del self._records[next(iter(self._records))]
        self._records[observation.evidence_id] = observation
        return observation

    def get(self, evidence_id: str) -> GatewayObservation:
        observation = self.lookup(evidence_id)
        if observation is None:
            raise ValueError("Unknown gateway evidence ID. Run inspect-gateway in this MCP session; "
                             "pasted facts, CLI output and records from another session are not evidence.")
        return observation

    def lookup(self, evidence_id: str) -> GatewayObservation | None:
        observation = self._records.get(evidence_id)
        return observation if observation and observation.workspace == str(self.workspace) else None
