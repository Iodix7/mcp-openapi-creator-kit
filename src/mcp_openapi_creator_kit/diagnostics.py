"""Bounded allowlisted process diagnostics; provider payloads are never returned."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re

MARKER = "MCP_KIT_DIAGNOSTIC:"
HINTS = {
    "dns": "Check DNS/network access to Azure, then repeat only the authorized read.",
    "authentication": "Reauthenticate the approved account; do not change the target or permissions.",
    "timeout": "Inspect the recorded phase and Azure state before resuming; writes may have completed.",
    "arm-validation": "Review the exact deployment validation privately; correct inputs before a new preview.",
    "encoding": "Check CLI output encoding; raw output remains suppressed.",
    "unknown": "Inspect private CLI logs and the approved target; no fallback or automatic retry.",
}
ARM_CODES = frozenset({
    "AuthorizationFailed", "AuthenticationFailed", "InvalidAuthenticationToken", "Forbidden",
    "InvalidApiVersionParameter", "MethodNotAllowedInPricingTier", "InvalidTemplate",
    "InvalidTemplateDeployment", "DeploymentFailed", "ResourceNotFound",
    "ResourceGroupNotFound", "PreconditionFailed", "Conflict", "TooManyRequests",
})
COMMANDS = {
    "az.deployment.group.list": "read", "az.deployment.group.show": "read",
    "az.deployment.group.what-if": "read", "az.deployment.group.validate": "read",
    "az.deployment.group.create": "write",
    "az.deployment.sub.what-if": "read", "az.deployment.sub.create": "write",
    "az.account.show": "read", "az.cloud.show": "read",
    "az.group.show": "read", "az.apim.show": "read",
    **{f"az.rest.{method}": "read" if method in {"GET", "HEAD"} else "write"
       for method in ("GET", "HEAD", "PUT", "POST", "PATCH", "DELETE")},
    **{f"kit.{name}": "unknown" for name in (
        "deploy", "prepare", "provision", "provision-group", "retire",
        "verify-mcp", "verify-rest", "build", "build-policy", "spec-sync", "catalog")},
    "unknown": "unknown",
}


def command_id(arguments: list[str]) -> str:
    if arguments[0] == "az":
        if arguments[1:2] == ["rest"]:
            try:
                method = arguments[arguments.index("--method") + 1].upper()
            except (ValueError, IndexError):
                method = "GET"
            candidate = f"az.rest.{method}"
            return candidate if candidate in COMMANDS else "unknown"
        for size in (4, 3):
            candidate = ".".join(arguments[:size])
            if candidate in COMMANDS:
                return candidate
    if "mcp_openapi_creator_kit.cli" in arguments:
        try:
            candidate = "kit." + arguments[arguments.index("--workspace") + 2]
            if candidate in COMMANDS:
                return candidate
        except (ValueError, IndexError):
            pass
    return "unknown"


@dataclass(frozen=True)
class Diagnostic:
    command: str
    category: str
    exitCode: int | None
    armCode: str = "unavailable"

    def public(self) -> dict:
        return {**asdict(self), "operation": COMMANDS[self.command],
                "outcome": "read-failed" if COMMANDS[self.command] == "read" else "unknown",
                "scope": "failing-command-only", "nextAction": HINTS[self.category],
                "automaticRetry": False}

    def message(self) -> str:
        return (f"Command failed [{self.command}/{self.category}]. "
                f"Operation {COMMANDS[self.command]}; outcome {self.public()['outcome']} "
                "(failing command only). "
                f"{HINTS[self.category]} Raw output is suppressed. " + MARKER +
                json.dumps(asdict(self), separators=(",", ":")))


def _nested(stdout: bytes, stderr: bytes) -> Diagnostic | None:
    for payload in (stderr, stdout):
        for match in re.finditer(rb"MCP_KIT_DIAGNOSTIC:(\{[^\r\n]{1,512}\})", payload[-65536:]):
            try:
                value = json.loads(match[1])
                if set(value) != {"command", "category", "exitCode", "armCode"}:
                    continue
                if (value["command"] not in COMMANDS or value["category"] not in HINTS
                        or value["armCode"] not in ARM_CODES | {"unavailable"}
                        or (value["exitCode"] is not None and (
                            type(value["exitCode"]) is not int or not -65536 <= value["exitCode"] <= 65536))):
                    continue
                return Diagnostic(**value)
            except (ValueError, TypeError):
                continue
    return None


def arm_error_code(stdout: bytes, stderr: bytes, encoding: str = "utf-8") -> str:
    for payload in (stderr, stdout):
        payload = payload[:65536]
        match = re.search(rb"(?:^|\r?\n)ERROR:\s+\(([A-Za-z][A-Za-z0-9_.-]{0,79})\)(?:\s|$)", payload)
        if match:
            code = match[1].decode("ascii")
            return code if code in ARM_CODES else "unavailable"
        try:
            text = payload.decode(encoding).strip().removeprefix("ERROR:").strip()
            wrapped = re.fullmatch(
                r"(?:Bad Request|Unauthorized|Forbidden|Not Found|Method Not Allowed|Conflict|Too Many Requests)"
                r"\((\{.*\})\)", text, flags=re.DOTALL)
            value = json.loads(wrapped[1] if wrapped else text)
            code = value.get("error", {}).get("code") if isinstance(value, dict) else None
            if isinstance(code, str) and code in ARM_CODES:
                return code
        except (UnicodeError, ValueError, AttributeError):
            continue
    return "unavailable"


def classify(arguments: list[str], stdout: bytes = b"", stderr: bytes = b"",
             exit_code: int | None = None, *, category: str | None = None) -> Diagnostic:
    identifier = command_id(arguments)
    if identifier.startswith("kit.") and category is None:
        nested = _nested(stdout, stderr)
        if nested is not None:
            return nested
    data = (stderr[:65536] + b"\n" + stdout[:65536]).lower()
    code = arm_error_code(stdout, stderr)
    if category is None:
        if any(term in data for term in (b"failed to resolve", b"getaddrinfo failed",
                                         b"name or service not known", b"temporary failure in name resolution")):
            category = "dns"
        elif code in {"AuthorizationFailed", "AuthenticationFailed", "InvalidAuthenticationToken", "Forbidden"} or any(
                term in data for term in (b"az login", b"aadsts", b"interaction_required")):
            category = "authentication"
        elif code in {"InvalidTemplate", "InvalidTemplateDeployment", "InvalidApiVersionParameter",
                      "MethodNotAllowedInPricingTier"}:
            category = "arm-validation"
        elif b"timed out" in data or b"timeout" in data:
            category = "timeout"
        else:
            category = "unknown"
    if category not in HINTS:
        raise ValueError("Unknown diagnostic category")
    return Diagnostic(identifier, category, exit_code, code)


def process_error(error_type, arguments: list[str], stdout: bytes = b"", stderr: bytes = b"",
                  exit_code: int | None = None, *, category: str | None = None):
    diagnostic = classify(arguments, stdout, stderr, exit_code, category=category)
    error = error_type(diagnostic.message())
    error.diagnostic = diagnostic
    return error


def failure_record(error: BaseException, phase: str) -> dict:
    result = {"phase": phase, "type": type(error).__name__}
    diagnostic = getattr(error, "diagnostic", None)
    if isinstance(diagnostic, Diagnostic):
        result["diagnostic"] = diagnostic.public()
    return result
