"""Consumer targets are independent of the existing APIM deployment profiles."""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$", max_length=80)]


class TargetError(ValueError):
    """An actionable, offline target validation failure."""


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value or "{" in value or "}" in value
            or any(p in (".", "..") for p in unquote(parsed.path).split("/"))):
        raise TargetError("Expected an absolute HTTPS URL without credentials, query, fragment or traversal")
    try:
        parsed.port
    except ValueError as error:
        raise TargetError("Invalid HTTPS port") from error
    return value.rstrip("/")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RemoteMCP(StrictModel):
    name: Slug
    url: str
    auth: Literal["none", "apiKey", "oauth2-interactive", "managedIdentity"]
    secretRef: Slug | None = None

    @model_validator(mode="after")
    def check(self):
        https_url(self.url)
        if (self.auth == "apiKey") != (self.secretRef is not None):
            raise ValueError("Only apiKey requires a secretRef name; never provide a secret")
        return self


class GatewayPreview(StrictModel):
    region: Literal["eastus2", "swedencentral"]
    source: Literal["openapi", "remote-mcp"] = "openapi"
    restBaseUrls: dict[Slug, str] = Field(default_factory=dict)
    remoteServers: list[RemoteMCP] = Field(default_factory=list)

    @model_validator(mode="after")
    def check(self):
        for url in self.restBaseUrls.values():
            https_url(url)
        if self.source == "openapi" and self.remoteServers:
            raise ValueError("remoteServers requires source remote-mcp")
        if self.source == "remote-mcp" and (not self.remoteServers or self.restBaseUrls):
            raise ValueError("remote-mcp requires remoteServers and no restBaseUrls")
        names = [s.name for s in self.remoteServers]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate remote backend name")
        return self


class Targets(StrictModel):
    consumer: Literal["copilot-studio", "rest"] = "copilot-studio"
    gateway: Literal["existing-apim", "ai-gateway-preview"] = "existing-apim"
    preview: GatewayPreview | None = None

    @model_validator(mode="after")
    def check(self):
        if (self.gateway == "ai-gateway-preview") != (self.preview is not None):
            raise ValueError("gateway ai-gateway-preview requires preview configuration")
        return self


def parse_targets(manifest: dict) -> Targets:
    try:
        return Targets.model_validate(manifest.get("targets", {}))
    except ValueError as error:
        # Do not echo pydantic's input_value: unknown fields might contain credentials.
        details = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in error.errors(include_input=False)
        ) if hasattr(error, "errors") else str(error)
        raise TargetError(f"targets: {details}") from None


def target_capabilities() -> dict:
    return {
        "consumerTargets": ["copilot-studio", "rest"],
        "gatewayTargets": ["existing-apim", "ai-gateway-preview"],
        "gatewayProfilesUnchanged": ["native-mcp", "policy-mcp-consumption", "rest-consumption"],
        "aiGatewayPreview": {
            "managementApiVersion": "2026-05-01-preview",
            "regions": ["eastus2", "swedencentral"], "runtimeHeader": "api-key",
            "keyScope": "every model and tool in the gateway",
            "applySupported": False, "mockServingVerified": False,
            "managementContract": "Unverified resource paths and payloads; apply is blocked.",
            "notClassicApimPolicies": True,
        },
    }
