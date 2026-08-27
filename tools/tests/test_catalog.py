import importlib.util
import json
import sys
from pathlib import Path

import yaml

_TOOLS = Path(__file__).resolve().parent.parent
_REPO = _TOOLS.parent
sys.path.insert(0, str(_TOOLS))
_spec = importlib.util.spec_from_file_location("build_catalog", _TOOLS / "build-catalog.py")
catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog)


def test_catalog_is_deterministic_and_complete():
    first = catalog.build_index(_REPO, _REPO / "catalog" / "metadata.yaml")
    second = catalog.build_index(_REPO, _REPO / "catalog" / "metadata.yaml")

    assert first == second
    assert first["formatVersion"] == "1.0"
    assert len(first["profiles"]) == 3
    assert first["summary"]["scenarios"] == len(first["scenarios"])
    assert first["summary"]["operations"] == sum(
        len(item["operations"]) for item in first["scenarios"])
    assert all("generatedAt" not in key for key in first)
    assert all(item["sourceLanguage"] == "en" for item in first["scenarios"])


def test_policy_compatibility_is_measured_and_bounded():
    index = catalog.build_index(_REPO, _REPO / "catalog" / "metadata.yaml")

    for scenario in index["scenarios"]:
        compatibility = scenario["compatibility"]["policy-mcp-consumption"]
        assert compatibility["supported"], compatibility.get("reason")
        assert compatibility["servers"]
        assert all(server["sizeBytes"] <= catalog.POLICY_LIMIT_BYTES
                   for server in compatibility["servers"])


def test_editorial_metadata_overrides_are_optional(tmp_path):
    metadata = tmp_path / "metadata.yaml"
    metadata.write_text(yaml.safe_dump({"contracts": {"customer-care": {
        "domain": "Customer Service", "tags": ["service"],
        "scenario": {
            "persona": {"it": "Operatore", "en": "Agent"},
            "jobToBeDone": {"it": "Gestire un caso", "en": "Handle a case"},
        },
    }}}), encoding="utf-8")

    index = catalog.build_index(_REPO, metadata)
    customer_care = next(
        item for item in index["scenarios"] if item["id"] == "customer-care")

    assert customer_care["domain"] == "Customer Service"
    assert customer_care["tags"] == ["service"]
    assert customer_care["persona"]["en"] == "Agent"
    assert not any(warning["contract"] == "customer-care"
                   for warning in index["warnings"])


def test_html_is_self_contained_themed_and_sanitized(tmp_path):
    output = tmp_path / "generated"
    catalog.write_outputs(_REPO, output, _REPO / "catalog" / "metadata.yaml")
    html = (output / "catalog.html").read_text(encoding="utf-8")
    data = json.loads((output / "catalog.json").read_text(encoding="utf-8"))

    assert "__CATALOG_DATA__" not in html
    assert "window.CATALOG=" in html
    assert "--cp-bg: #f7f4ef" in html
    assert 'data-theme="dark"' in html
    assert "Capability catalog" in html
    assert "https://" not in html.split("window.CATALOG=", 1)[0]
    assert data["summary"]["scenarios"] > 0
    assert b"\r\n" not in (output / "catalog.json").read_bytes()
    assert b"\r\n" not in (output / "catalog.html").read_bytes()


def test_safe_script_json_escapes_closing_script():
    encoded = catalog.safe_script_json({
        "lower": "</script>",
        "mixed": "</ScRiPt><script>alert(1)</script>",
    })
    assert "<" not in encoded
    assert "\\u003c/script>" in encoded
    assert "\\u003c/ScRiPt>" in encoded


def test_builder_has_no_fixture_dependency():
    source = (_TOOLS / "build-catalog.py").read_text(encoding="utf-8").lower()
    for fixture in ("customer-care", "novaretail"):
        assert fixture not in source
