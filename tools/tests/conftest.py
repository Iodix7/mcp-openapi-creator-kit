from pathlib import Path
import shutil

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mcp_workspace(tmp_path):
    """A fixed neutral workspace, independent of the developer's client list."""
    for name in ("AGENTS.md", "skills", "catalog"):
        source = ROOT / name
        destination = tmp_path / name
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("generated", "__pycache__"))
        else:
            shutil.copyfile(source, destination)
    (tmp_path / "apis").mkdir()
    shutil.copytree(ROOT / "apis" / "customer-care", tmp_path / "apis" / "customer-care")
    shutil.copyfile(ROOT / "apis" / "canonical-schemas.yaml", tmp_path / "apis" / "canonical-schemas.yaml")
    client = tmp_path / "clients" / "fixture"
    client.mkdir(parents=True)
    (client / "mcp-manifest.yaml").write_text(yaml.safe_dump({
        "client": "fixture", "displayName": "Fixture",
        "mcpExposure": {"mode": "facade", "facadeName": "agent"},
        "apis": [{"name": "customer-care", "displayName": "Care",
                  "backend": {"mode": "mock"}, "mcpTools": ["get-customer-context"]}],
    }), encoding="utf-8")
    return tmp_path
