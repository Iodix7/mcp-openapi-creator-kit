#!/usr/bin/env python3
"""Build wheel/sdist and test a real clean installation outside the checkout."""
import argparse
import hashlib
import json
import locale
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True,
                        help="New persistent artifact directory OUTSIDE the checkout")
    parser.add_argument("--compile-bicep", action="store_true",
                        help="Use local az bicep build only; no Azure login/resource access")
    parser.add_argument("--wheel", type=Path,
                        help="Test this existing release wheel instead of rebuilding the release")
    parser.add_argument("--sdist", type=Path,
                        help="Matching release sdist; required with --wheel")
    parser.add_argument("--expected-sha256",
                        help="Expected release wheel SHA-256; requires --wheel")
    args = parser.parse_args()
    if bool(args.wheel) != bool(args.sdist):
        parser.error("--wheel and --sdist must be supplied together")
    if args.expected_sha256 and not args.wheel:
        parser.error("--expected-sha256 requires --wheel")
    if args.wheel:
        args.wheel, args.sdist = args.wheel.resolve(), args.sdist.resolve()
        if not args.wheel.is_file() or not args.sdist.is_file():
            parser.error("Both release artifacts must be existing files")
        if args.expected_sha256 and hashlib.sha256(args.wheel.read_bytes()).hexdigest() != args.expected_sha256.lower():
            parser.error("Release wheel SHA-256 does not match; no artifact was installed")
    source = Path(__file__).resolve().parent.parent
    artifacts = args.artifacts.resolve()
    if artifacts.is_relative_to(source) or artifacts.exists():
        parser.error("--artifacts must be a new directory outside the source checkout")
    artifacts.mkdir(parents=True)
    logs = artifacts / "logs"
    logs.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    counter = 0

    def run(command, cwd=source, *, stdout_only=False):
        nonlocal counter
        counter += 1
        process = subprocess.run(command, cwd=cwd, env=env,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE if stdout_only else subprocess.STDOUT)
        azure_command = Path(command[0]).name.lower() in {"az", "az.cmd", "az.exe"}
        encoding = locale.getencoding() if os.name == "nt" and azure_command else "utf-8"
        output = process.stdout.decode(encoding)
        diagnostics = process.stderr.decode(encoding) if stdout_only else ""
        log = logs / f"{counter:02d}.log"
        log.write_text(output + diagnostics, encoding="utf-8")
        if process.returncode:
            (artifacts / "result.json").write_text(json.dumps({
                "status": "failed", "exitCode": process.returncode, "log": str(log),
            }, indent=2), encoding="utf-8")
            print(output + diagnostics)
            raise RuntimeError(f"Smoke subprocess failed: {command[:3]}")
        return output

    dist = artifacts / "dist"
    if args.wheel:
        wheel, sdist = args.wheel, args.sdist
    else:
        run([sys.executable, "-X", "utf8", "-m", "build", "--wheel", "--sdist",
             "--outdir", str(dist)])
        wheel = next(dist.glob("*.whl"))
        sdist = next(dist.glob("*.tar.gz"))
    extracted = artifacts / "sdist-source"
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        archive.extractall(extracted, filter="data")
    sdist_root = next(extracted.iterdir())
    sdist_dist = artifacts / "sdist-dist"
    run([sys.executable, "-X", "utf8", "-m", "build", "--wheel",
         "--outdir", str(sdist_dist)], cwd=sdist_root)
    sdist_wheel = next(sdist_dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as direct, zipfile.ZipFile(sdist_wheel) as rebuilt:
        entry = "mcp_openapi_creator_kit/assets/manifest.json"
        assert direct.read(entry) == rebuilt.read(entry), "sdist asset provenance differs"
        for name in direct.namelist():
            if name.startswith("mcp_openapi_creator_kit/"):
                assert direct.read(name) == rebuilt.read(name), name
    venv = artifacts / "venv"
    run([sys.executable, "-X", "utf8", "-m", "venv", str(venv)])
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([str(python), "-X", "utf8", "-m", "pip", "install", "--quiet", str(wheel)])
    customer = artifacts / "customer"
    customer.mkdir()
    # Stage harness code outside the checkout; -c would eventually exceed the
    # Windows process argument limit. Runtime source access is still denied.
    probe = artifacts / "wheel_probe.py"
    fixture = artifacts / "offline_scenarios.py"
    shutil.copyfile(source / "tools/tests/wheel_probe.py", probe)
    shutil.copyfile(source / "tools/tests/offline_scenarios.py", fixture)
    output = run([str(python), "-I", "-X", "utf8", str(probe), str(source), str(fixture)], cwd=customer)
    result = json.loads(output.strip().splitlines()[-1])
    if args.compile_bicep:
        az = shutil.which("az")
        if not az:
            raise RuntimeError("Local az/Bicep compiler is required by --compile-bicep")
        for template in result["bicep"]:
            compiled = run([az, "bicep", "build", "--file", template, "--stdout"],
                           cwd=customer, stdout_only=True)
            assert '"resources"' in compiled
            if Path(template).parent == customer / "clients" / "fictional-sap-warehouse-demo" / "generated":
                compiled_path = artifacts / "long-client.compiled.json"
                compiled_path.write_text(compiled, encoding="utf-8")
                output = run([
                    str(python), "-I", "-X", "utf8", str(probe), str(source),
                    "--compiled-recovery", str(compiled_path),
                    str(Path(template).parent.parent / "mcp-manifest.yaml"),
                ], cwd=customer)
                result["compiledRecoveryPayloads"] = json.loads(output)["compiledRecoveryPayloads"]
        result["compiledBicep"] = len(result["bicep"])
    result["wheel"] = str(wheel)
    result["installedWheel"] = str(wheel)
    result["installedWheelSha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    result["existingReleaseArtifacts"] = bool(args.wheel)
    result["sdistWheel"] = str(sdist_wheel)
    result["sdistAssetHashesMatch"] = True
    result["status"] = "passed"
    (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
