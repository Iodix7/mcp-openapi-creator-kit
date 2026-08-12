import importlib.util
from pathlib import Path


_TOOLS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_publication", _TOOLS / "check-publication.py")
publication = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publication)


def test_clean_public_files_pass(tmp_path):
    readme = tmp_path / "README.md"
    guide = tmp_path / "docs" / "guide.md"
    guide.parent.mkdir()
    guide.write_text("# Guide\n", encoding="utf-8")
    readme.write_text("[Guide](docs/guide.md)\n", encoding="utf-8")

    assert publication.scan_repository(tmp_path, [readme, guide]) == []


def test_publication_findings_cover_release_gates(tmp_path):
    generated = tmp_path / "clients" / "demo" / "generated" / "client.bicep"
    generated.parent.mkdir(parents=True)
    generated.write_text("// generated\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        "NovaRetail [missing](docs/missing.md) C:\\Users\\person\\private.txt\n",
        encoding="utf-8")
    key = tmp_path / "config.yaml"
    key.write_text(
        "Account" + "Key=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n", encoding="utf-8")

    findings = publication.scan_repository(tmp_path, [generated, readme, key])

    assert any("generated artifact" in finding for finding in findings)
    assert any("retired branding" in finding for finding in findings)
    assert any("private path" in finding for finding in findings)
    assert any("broken local link" in finding for finding in findings)
    assert any("secret pattern" in finding for finding in findings)