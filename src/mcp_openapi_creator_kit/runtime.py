"""Dispatch only installed, allowlisted command implementations."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys

COMMANDS = {
    "build": "build-facade", "build-policy": "build-policy-mcp",
    "deploy": "deploy-client", "retire": "retire-client", "variant": "prepare-variant",
    "provision": "provision-gateway",
    "provision-group": "provision-resource-group",
    "validate": "validate-deployment-profile", "verify-mcp": "verify-mcp",
    "verify-rest": "verify-rest",
}


def command(name: str):
    if name not in {*COMMANDS.values(), "deployment", "local_python", "lifecycle", "verification"}:
        raise ValueError("Unknown installed command")
    return importlib.import_module(f"mcp_openapi_creator_kit._commands.{name}")


def isolated_command(name: str, root: Path):
    """Bind trusted command globals without changing another workspace's module."""
    original = command(name)
    spec = importlib.util.spec_from_file_location(original.__name__, original.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = root
    return module


def invoke(name: str, root: Path, arguments: list[str]):
    module = command(COMMANDS[name])
    previous_root = module.REPO_ROOT
    previous_args = sys.argv
    try:
        module.REPO_ROOT = root
        sys.argv = [f"mcp-kit {name}", *arguments]
        module.main()
    finally:
        module.REPO_ROOT = previous_root
        sys.argv = previous_args


def child_command(root: Path, name: str, *arguments: str) -> list[str]:
    return [sys.executable, "-I", "-m", "mcp_openapi_creator_kit.cli",
            "--workspace", str(root), name, *arguments]
