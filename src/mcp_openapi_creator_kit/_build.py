"""Deterministic setuptools asset staging from the maintained source files."""
from pathlib import Path
import hashlib
import json
import shutil

from setuptools.command.build_py import build_py


ASSETS = (
    "AGENTS.md", "HANDOVER.md", "skills/*.md", "docs/*.md",
    "docs/templates/*.md", "docs/pilots/*.md", "modules/*",
    "apis/canonical-schemas.yaml", "apis/customer-care/openapi.yaml",
    "clients/sample/mcp-manifest.yaml", "catalog/metadata.yaml",
    "catalog/template.html",
    "platform/gateway.bicep", "platform/resource-group.bicep",
    "examples/library/**/*.yaml", "examples/library/**/*.md",
)
COMMANDS = (
    "build-facade", "build-policy-mcp", "deploy-client", "retire-client", "deployment",
    "deployment_recovery", "deployment_recovery_payloads",
    "lifecycle", "local_python", "prepare-variant", "reconcile-client",
    "reconcile-all", "validate-deployment-profile", "verification",
    "verify-mcp", "verify-rest", "provision-gateway", "provision-resource-group",
)


class BuildPy(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parents[2]
        package = Path(self.build_lib) / "mcp_openapi_creator_kit"
        assets = package / "assets"
        if assets.exists():
            shutil.rmtree(assets)
        hashes = {}
        commands = package / "_commands"
        initializer = (root / "src" / "mcp_openapi_creator_kit" / "_commands" / "__init__.py").read_bytes()
        if commands.exists():
            shutil.rmtree(commands)
        commands.mkdir(parents=True)
        (commands / "__init__.py").write_bytes(initializer)
        for pattern in ASSETS:
            for source in sorted(root.glob(pattern)):
                if not source.is_file():
                    continue
                relative = source.relative_to(root).as_posix()
                target = assets / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
        for name in COMMANDS:
            source = root / "tools" / f"{name}.py"
            target = package / "_commands" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            hashes[f"tools/{source.name}"] = hashlib.sha256(source.read_bytes()).hexdigest()
        for source in sorted((root / "src" / "mcp_openapi_creator_kit").rglob("*")):
            if source.is_file() and source.suffix in {".py", ".json"} and "__pycache__" not in source.parts:
                relative = source.relative_to(root / "src" / "mcp_openapi_creator_kit").as_posix()
                hashes[f"package/{relative}"] = hashlib.sha256(source.read_bytes()).hexdigest()
        (assets / "manifest.json").write_text(
            json.dumps(hashes, sort_keys=True, indent=2) + "\n", encoding="utf-8")
