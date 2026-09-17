#!/usr/bin/env python3
"""Install a supplied kit wheel into a new, pinned runtime; preview by default."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import subprocess
import sys
import zipfile


PACKAGE = "mcp_openapi_creator_kit"
DISTRIBUTION = "mcp-openapi-creator-kit"
PLUGIN = "mcp-openapi-creator"


class InstallError(ValueError):
    """An actionable installation failure, without subprocess secrets."""

    def __init__(self, message: str, *, category: str = "installation"):
        super().__init__(message)
        self.category = category


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise InstallError("Use absolute paths without '..' components.")
    if os.name == "nt" and str(path).startswith(("\\\\?\\", "\\\\.\\")):
        raise InstallError("Use normal absolute drive/UNC paths, not Windows device namespaces.")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise InstallError(f"Paths must not traverse a symlink/junction: {part}")
    return path.resolve()


def new_directory(value: str | Path) -> Path:
    path = safe_path(value)
    if path.exists():
        raise InstallError(f"Refusing an existing output (even if empty): {path}")
    if not path.parent.is_dir():
        raise InstallError(f"Create the output parent explicitly first: {path.parent}")
    return path


def disjoint(*paths: Path) -> None:
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            if first.is_relative_to(second) or second.is_relative_to(first):
                raise InstallError(f"Paths must not overlap: {first} and {second}")


def python_requirement(requirement: str, version: tuple[int, ...]) -> bool:
    """Conservative stdlib check: refuse unsupported metadata rather than guess."""
    for term in requirement.split(","):
        match = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})\s*", term)
        if not match:
            raise InstallError(f"Unsupported wheel Requires-Python: {requirement!r}")
        operator, number = match.groups()
        required = tuple(map(int, number.split(".")))
        actual = tuple(version[:len(required)])
        if not {">=": actual >= required, "<=": actual <= required,
                "==": actual == required, "!=": actual != required,
                ">": actual > required, "<": actual < required}[operator]:
            return False
    return True


def no_direct_dependencies(metadata) -> None:
    if any("@" in value or "://" in value for value in metadata.get_all("Requires-Dist", [])):
        raise InstallError("Direct-URL dependencies are unsupported; supply normal dependency wheels.")


def wheel_identity(wheel: Path, expected_sha256: str) -> dict:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise InstallError("The expected SHA-256 must contain exactly 64 hexadecimal characters.")
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise InstallError("Supply an existing local .whl file, not a URL.")
    actual = digest(wheel)
    if actual != expected_sha256.lower():
        raise InstallError("Wheel SHA-256 mismatch; nothing was installed. Obtain the trusted release again.")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise InstallError("Wheel has duplicate archive entries.")
        for name in names:
            if (PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                    or "\\" in name or ":" in name):
                raise InstallError("Wheel contains an unsafe archive path.")
        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise InstallError("Wheel must contain exactly one distribution.")
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        no_direct_dependencies(metadata)
        version = metadata.get("Version", "")
        if (metadata.get("Name") != DISTRIBUTION
                or not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+|\.dev\d+)?", version)):
            raise InstallError("This is not a supported MCP OpenAPI Creator Kit release wheel.")
        prefix = f"{PACKAGE}-{version}.dist-info"
        if (wheel.name != f"{PACKAGE}-{version}-py3-none-any.whl"
                or metadata_files[0] != f"{prefix}/METADATA"):
            raise InstallError("Wheel filename/metadata mismatch or unsupported platform tag.")
        if not python_requirement(metadata.get("Requires-Python", ""), tuple(sys.version_info[:3])):
            raise InstallError("The explicitly invoked Python does not satisfy the wheel Requires-Python.")
        wheel_metadata = BytesParser().parsebytes(archive.read(f"{prefix}/WHEEL"))
        if (wheel_metadata.get("Wheel-Version") != "1.0"
                or wheel_metadata.get("Root-Is-Purelib") != "true"
                or wheel_metadata.get_all("Tag") != ["py3-none-any"]):
            raise InstallError("Only the supplied pure-Python py3-none-any kit wheel is supported.")
        manifest_name = f"{PACKAGE}/assets/manifest.json"
        manifest_bytes = archive.read(manifest_name)
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict) or not manifest:
            raise InstallError("Wheel asset manifest is missing or empty.")
        for relative, expected in manifest.items():
            if (not isinstance(relative, str) or PurePosixPath(relative).is_absolute()
                    or ".." in PurePosixPath(relative).parts or "\\" in relative or ":" in relative):
                raise InstallError("Wheel asset manifest contains an unsafe path.")
            if relative.startswith("tools/"):
                member = f"{PACKAGE}/_commands/{relative.removeprefix('tools/')}"
            elif relative.startswith("package/"):
                member = f"{PACKAGE}/{relative.removeprefix('package/')}"
            else:
                member = f"{PACKAGE}/assets/{relative}"
            if hashlib.sha256(archive.read(member)).hexdigest() != expected:
                raise InstallError(f"Wheel asset integrity mismatch: {relative}")
        required = {"AGENTS.md", "skills/discovery.md", "skills/onboarding.md", "skills/lifecycle.md"}
        if not required.issubset(manifest):
            raise InstallError("Wheel does not contain the required installed guidance.")
        if not {"package/plugin.py", "package/cli.py"}.issubset(manifest):
            raise InstallError("Wheel lacks the plugin/CLI features required by this bootstrap; obtain the current release.")
    return {"distribution": DISTRIBUTION, "version": version, "wheel": wheel.name,
            "wheelSha256": actual, "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest()}


def expected_hash(args: argparse.Namespace, wheel: Path) -> str:
    if args.sha256:
        return args.sha256
    checksum_file = safe_path(args.sha256_file)
    matches = []
    for line in checksum_file.read_text("utf-8").splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})  ([^\r\n]+)", line)
        if match and match[2] == wheel.name:
            matches.append(match[1])
    if len(matches) != 1:
        raise InstallError("Checksum file must contain exactly one SHA256SUMS entry for this wheel filename.")
    return matches[0]


def clean_environment() -> dict[str, str]:
    certificate_overrides = {"REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "PIP_"))
           and key.upper() not in certificate_overrides}
    env.update(PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull)
    return env


def run(command: list[str], *, cwd: Path, label: str) -> str:
    process = subprocess.run(command, cwd=cwd, env=clean_environment(), shell=False,
                             text=True, encoding="utf-8", errors="replace",
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode:
        # pip diagnostics may contain authenticated repository URLs. Do not persist
        # or echo ambient credentials in an installation receipt or error message.
        diagnostic = process.stdout + process.stderr
        category = "process-failure"
        action = "Check Python/venv availability and dependency access; use a complete official wheelhouse offline."
        if "SSLV3_ALERT_HANDSHAKE_FAILURE" in diagnostic:
            category = "tls-handshake"
            action = ("TLS handshake failed; check approved proxy/firewall/CDN access or use an official offline wheelhouse. "
                      "Do not disable TLS verification.")
        elif "CERTIFICATE_VERIFY_FAILED" in diagnostic:
            category = "tls-certificate-verification"
            action = ("Certificate verification failed; check OS trust or explicitly select an approved PEM CA bundle "
                      "with --ca-bundle. Do not disable TLS verification.")
        raise InstallError(f"{label} failed (exit {process.returncode}). {action} "
                           "No host was enabled. Retained new paths are incomplete; use fresh paths to retry.",
                           category=category)
    return process.stdout


PROBE = """
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import mcp_openapi_creator_kit as package
from mcp_openapi_creator_kit.assets import verify_assets
print(json.dumps({
    "kit": verify_assets(), "prefix": sys.prefix, "basePrefix": sys.base_prefix,
    "executable": sys.executable, "package": str(Path(package.__file__).absolute()),
    "python": list(sys.version_info[:3]),
    "distributions": dict(sorted((d.metadata["Name"], d.version) for d in metadata.distributions())),
}))
"""


def host_instructions(plugin: Path) -> dict:
    return {
        "activation": "Not performed. Review the plugin, then choose ONE supported host method.",
        "copilotCli": {"executable": "copilot", "arguments": ["plugin", "install", str(plugin)]},
        "vscodeSettings": {"chat.pluginLocations": {str(plugin): True}},
        "notes": [
            "Requires a Copilot CLI supporting local plugins or VS Code supporting chat.pluginLocations.",
            "Sign in to GitHub Copilot yourself before activation; host sign-in and host E2E are not performed by this installer.",
            "Merge only this entry; preserve unrelated settings. Enabling can start the pinned MCP.",
            "Disable the old bundle/duplicate standalone MCP before enabling a replacement.",
            "Select mcp-openapi-creator:mcp-openapi-creator in CLI, or the mcp-openapi-creator agent in VS Code.",
            "No Python dependency installation occurs at MCP startup.",
        ],
    }


def certificate_policy(ca_bundle: Path | None) -> dict:
    try:
        import ensurepip
    except ImportError as error:
        raise InstallError("This Python lacks ensurepip; explicitly install Python >=3.12 with venv/ensurepip.") from error
    version = ensurepip.version()
    match = re.match(r"(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (22, 2):
        raise InstallError("This Python's bundled pip lacks supported OS truststore; select Python with pip >=22.2.")
    result = {
        "verificationRequired": True, "osTruststore": True,
        "bundledPipVersion": version, "truststoreFeatureFlag": tuple(map(int, match.groups())) < (24, 2),
        "caBundle": None, "caBundleSha256": None, "inheritedCertificateOverrides": False,
    }
    if ca_bundle is not None:
        source = safe_path(ca_bundle)
        if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
            raise InstallError("--ca-bundle must be an existing PEM certificate bundle no larger than 2 MiB.")
        content = source.read_bytes()
        labels = re.findall(rb"-----BEGIN ([A-Z0-9 ]+)-----", content)
        if not labels or set(labels) != {b"CERTIFICATE"}:
            raise InstallError("--ca-bundle must contain only public PEM certificates, never a private key.")
        try:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=str(source))
        except ssl.SSLError as error:
            raise InstallError("--ca-bundle is not a valid PEM CA certificate bundle.") from error
        result.update(caBundle=str(source), caBundleSha256=hashlib.sha256(content).hexdigest())
    return result


def plan(args: argparse.Namespace) -> dict:
    if sys.version_info < (3, 12):
        raise InstallError("Explicitly invoke this bootstrap with Python >=3.12; no interpreter fallback is used.")
    wheel = safe_path(args.wheel)
    workspace = safe_path(args.workspace)
    if not workspace.is_dir():
        raise InstallError("Create the exact customer workspace explicitly first (an empty directory is supported).")
    installation = new_directory(args.install_dir)
    plugin = new_directory(args.plugin_dir)
    disjoint(workspace, installation, plugin)
    for output in (installation, plugin):
        for ancestor in output.parents:
            if (ancestor / "pyvenv.cfg").exists():
                raise InstallError("New outputs must not be inside an existing runtime; upgrades are side-by-side.")
            if (ancestor / "plugin.json").is_file() and (ancestor / ".mcp.json").is_file():
                raise InstallError("New outputs must not be inside an existing plugin export; upgrades are side-by-side.")
        for prefix in {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}:
            disjoint(output, prefix)
    disjoint(installation, wheel)
    disjoint(plugin, wheel)
    identity = wheel_identity(wheel, expected_hash(args, wheel))
    trust = certificate_policy(args.ca_bundle)
    if trust["caBundle"]:
        disjoint(installation, Path(trust["caBundle"]))
        disjoint(plugin, Path(trust["caBundle"]))
    wheelhouse = safe_path(args.wheelhouse) if args.wheelhouse else None
    dependency_wheels = {}
    if wheelhouse:
        if not wheelhouse.is_dir():
            raise InstallError("The dependency wheelhouse must be an existing local directory.")
        for entry in sorted(wheelhouse.iterdir()):
            safe_path(entry)
            if entry.is_file() and entry.suffix == ".whl":
                with zipfile.ZipFile(entry) as archive:
                    metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
                    if len(metadata) != 1:
                        raise InstallError("Each dependency wheel must have exactly one distribution.")
                    no_direct_dependencies(BytesParser().parsebytes(archive.read(metadata[0])))
                dependency_wheels[entry.name] = digest(entry)
        disjoint(installation, wheelhouse)
        disjoint(plugin, wheelhouse)
    if args.offline and not wheelhouse:
        raise InstallError("--offline requires --wheelhouse containing all dependency wheels.")
    python = installation / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    staged_wheel = installation / ".bootstrap" / wheel.name
    pip = [str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
           "--no-input", "--no-cache-dir", "install", "--only-binary=:all:"]
    if trust["truststoreFeatureFlag"]:
        pip.append("--use-feature=truststore")
    if trust["caBundle"]:
        pip.extend(["--cert", str(installation / ".bootstrap" / "ca-bundle.pem")])
    if args.offline:
        pip.append("--no-index")
    else:
        pip.extend(["--index-url", "https://pypi.org/simple"])
    if wheelhouse:
        pip.extend(["--find-links", str(installation / ".bootstrap" / "dependencies")])
    pip.append(str(staged_wheel))
    export = [str(python), "-I", "-m", f"{PACKAGE}.cli", "--workspace", str(workspace),
              "plugin-export", "--output", str(plugin), "--write"]
    return {
        "status": "preview", "writes": False, "hostActivated": False, "release": identity,
        "bootstrapPython": str(Path(sys.executable).absolute()),
        "installDirectory": str(installation), "pluginDirectory": str(plugin),
        "workspace": str(workspace), "wheel": str(wheel),
        "wheelhouse": str(wheelhouse) if wheelhouse else None, "dependencyWheels": dependency_wheels,
        "certificateTrust": trust,
        "dependencyPolicy": {
            "pipNoIndex": args.offline, "binaryWheelsOnly": True,
            "indexUrl": None if args.offline else "https://pypi.org/simple",
            "localWheelhouse": str(wheelhouse) if wheelhouse else None,
            "localWheelCount": len(dependency_wheels),
            "localWheelsSnapshottedAndHashed": bool(wheelhouse),
            "localWheelhousePublisherVerified": False,
            "directUrlDependenciesAllowed": False, "inheritedPipConfiguration": False,
        },
        "commands": {
            "createVenv": [str(Path(sys.executable).absolute()), "-I", "-m", "venv", "--copies", str(installation)],
            "installWheel": pip, "exportPlugin": export,
        },
        "dependencySource": "local wheelhouse only" if args.offline else "PyPI (and optional local wheelhouse)",
        "host": host_instructions(plugin),
        "next": "Review this plan and the trusted wheel checksum; repeat with --apply to install. No customer files change.",
    }


def verify_probe(result: dict, expected: dict) -> None:
    installation = Path(expected["installDirectory"])
    python = expected["commands"]["installWheel"][0]
    kit = result["kit"]
    distributions = {re.sub(r"[-_.]+", "-", name).lower(): version
                     for name, version in result["distributions"].items()}
    if (kit.get("source") != "installed-package" or kit.get("version") != expected["release"]["version"]
            or kit.get("manifestSha256") != expected["release"]["manifestSha256"]
            or not kit.get("verifiedFiles")
            or Path(result["prefix"]).resolve() != installation
            or result["prefix"] == result["basePrefix"]
            or os.path.normcase(result["executable"]) != os.path.normcase(python)
            or not Path(result["package"]).resolve().is_relative_to(installation)
            or distributions.get(DISTRIBUTION) != expected["release"]["version"]
            or tuple(result["python"]) < (3, 12)):
        raise InstallError("Installed provenance/interpreter does not match the supplied wheel and dedicated runtime.")


def verify_plugin(result: dict, probe: dict) -> None:
    plugin = safe_path(result["pluginDirectory"])
    connection = json.loads(safe_path(plugin / "connection.json").read_text("utf-8"))
    servers = json.loads(safe_path(plugin / ".mcp.json").read_text("utf-8"))["mcpServers"]
    python = result["commands"]["installWheel"][0]
    workspace = result["workspace"]
    if (connection.get("kit") != probe["kit"] or connection.get("workspace") != workspace
            or connection.get("interpreter") != python
            or connection.get("cliPrefix") != [python, "-I", "-m", f"{PACKAGE}.cli", "--workspace", workspace]
            or servers != {PLUGIN: {"command": python, "args": ["-I", "-m", PACKAGE, "--workspace", workspace]}}):
        raise InstallError("Exported plugin is not pinned to the verified installation and customer workspace.")
    for relative, expected in connection.get("guidanceSha256", {}).items():
        target = safe_path(plugin / relative)
        if not target.is_relative_to(plugin) or digest(target) != expected:
            raise InstallError("Exported guidance identity mismatch.")
    required = {f"skills/create-mcp/references/{name}" for name in
                ("AGENTS.md", "skills/discovery.md", "skills/onboarding.md", "skills/lifecycle.md")}
    if set(connection.get("guidanceSha256", {})) != required:
        raise InstallError("Exported plugin has incomplete guidance provenance.")


def install(args: argparse.Namespace) -> dict:
    result = plan(args)
    if not args.apply:
        return result
    installation = new_directory(result["installDirectory"])
    new_directory(result["pluginDirectory"])
    installation.mkdir(mode=0o700)
    marker = installation / "INSTALLATION-INCOMPLETE"
    marker.write_text("Not ready. Do not enable a plugin unless installation.json exists.\n", encoding="utf-8")
    inputs = installation / ".bootstrap"
    inputs.mkdir()
    wheel = safe_path(result["wheel"])
    staged_wheel = inputs / wheel.name
    with wheel.open("rb") as source, staged_wheel.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    if digest(staged_wheel) != result["release"]["wheelSha256"]:
        raise InstallError("Wheel changed after preview. Installation is incomplete; use new output paths.")
    if result["certificateTrust"]["caBundle"]:
        source = safe_path(result["certificateTrust"]["caBundle"])
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != result["certificateTrust"]["caBundleSha256"]:
            raise InstallError("CA bundle changed after preview; retry with fresh output paths.")
        with (inputs / "ca-bundle.pem").open("xb") as destination:
            destination.write(content)
    if result["wheelhouse"]:
        dependencies = inputs / "dependencies"
        dependencies.mkdir()
        for name, expected in result["dependencyWheels"].items():
            source = safe_path(Path(result["wheelhouse"]) / name)
            destination = dependencies / name
            with source.open("rb") as incoming, destination.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if digest(destination) != expected:
                raise InstallError("Dependency wheel changed after preview; retry with fresh output paths.")
    run(result["commands"]["createVenv"], cwd=installation.parent, label="Dedicated venv creation")
    safe_path(installation)
    python = safe_path(result["commands"]["installWheel"][0])
    if not (installation / "pyvenv.cfg").is_file() or not python.is_file():
        raise InstallError("The dedicated venv interpreter was not created.")
    run(result["commands"]["installWheel"], cwd=installation, label="Wheel/dependency installation")
    run([str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "check"],
        cwd=installation, label="Installed dependency validation")
    probe = json.loads(run([str(python), "-I", "-c", PROBE], cwd=installation, label="Installed asset verification"))
    verify_probe(probe, result)
    safe_path(result["workspace"])
    new_directory(result["pluginDirectory"])
    exported = json.loads(run(result["commands"]["exportPlugin"], cwd=installation, label="Plugin export"))
    if not exported.get("writes"):
        raise InstallError("Plugin export did not complete; no successful receipt was written.")
    verify_plugin(result, probe)
    receipt = {
        **result, "status": "installed", "writes": True, "runtime": probe,
        "bootstrapSha256": digest(Path(__file__)),
        "next": "Review the verified bundle and activate ONE host manually using the printed instructions.",
    }
    with (installation / "installation.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    marker.unlink()
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--wheel", required=True, type=Path, help="Absolute local path to the supplied kit wheel")
    hashes = result.add_mutually_exclusive_group(required=True)
    hashes.add_argument("--sha256", help="Trusted wheel SHA-256")
    hashes.add_argument("--sha256-file", type=Path, help="Absolute path to release SHA256SUMS")
    result.add_argument("--install-dir", required=True, type=Path, help="New dedicated venv path; parent must exist")
    result.add_argument("--plugin-dir", required=True, type=Path, help="New exported plugin path; parent must exist")
    result.add_argument("--workspace", required=True, type=Path, help="Existing customer directory, preserved unchanged")
    result.add_argument("--wheelhouse", type=Path, help="Optional absolute dependency-wheel directory")
    result.add_argument("--offline", action="store_true", help="Use only --wheelhouse; never contact an index")
    result.add_argument("--ca-bundle", type=Path,
                        help="Optional absolute, explicitly approved public PEM CA bundle; TLS verification stays enabled")
    result.add_argument("--apply", action="store_true", help="Explicitly install and export after reviewing the preview")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = install(args)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, zipfile.BadZipFile, KeyboardInterrupt) as error:
        print(json.dumps({"status": "failed", "hostActivated": False,
                          "category": getattr(error, "category", "installation"),
                          "error": str(error) or "Interrupted; any new output paths are incomplete.",
                          "notice": "No customer cleanup or host activation was performed. "
                                    "Do not enable incomplete outputs; preserve old installations and retry with new paths."}),
              file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
