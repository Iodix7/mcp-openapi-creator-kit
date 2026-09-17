#!/usr/bin/env python3
"""Prepare a local colleague release from an allowlisted snapshot; never publish."""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
EPOCH = 315532800  # Stable 1980 timestamp is also supported by ZIP.
spec = importlib.util.spec_from_file_location("release_installer", ROOT / "install-kit.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def source_files(root: Path) -> list[Path]:
    declarations = {}
    tree = ast.parse((root / "src/mcp_openapi_creator_kit/_build.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"ASSETS", "COMMANDS"}:
                    declarations[target.id] = ast.literal_eval(node.value)
    patterns = [
        "pyproject.toml", "MANIFEST.in", "LICENSE", "README.md", "install-kit.py",
        "src/mcp_openapi_creator_kit/**/*.py", "src/mcp_openapi_creator_kit/**/*.json",
        "src/mcp_openapi_creator_kit/py.typed", "tools/*.py",
        *declarations["ASSETS"],
    ]
    selected = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if "__pycache__" in path.parts or "assets" in path.relative_to(root).parts:
                continue
            installer.safe_path(path)
            if path.is_file():
                selected.add(path)
    for required in ("pyproject.toml", "MANIFEST.in", "install-kit.py", "LICENSE"):
        if root / required not in selected:
            raise installer.InstallError(f"Required release source is missing: {required}")
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def normalize_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        entries = [(member, archive.extractfile(member).read() if member.isfile() else None)
                   for member in archive.getmembers()]
    replacement = path.with_suffix(".normalized")
    with replacement.open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=EPOCH) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for member, content in sorted(entries, key=lambda entry: entry[0].name):
                    if not member.isfile() and not member.isdir():
                        raise installer.InstallError("Release sdist contains a link or unsupported member.")
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    member.mtime = EPOCH
                    member.pax_headers = {}
                    member.mode = 0o644 if member.isfile() else 0o755
                    archive.addfile(member, io.BytesIO(content) if content is not None else None)
    replacement.replace(path)


def instructions(wheel: str, version: str) -> str:
    return f"""MCP OpenAPI Creator Kit {version} — local release, not publication

Prerequisites: explicitly selected Python >=3.12 with venv/ensurepip; an existing
customer directory; new runtime and plugin paths whose parents already exist.
No source checkout, Azure login, azd, or global Python installation is required.
Dependencies are installed only on --apply (PyPI by default).

Trust the supplier and verify SHA256SUMS via a trusted channel before executing
install-kit.py. An adjacent checksum is integrity evidence, not a signature.
Share this release, NOT an exported plugin directory or an installed venv.

Windows PowerShell (replace the explicit Python and all paths):
$Python = 'C:\\Python312\\python.exe'
$Release = 'C:\\Kit releases\\{version}'
$InstallArgs = @('-I', "$Release\\install-kit.py",
  '--wheel', "$Release\\{wheel}",
  '--sha256-file', "$Release\\SHA256SUMS",
  '--workspace', 'C:\\Customers\\Acme',
  '--install-dir', 'C:\\Kit runtimes\\{version}-acme',
  '--plugin-dir', 'C:\\Kit plugins\\{version}-acme')
& $Python @InstallArgs
# Review the plan, then:
& $Python @InstallArgs --apply
if ($LASTEXITCODE -ne 0) {{ throw 'Installation failed; do not enable its plugin' }}

The installer prints the exact Copilot CLI argument array and VS Code
chat.pluginLocations entry. Review them and choose ONE host method manually.
No host settings, plugin enablement, customer files or Azure resources change.
A successful installation.json receipt lives in the new runtime.

Upgrade: use the new release with fresh runtime/plugin paths and the SAME
customer directory. Keep the old runtime/plugin for rollback. Only after success,
disable the previous host connection and manually enable the new bundle.
Failed paths remain for diagnosis; never enable them or overwrite an old runtime.

Offline: obtain a complete compatible dependency wheelhouse from your trusted
supplier; add --wheelhouse <absolute-directory> --offline to both invocations.
No arbitrary download URL or interpreter/PATH fallback is supported.

POSIX: the bootstrap also selects bin/python rather than Scripts/python.exe.
Use absolute paths and an explicitly selected Python:
"/opt/python/3.12/bin/python3" -I "/opt/releases/{version}/install-kit.py" \\
  --wheel "/opt/releases/{version}/{wheel}" \\
  --sha256-file "/opt/releases/{version}/SHA256SUMS" \\
  --workspace "/srv/customer-data/acme" \\
  --install-dir "/opt/kit-runtimes/{version}-acme" \\
  --plugin-dir "/opt/kit-plugins/{version}-acme"
Repeat with --apply after preview. This release was exercised on Windows;
POSIX commands are illustrative, not host/platform acceptance evidence.

Host prerequisites: a Copilot entitlement and a CLI supporting local plugin
installation, or VS Code/Copilot supporting chat.pluginLocations. CLI direct-path
installation may be deprecated by a host release. Enabling can start the MCP.
Sign in to GitHub Copilot yourself before activation. The bootstrap never signs
in; a sign-in prompt blocks host acceptance even after successful installation.
Python installation does not establish host/model, VS Code, or live Azure E2E.
See packaged docs/installation.md and docs/copilot-plugin.md for scope.
"""


def build(root: Path, output: Path, *, apply: bool = False) -> dict:
    if sys.version_info < (3, 12):
        raise installer.InstallError("Use the explicitly selected build Python >=3.12.")
    output = installer.new_directory(output)
    if root.is_relative_to(output):
        raise installer.InstallError("Artifact output must not contain the source checkout.")
    files = source_files(root)
    if output.is_relative_to(root):
        source_directories = {path.relative_to(root).parts[0] for path in files
                              if len(path.relative_to(root).parts) > 1}
        if output.relative_to(root).parts[0] in source_directories:
            raise installer.InstallError("Release output must be outside maintained source/data directories.")
    inputs = {path.relative_to(root).as_posix(): installer.digest(path) for path in files}
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
    if project["name"] != installer.DISTRIBUTION:
        raise installer.InstallError("This release builder is only for MCP OpenAPI Creator Kit.")
    plan = {"status": "preview", "writes": False, "version": project["version"],
            "sourceContentSha256": identity, "sourceFiles": inputs, "output": str(output),
            "notice": "Local artifacts only. No tagging, publication, cloud or host activation."}
    if not apply:
        return plan
    if sys.prefix == sys.base_prefix:
        raise installer.InstallError("Use a dedicated build venv with the project's explicit build dependencies.")
    tool_versions = {}
    for name in ("build", "setuptools", "wheel"):
        try:
            tool_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise installer.InstallError(f"Missing {name}; explicitly install the project's build dependencies first.") from error
    output.mkdir(mode=0o700)
    stage = output / ".build-source"
    stage.mkdir()
    marker = output / "RELEASE-INCOMPLETE"
    marker.write_text("Local release build incomplete; do not distribute this directory.\n", encoding="utf-8")
    for path in files:
        relative = path.relative_to(root)
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
        if installer.digest(destination) != inputs[relative.as_posix()]:
            raise installer.InstallError(f"Source changed while snapshotting: {relative}; retry with a fresh output.")
        os.utime(destination, (EPOCH, EPOCH))
    env = installer.clean_environment()
    env.update(SOURCE_DATE_EPOCH=str(EPOCH), PYTHONHASHSEED="0")
    process = subprocess.run(
        [sys.executable, "-I", "-m", "build", "--no-isolation", "--wheel", "--sdist", "--outdir", str(output)],
        cwd=stage, env=env, shell=False, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.returncode:
        raise installer.InstallError("Build failed. Check explicit build dependencies and source; "
                                     "the new output remains incomplete. Nothing was published.")
    wheels, sdists = list(output.glob("*.whl")), list(output.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise installer.InstallError("Expected exactly one wheel and one sdist.")
    wheel, sdist = wheels[0], sdists[0]
    wheel_metadata = installer.wheel_identity(wheel, installer.digest(wheel))
    if wheel_metadata["version"] != project["version"]:
        raise installer.InstallError("Built wheel version does not match the source project.")
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        if not any(name.endswith("/install-kit.py") for name in names):
            raise installer.InstallError("MANIFEST.in must include install-kit.py before preparing a release.")
    normalize_sdist(sdist)
    shutil.copyfile(stage / "install-kit.py", output / "install-kit.py")
    (output / "INSTALL.txt").write_text(instructions(wheel.name, project["version"]), encoding="utf-8", newline="\n")
    artifact_hashes = {path.name: installer.digest(path)
                       for path in sorted((wheel, sdist, output / "install-kit.py", output / "INSTALL.txt"))}
    provenance = {
        "schemaVersion": 1, "distribution": project["name"], "version": project["version"],
        "sourceContentSha256": identity, "sourceFiles": inputs, "buildTools": tool_versions,
        "sourceDateEpoch": EPOCH, "wheel": wheel_metadata, "artifacts": artifact_hashes,
        "notice": "Unsigned local build provenance; hashes identify content, not publisher authenticity.",
    }
    (output / "provenance.json").write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    artifact_hashes["provenance.json"] = installer.digest(output / "provenance.json")
    (output / "SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in sorted(artifact_hashes.items())), encoding="utf-8", newline="\n")
    # Only this invocation's explicitly created source snapshot is disposable.
    shutil.rmtree(stage)
    marker.unlink()
    return {**plan, "status": "prepared", "writes": True, "artifacts": artifact_hashes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New absolute artifact directory; parent must exist")
    parser.add_argument("--apply", action="store_true", help="Build the previewed release without network build isolation")
    args = parser.parse_args(argv)
    try:
        result = build(ROOT, args.output, apply=args.apply)
    except (OSError, ValueError, RuntimeError, KeyError, tarfile.TarError) as error:
        print(json.dumps({"status": "failed", "error": str(error), "published": False}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
