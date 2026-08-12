#!/usr/bin/env python3
"""Check tracked files for public-release hygiene violations."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED_PATH = re.compile(
    r"^(clients/[^/]+/generated/|infra/[^/]+\.gen\.bicep$|catalog/generated/)")
PRIVATE_PATH = re.compile(
    r"(?i)(?:[a-z]:\\users\\[^\\\s]+|/users/[^/\s]+|/home/[^/\s]+|"
    r"mcp-agent-demokit)")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)AccountKey=[A-Za-z0-9+/=]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
RETIRED_BRANDING = (
    "novaretail", "fsi-demo", "fibercop", "mcp-agent-kit",
)
BRANDING_PATHS = ("apis/", "clients/", "catalog/", "docs/")
BRANDING_FILES = {"README.md", "HANDOVER.md"}
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
TEXT_SUFFIXES = {
    ".bicep", ".bicepparam", ".html", ".json", ".md", ".py", ".xml",
    ".yaml", ".yml",
}


def tracked_files(root: Path) -> list[Path]:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required in PATH")
    process = subprocess.run(
        [git, "-C", str(root), "ls-files", "-z"], capture_output=True,
        check=False)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode(errors="replace").strip())
    return [root / value.decode() for value in process.stdout.split(b"\0") if value]


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def scan_markdown_links(root: Path, path: Path, text: str) -> list[str]:
    findings = []
    for match in MARKDOWN_LINK.finditer(text):
        target = match.group(1).split("#", 1)[0].strip()
        if not target or re.match(r"^(?:https?://|mailto:)", target):
            continue
        target = target.split(maxsplit=1)[0].strip("<>")
        if not (path.parent / target).resolve().exists():
            findings.append(
                f"{relative(root, path)}: broken local link '{target}'")
    return findings


def scan_repository(root: Path, paths: list[Path]) -> list[str]:
    findings = []
    for path in paths:
        rel = relative(root, path)
        if GENERATED_PATH.search(rel):
            findings.append(f"{rel}: generated artifact is tracked")
        if rel.startswith(".azure/"):
            findings.append(f"{rel}: local azd environment state is tracked")
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if rel != "tools/check-publication.py":
            if match := PRIVATE_PATH.search(text):
                findings.append(f"{rel}: private path '{match.group(0)}'")
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(f"{rel}: high-confidence secret pattern")
                    break
        if rel in BRANDING_FILES or rel.startswith(BRANDING_PATHS):
            lowered = text.lower()
            for retired in RETIRED_BRANDING:
                if retired in lowered:
                    findings.append(f"{rel}: retired branding '{retired}'")
        if path.suffix.lower() == ".md":
            findings.extend(scan_markdown_links(root, path, text))
    return findings


def main():
    try:
        findings = scan_repository(REPO_ROOT, tracked_files(REPO_ROOT))
    except RuntimeError as error:
        print(f"[publication] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    if findings:
        for finding in findings:
            print(f"[publication] ERROR: {finding}", file=sys.stderr)
        raise SystemExit(1)
    print("[publication] tracked-file, secret, private-path, branding, and link checks passed")


if __name__ == "__main__":
    main()