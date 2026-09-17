"""Release preparation is local, allowlisted and path-independent."""
import importlib.util
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_release_tests", ROOT / "tools/build-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    files = {
        "pyproject.toml": '[project]\nname="mcp-openapi-creator-kit"\nversion="1.2.3"\n',
        "MANIFEST.in": "include install-kit.py\n", "LICENSE": "MIT\n", "README.md": "kit\n",
        "install-kit.py": "print('installer')\n",
        "src/mcp_openapi_creator_kit/_build.py": 'ASSETS = ("AGENTS.md", "skills/*.md", "clients/sample/mcp-manifest.yaml")\nCOMMANDS = ()\n',
        "src/mcp_openapi_creator_kit/__init__.py": "",
        "AGENTS.md": "maintained guidance", "skills/discovery.md": "discover",
        "clients/sample/mcp-manifest.yaml": "client: sample",
        "clients/private/mcp-manifest.yaml": "PRIVATE CUSTOMER",
        ".azure/config.json": "PRIVATE ACCOUNT", ".env": "SECRET",
        ".venv/local.txt": "MACHINE PATH",
        "src/mcp_openapi_creator_kit/assets/stale.json": "STALE",
    }
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def test_preview_does_not_write_or_build_and_excludes_customer_state(source, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not execute a build")

    monkeypatch.setattr(release.installer.subprocess, "run", forbidden)
    output = tmp_path / "release"
    result = release.build(source, output)
    assert result["status"] == "preview" and not result["writes"] and not output.exists()
    assert "clients/sample/mcp-manifest.yaml" in result["sourceFiles"]
    assert not any(name.startswith(("clients/private", ".azure", ".env", ".venv")) for name in result["sourceFiles"])
    assert not any("assets/stale" in name for name in result["sourceFiles"])


def test_source_identity_does_not_depend_on_absolute_checkout_path(source, tmp_path):
    import shutil
    second = tmp_path / "another machine checkout"
    shutil.copytree(source, second)
    first = release.build(source, tmp_path / "out1")
    other = release.build(second, tmp_path / "out2")
    assert first["sourceContentSha256"] == other["sourceContentSha256"]
    assert first["sourceFiles"] == other["sourceFiles"]
    (second / "AGENTS.md").write_text("changed guidance", encoding="utf-8")
    changed = release.build(second, tmp_path / "out3")
    assert changed["sourceContentSha256"] != first["sourceContentSha256"]


def test_existing_artifact_output_refused(source, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(release.installer.InstallError, match="existing"):
        release.build(source, output)


def test_instructions_name_exact_wheel_and_keep_host_activation_manual():
    result = release.instructions("mcp_openapi_creator_kit-1.2.3-py3-none-any.whl", "1.2.3")
    assert "SHA256SUMS" in result and "--apply" in result
    assert "mcp_openapi_creator_kit-1.2.3-py3-none-any.whl" in result
    assert "ONE host" in result and "not host/platform acceptance evidence" in result
    assert "--wheelhouse" in result and "source checkout" in result


def test_sdist_normalization_has_stable_identity(tmp_path):
    import io
    files = []
    for index, timestamp in enumerate((1000000000, 2000000000)):
        path = tmp_path / f"{index}.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo("package/file.txt")
            member.uid = timestamp
            member.uname = f"user-{index}"
            member.mtime = timestamp
            member.size = 5
            archive.addfile(member, io.BytesIO(b"hello"))
        release.normalize_sdist(path)
        files.append(path.read_bytes())
    assert files[0] == files[1]


def test_build_failure_preserves_source_and_marks_only_new_output(source, tmp_path, monkeypatch):
    import subprocess
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    monkeypatch.setattr(release.installer.subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "failure"))
    output = tmp_path / "failed release"
    with pytest.raises(release.installer.InstallError, match="Build failed"):
        release.build(source, output, apply=True)
    assert (output / "RELEASE-INCOMPLETE").exists()
    assert not (output / "provenance.json").exists()
    assert not (output / "SHA256SUMS").exists()
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()} == before
