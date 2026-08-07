#!/usr/bin/env python3
"""Validate platform-wide constraints before an Azure deployment."""
import argparse
import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = {"native-mcp", "rest-consumption", "policy-mcp-consumption"}


def fail(message: str):
    print(f"[validate-profile] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate(profile: str, manifest_paths: list[Path], report_only: bool = False):
    if profile not in PROFILES:
        fail(f"GATEWAY_PROFILE '{profile}' invalid; use native-mcp | "
             "rest-consumption | policy-mcp-consumption")

    if profile == "native-mcp":
        print("[validate-profile] native-mcp: valid profile")
        return

    violations = []
    for path in manifest_paths:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        client = manifest.get("client", path.parent.name)
        network = manifest.get("networkProfile", "public")
        if network != "public":
            violations.append(
                f"{client}: networkProfile={network}; Consumption requires public")
        for api in manifest.get("apis") or []:
            backend_mode = (api.get("backend") or {}).get("mode")
            if backend_mode != "mock":
                violations.append(
                    f"{client}/{api.get('name', '?')}: backend.mode={backend_mode}; "
                    f"{profile} supports mock backend only")

    if violations:
        message = (f"{profile} is not deployable:\n  - " + "\n  - ".join(violations)
                   + "\nUse mock/public backend or GATEWAY_PROFILE=native-mcp.")
        if report_only:
            print(f"[validate-profile] REPORT: {message}")
            return violations
        fail(message)
    print(f"[validate-profile] {profile}: {len(manifest_paths)} manifest "
          "valid (REST mock, public network, zero backend compute)")
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="client directory or manifest path")
    parser.add_argument("--profile", default=os.environ.get("GATEWAY_PROFILE",
                                                            "native-mcp"))
    parser.add_argument("--report-only", action="store_true",
                        help="print incompatibilities without failing CI")
    args = parser.parse_args()

    if args.paths:
        manifest_paths = []
        for raw in args.paths:
            path = (REPO_ROOT / raw).resolve()
            manifest_paths.append(path / "mcp-manifest.yaml" if path.is_dir() else path)
    else:
        manifest_paths = sorted((REPO_ROOT / "clients").glob("*/mcp-manifest.yaml"))
    missing = [str(path) for path in manifest_paths if not path.exists()]
    if missing:
        fail(f"manifests not found: {missing}")
    validate(args.profile, manifest_paths, report_only=args.report_only)


if __name__ == "__main__":
    main()