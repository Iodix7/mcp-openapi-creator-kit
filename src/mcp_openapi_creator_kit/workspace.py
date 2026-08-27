"""Safe, read-only access to an MCP OpenAPI Creator Kit workspace."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .catalog import build_index, render_outputs


class WorkspaceError(RuntimeError):
    """Raised when the selected workspace is missing or unsafe."""


class WorkspaceReader:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    def _path(self, *parts: str) -> Path:
        return self._contained(self.root.joinpath(*parts))

    def _contained(self, path: Path) -> Path:
        candidate = path.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise WorkspaceError("workspace path escapes the selected root") from error
        return candidate

    def _validate_catalog_inputs(self):
        for relative in (
            ("catalog", "template.html"),
            ("catalog", "metadata.yaml"),
            ("apis", "canonical-schemas.yaml"),
        ):
            path = self.root.joinpath(*relative)
            if path.exists():
                self._contained(path)
        for pattern in (
            "clients/*/mcp-manifest.yaml",
            "apis/*/openapi.yaml",
        ):
            for path in self.root.glob(pattern):
                self._contained(path)

    def read_text(self, *parts: str) -> str:
        path = self._path(*parts)
        if not path.is_file():
            raise WorkspaceError(
                f"required workspace file is missing: {path.relative_to(self.root)}")
        return path.read_text(encoding="utf-8")

    def read_yaml(self, *parts: str) -> dict[str, Any]:
        value = yaml.safe_load(self.read_text(*parts))
        if not isinstance(value, dict):
            raise WorkspaceError(f"{'/'.join(parts)} must contain a YAML object")
        return value

    def manifests(self) -> list[dict[str, Any]]:
        records = []
        clients_dir = self._path("clients")
        if not clients_dir.is_dir():
            return records
        for path in sorted(clients_dir.glob("*/mcp-manifest.yaml")):
            resolved = path.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError as error:
                raise WorkspaceError("client manifest escapes the workspace root") from error
            manifest = yaml.safe_load(resolved.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise WorkspaceError(
                    f"{resolved.relative_to(self.root)} must contain a YAML object")
            records.append(manifest)
        return records

    def status(self) -> dict[str, Any]:
        required = [
            "AGENTS.md",
            "skills/discovery.md",
            "skills/onboarding.md",
            "skills/lifecycle.md",
            "catalog/template.html",
        ]
        missing = [value for value in required if not self._path(*value.split("/")).is_file()]
        manifests = self.manifests()
        contracts_dir = self._path("apis")
        contracts = []
        if contracts_dir.is_dir():
            for path in contracts_dir.glob("*/openapi.yaml"):
                contracts.append(self._contained(path).parent.name)
            contracts.sort()
        return {
            "valid": not missing,
            "root": str(self.root),
            "clients": [
                {
                    "id": item.get("client"),
                    "displayName": item.get("displayName"),
                    "apis": [api.get("name") for api in item.get("apis", [])
                             if isinstance(api, dict)],
                }
                for item in manifests
            ],
            "contracts": contracts,
            "catalogGenerated": self._path(
                "catalog", "generated", "catalog.json").is_file(),
            "missing": missing,
        }

    def catalog(self) -> dict[str, Any]:
        self._validate_catalog_inputs()
        return build_index(self.root, self._path("catalog", "metadata.yaml"))

    def catalog_json(self) -> str:
        return json.dumps(self.catalog(), indent=2, ensure_ascii=False) + "\n"

    def dashboard(self) -> tuple[dict[str, Any], str]:
        self._validate_catalog_inputs()
        return render_outputs(self.root, self._path("catalog", "metadata.yaml"))
