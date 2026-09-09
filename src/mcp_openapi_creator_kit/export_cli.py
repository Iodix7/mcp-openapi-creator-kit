"""Explicit offline artifact writer; intentionally not exposed as an MCP tool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .consumer_export import (contained, inspect_targets, json_bytes,
                              load_client, preview_plan)
from .targets import TargetError


def write_artifacts(root: Path, client: str, artifacts: dict[str, bytes], *, namespace="targets") -> Path:
    if namespace not in ("targets", "pilot-ai-gateway"):
        raise TargetError("Unknown generated output namespace")
    output = contained(root, f"clients/{client}/generated/{namespace}")
    # Validate every destination before any writes; never follow generated symlinks.
    paths = {name: contained(root, f"clients/{client}/generated/{namespace}/{name}")
             for name in artifacts}
    for name in artifacts:
        lexical = root / "clients" / client / "generated" / namespace / name
        if any(p.is_symlink() or p.is_junction() for p in (lexical, *lexical.parents)
               if p.is_relative_to(root)):
            raise TargetError("Generated output cannot contain symlinks or junctions")
    for path in paths.values():
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise TargetError("Unsafe generated output destination")
        if path.exists() and path.stat().st_nlink != 1:
            raise TargetError("Generated output cannot overwrite a hard-linked file")
    output.mkdir(parents=True, exist_ok=True)
    # Clean only this generator's namespace, to avoid leaving stale plans.
    for old in output.iterdir():
        if old.is_symlink() or not old.is_file():
            raise TargetError("Unexpected entry in generated targets; remove it explicitly")
    for old in output.iterdir():
        if old.name not in artifacts:
            old.unlink()
    for name, path in paths.items():
        path.write_bytes(artifacts[name])
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline AI Gateway tier plans; never deploys")
    parser.add_argument("client", help="Client slug, not a path")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", action="store_true", help="Read-only compatibility/outputs preview")
    parser.add_argument("--apply", action="store_true", help="Always blocked; no verified management contract")
    args = parser.parse_args(argv)
    if args.apply:
        parser.exit(2, "BLOCKED: AI Gateway management resource paths/payloads are unverified; no modifying request was issued.\n")
    try:
        root = args.root.resolve()
        manifest, specs = load_client(root, args.client)
        if args.report:
            report = inspect_targets(root, manifest, specs)
            print(json_bytes(report).decode(), end="")
            return 1 if report["errors"] else 0
        artifacts = preview_plan(manifest, specs)
        output = write_artifacts(root, args.client, artifacts)
        print(json.dumps({"output": str(output), "files": sorted(artifacts), "liveVerified": False}))
        return 0
    except (TargetError, OSError, ValueError) as error:
        parser.exit(2, f"Export failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
