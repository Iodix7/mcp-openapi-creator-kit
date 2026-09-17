"""Offline, path-bound plugin exports; no host installation or cloud access."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

import pytest
import yaml

from mcp_openapi_creator_kit import __version__
from mcp_openapi_creator_kit.assets import kit_root, verify_assets
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.plugin import export_plugin


NAME = "mcp-openapi-creator"
SKILL = "skills/create-mcp/SKILL.md"
AGENT = "agents/mcp-openapi-creator.agent.md"
GUIDANCE = ("AGENTS.md", "skills/discovery.md", "skills/onboarding.md", "skills/lifecycle.md")
FILES = sorted([
    "plugin.json", ".mcp.json", "connection.json", "README.md", SKILL, AGENT,
    *(f"skills/create-mcp/references/{name}" for name in GUIDANCE),
])


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def json_file(root, name):
    return json.loads((root / name).read_text("utf-8"))


def frontmatter(path):
    text = path.read_text("utf-8")
    assert text.startswith("---\n"), path
    parts = text.split("---\n", 2)
    assert len(parts) == 3, path
    metadata = yaml.safe_load(parts[1])
    assert isinstance(metadata, dict), path
    assert isinstance(metadata["description"], str) and len(metadata["description"]) > 30
    return metadata, parts[2]


@pytest.fixture
def customer(tmp_path):
    root = tmp_path / "customer"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def no_exporter_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Plugin export must not start a process, installer or network request")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)


def test_preview_is_read_only_and_lists_exact_sorted_files(customer, monkeypatch):
    output = customer.parent / "plugin"
    before = snapshot(customer.parent)

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not write or stage files")

    with monkeypatch.context() as readonly:
        for method in ("mkdir", "write_bytes", "write_text", "touch", "rename", "replace"):
            readonly.setattr(Path, method, forbidden)
        result = export_plugin(customer, output)
    assert snapshot(customer.parent) == before
    assert not output.exists()
    assert result["plugin"] == {
        "name": NAME, "version": __version__, "format": "copilot",
    }
    assert result["workspace"] == str(customer.resolve())
    assert result["output"] == str(output.resolve())
    assert result["kit"] == verify_assets()
    assert result["writes"] is False
    assert result["files"] == FILES
    assert all("\\" not in name and not Path(name).is_absolute() for name in result["files"])
    installation = result["installation"]
    assert installation["copilotCli"] == {
        "executable": "copilot", "arguments": ["plugin", "install", str(output.resolve())],
    }
    assert installation["vscodeSettings"] == {
        "chat.pluginLocations": {str(output.resolve()): True},
    }
    assert result.get("notes") or result.get("notice")


def test_write_matches_preview_and_supported_copilot_layout(customer):
    output = customer.parent / "plugin"
    preview = export_plugin(customer, output)
    written = export_plugin(customer, output, write=True)
    assert written == {**preview, "writes": True}
    assert sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()) == FILES
    assert list(customer.iterdir()) == []
    manifest = json_file(output, "plugin.json")
    assert "$schema" not in manifest
    assert manifest["name"] == NAME and manifest["version"] == __version__
    assert manifest["description"] and manifest["license"] == "MIT"
    assert manifest["skills"] == ["skills/"]
    assert manifest["agents"] == "agents/"
    assert manifest["mcpServers"] == ".mcp.json"
    assert "hooks" not in manifest
    mcp = json_file(output, ".mcp.json")
    assert "$schema" not in mcp
    assert set(mcp["mcpServers"]) == {NAME}
    server = mcp["mcpServers"][NAME]
    assert server.get("type", "stdio") == "stdio"
    assert server["command"] == str(Path(sys.executable).absolute())
    assert server["args"] == ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", str(customer.resolve())]
    connection = json_file(output, "connection.json")
    assert connection["kit"] == written["kit"]
    assert connection["workspace"] == str(customer.resolve())
    assert connection["interpreter"] == server["command"]
    assert connection["cliPrefix"] == [
        server["command"], "-I", "-m", "mcp_openapi_creator_kit.cli",
        "--workspace", str(customer.resolve()),
    ]


def test_native_skill_agent_and_their_relative_links(customer):
    output = customer.parent / "plugin"
    export_plugin(customer, output, write=True)
    skill, skill_body = frontmatter(output / SKILL)
    agent, agent_body = frontmatter(output / AGENT)
    assert skill["name"] == "create-mcp"
    assert agent["name"] == NAME
    # Tool names vary between hosts; inheritance, not an allowlist, is the contract.
    assert "tools" not in agent
    assert "../skills/create-mcp/SKILL.md" in agent_body
    for filename, body in ((SKILL, skill_body), (AGENT, agent_body)):
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", body)
        assert links, filename
        for link in links:
            if "://" in link or link.startswith("#"):
                continue
            target = (output / filename).parent / link.split("#", 1)[0]
            assert target.resolve().is_relative_to(output.resolve()), (filename, link)
            assert target.is_file(), (filename, link)
    for term in ("discovery", "onboarding", "lifecycle", "workflow-status", "nextInvocation",
                 "spec-sync", "dashboard", "connection.json"):
        assert term in skill_body
    assert re.search(r"recover|retry", skill_body, re.IGNORECASE)
    readme = (output / "README.md").read_text("utf-8")
    for term in ("per-installation", "chat.pluginLocations", "copilot plugin install",
                 "standalone", "Python", "Agent Plugins 1.0", "absolute", ".mcp.json"):
        assert term.lower() in readme.lower()


def test_guidance_copies_are_exact_trusted_assets_not_customer_overrides(customer):
    for name in GUIDANCE:
        local = customer / name
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text("MALICIOUS_CUSTOMER_OVERRIDE", encoding="utf-8")
    output = customer.parent / "plugin"
    before = snapshot(customer)
    export_plugin(customer, output, write=True)
    assert snapshot(customer) == before
    for name in GUIDANCE:
        assert (output / "skills/create-mcp/references" / name).read_bytes() == (kit_root() / name).read_bytes()
    assert json_file(output, "connection.json")["guidanceSha256"] == {
        f"skills/create-mcp/references/{name}": hashlib.sha256((kit_root() / name).read_bytes()).hexdigest()
        for name in GUIDANCE
    }
    assert all(b"MALICIOUS_CUSTOMER_OVERRIDE" not in p.read_bytes()
               for p in output.rglob("*") if p.is_file())


def test_pins_interpreter_and_customer_despite_unrelated_cwd(customer, monkeypatch):
    unrelated = customer.parent / "unrelated"
    unrelated.mkdir()
    (unrelated / "AGENTS.md").write_text("WRONG_WORKSPACE", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    output = customer.parent / "plugin"
    export_plugin(customer, output, write=True)
    server = json_file(output, ".mcp.json")["mcpServers"][NAME]
    assert Path(server["command"]).is_absolute()
    assert server["command"] == sys.executable
    assert server["args"] == ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", str(customer.resolve())]
    assert "cwd" not in server or server["cwd"] == str(customer.resolve())
    assert json_file(output, "connection.json")["workspace"] == str(customer.resolve())
    assert list(customer.iterdir()) == []


def test_exports_to_distinct_paths_have_identical_bytes(customer):
    first = customer.parent / "first-plugin"
    second = customer.parent / "second-plugin"
    export_plugin(customer, first, write=True)
    export_plugin(customer, second, write=True)
    assert snapshot(first) == snapshot(second)
    for path in first.rglob("*"):
        if path.is_file():
            text = path.read_text("utf-8")
            for output in (first, second):
                assert str(output) not in text
                assert json.dumps(str(output))[1:-1] not in text


def test_failed_staging_leaves_no_partial_output_or_scratch(customer, monkeypatch):
    output = customer.parent / "plugin"
    before = snapshot(customer.parent)
    original_write = Path.write_bytes
    writes = 0

    def fail_during_staging(path, content):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("Simulated storage failure")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_during_staging)
    with pytest.raises(OSError, match="Simulated storage failure"):
        export_plugin(customer, output, write=True)
    assert writes == 2 and not output.exists()
    assert snapshot(customer.parent) == before


def test_output_created_during_staging_is_preserved(customer, monkeypatch):
    output = customer.parent / "plugin"
    original_write = Path.write_bytes
    created = False

    def competing_output(path, content):
        nonlocal created
        if not created:
            created = True
            output.mkdir()
            original_write(output / "sentinel", b"preserve competing writer")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", competing_output)
    with pytest.raises(ValueError, match="(?i)exist|new"):
        export_plugin(customer, output, write=True)
    assert snapshot(output) == {"sentinel": b"preserve competing writer"}
    assert {p.name for p in customer.parent.iterdir()} == {"customer", "plugin"}
    assert list(customer.iterdir()) == []


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("kind", ["empty", "file", "populated", "identical"])
def test_existing_output_is_never_reused(customer, kind, write):
    output = customer.parent / "plugin"
    if kind == "identical":
        export_plugin(customer, output, write=True)
    elif kind == "file":
        output.write_text("preserve", encoding="utf-8")
    else:
        output.mkdir()
        if kind == "populated":
            (output / "unrelated").write_bytes(b"preserve")
    before = snapshot(customer.parent)
    with pytest.raises(ValueError, match="(?i)exist|new"):
        export_plugin(customer, output, write=write)
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("protected", ["customer", "assets", "installation"])
def test_protected_output_tree_rejected_before_any_write(customer, protected, write):
    parent = {
        "customer": customer, "assets": kit_root(), "installation": Path(sys.prefix),
    }[protected]
    output = parent / ("rejected-plugin-" + customer.parent.name)
    assert not output.exists()
    before = snapshot(customer.parent)
    with pytest.raises(ValueError, match="(?i)overlap|customer|kit|install"):
        export_plugin(customer, output, write=write)
    assert not output.exists()
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("write", [False, True])
def test_customer_ancestor_cannot_be_output(customer, write):
    before = snapshot(customer.parent)
    with pytest.raises(ValueError, match="(?i)exist|overlap"):
        export_plugin(customer, customer.parent, write=write)
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("kind", ["missing", "file"])
def test_output_parent_must_be_existing_directory(customer, kind, write):
    parent = customer.parent / "invalid-parent"
    if kind == "file":
        parent.write_text("preserve", encoding="utf-8")
    before = snapshot(customer.parent)
    with pytest.raises(ValueError, match="(?i)parent|directory"):
        export_plugin(customer, parent / "plugin", write=write)
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("kind", ["leaf", "parent", "dangling", "dotdot", "junction"])
def test_symlink_or_junction_output_rejected(customer, kind, write):
    destination = customer.parent / "destination"
    destination.mkdir()
    link = customer.parent / "linked"
    try:
        if kind == "junction":
            if os.name != "nt":
                pytest.skip("Windows junctions only")
            import _winapi
            if not hasattr(_winapi, "CreateJunction"):
                pytest.skip("Junction creation is unavailable")
            _winapi.CreateJunction(str(destination), str(link))
        else:
            target = destination / "missing" if kind == "dangling" else destination
            link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Link creation unsupported: {error}")
    output = link if kind in {"leaf", "dangling"} else link / "plugin"
    if kind == "dotdot":
        output = link / ".." / "plugin"
    before = snapshot(destination)
    try:
        with pytest.raises(ValueError, match="(?i)symlink|junction"):
            export_plugin(customer, output, write=write)
        assert snapshot(destination) == before
        assert not (customer.parent / "plugin").exists()
    finally:
        if kind == "junction":
            link.rmdir()
        else:
            link.unlink()


def test_cli_preview_write_and_standalone_vscode_config(customer, capsys):
    output = customer.parent / "plugin"
    args = ["--workspace", str(customer), "plugin-export", "--output", str(output)]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["writes"] is False and not output.exists()
    assert main(args + ["--write"]) == 0
    assert json.loads(capsys.readouterr().out) == {**preview, "writes": True}
    before = snapshot(customer.parent)
    assert main(["--workspace", str(customer), "vscode-config"]) == 0
    assert json.loads(capsys.readouterr().out) == {"servers": {NAME: {
        "type": "stdio", "command": str(Path(sys.executable).absolute()),
        "args": ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", str(customer.resolve())],
    }}}
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("kind", ["missing-output", "unknown-flag", "existing", "parent", "overlap", "workspace"])
@pytest.mark.parametrize("write", [False, True])
def test_cli_reports_invalid_export_without_traceback_or_writes(customer, capsys, kind, write):
    output = customer.parent / "plugin"
    root = customer
    if kind == "existing":
        output.mkdir()
    elif kind == "parent":
        output = customer.parent / "missing-parent" / "plugin"
    elif kind == "overlap":
        output = customer / "plugin"
    elif kind == "workspace":
        root = customer.parent / "missing-customer"
    args = ["--workspace", str(root), "plugin-export"]
    if kind != "missing-output":
        args.extend(["--output", str(output)])
    if kind == "unknown-flag":
        args.append("--apply")
    if write:
        args.append("--write")
    before = snapshot(customer.parent)
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.err and "Traceback" not in captured.err
    assert snapshot(customer.parent) == before
