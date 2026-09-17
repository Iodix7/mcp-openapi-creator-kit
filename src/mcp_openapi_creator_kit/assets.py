"""Versioned kit-owned resources, never resolved relative to customer data."""
from __future__ import annotations

from importlib.resources import files
import hashlib
import json
from pathlib import Path

from . import __version__


def kit_root() -> Path:
    package = Path(str(files("mcp_openapi_creator_kit")))
    bundled = package / "assets"
    if bundled.is_dir():
        return bundled
    # Editable/source development only. This location derives from our import,
    # never cwd, a customer manifest, an environment variable or a Git remote.
    source = package.parent.parent
    if (source / "pyproject.toml").is_file() and (package / "_build.py").is_file():
        return source
    raise RuntimeError("Kit assets are missing; reinstall a complete kit wheel.")


def source_info() -> dict:
    root = kit_root()
    manifest = root / "manifest.json"
    return {
        "version": __version__,
        "source": "installed-package" if manifest.is_file() else "source-development",
        "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest.is_file() else None,
    }


def asset_text(relative: str) -> str:
    return (kit_root() / relative).read_text(encoding="utf-8")


def verify_assets() -> dict:
    root = kit_root()
    if not (root / "manifest.json").is_file():
        return source_info()
    hashes = json.loads((root / "manifest.json").read_text("utf-8"))
    for relative, expected in hashes.items():
        if relative.startswith("tools/"):
            path = root.parent / "_commands" / relative.removeprefix("tools/")
        elif relative.startswith("package/"):
            path = root.parent / relative.removeprefix("package/")
        else:
            path = root / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Installed kit asset integrity mismatch: {relative}")
    return {**source_info(), "verifiedFiles": len(hashes)}
