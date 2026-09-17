"""Project declared scenario metadata, not inferred meaning, from the specification."""
from __future__ import annotations

from pathlib import Path
import re

import yaml

from .data_paths import safe_data_path

MAX_SPEC_BYTES = 128 * 1024
MAX_HEADER_BYTES = 16 * 1024
FIELDS = ("title", "persona", "jobToBeDone", "outcome")


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("Scenario frontmatter requires unique string keys")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_spec_metadata(text: str) -> dict:
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].rstrip(" \t") != "---":
        return {}
    end = next((index for index in range(1, len(lines))
                if lines[index].rstrip(" \t") in {"---", "..."}), None)
    header = "\n".join(lines[1:end])
    if not re.search(r"""^[ \t]*(?:catalog|"catalog"|'catalog')\s*:""", header, re.MULTILINE):
        return {}
    if end is None or len(header.encode("utf-8")) > MAX_HEADER_BYTES:
        raise ValueError("Close the scenario catalog frontmatter within the 16 KiB limit")
    try:
        if any(isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(header)):
            raise ValueError("Scenario catalog frontmatter must not use YAML aliases or anchors")
        value = yaml.load(header, Loader=_UniqueLoader)
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError("Invalid YAML in scenario catalog frontmatter; inspect the specification header") from error
    catalog = value.get("catalog") if isinstance(value, dict) else None
    if not isinstance(catalog, dict) or not catalog or set(catalog) - set(FIELDS):
        raise ValueError("Scenario catalog must declare title, persona, jobToBeDone or outcome")
    result = {}
    for field, translations in catalog.items():
        if isinstance(translations, str):
            translations = {"source": translations}
        if not isinstance(translations, dict) or not translations:
            raise ValueError(f"Scenario catalog {field} must be text or a nonempty language mapping")
        clean = {}
        for language, content in translations.items():
            if language not in {"source", "en", "it"}:
                raise ValueError(f"Scenario catalog {field} supports en, it and source language keys")
            if not isinstance(content, str) or not content.strip() or len(content) > 2000:
                raise ValueError(f"Scenario catalog {field} text must contain 1-2000 characters")
            if re.fullmatch(r"<[^<>]+>", content.strip()):
                raise ValueError(f"Complete the scenario catalog {field} placeholder before preparation")
            clean[language] = content.strip()
        result[field] = clean
    return result


def read_spec_metadata(root: Path, client: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", client):
        raise ValueError("Scenario metadata requires a client slug")
    path = safe_data_path(root, root / "docs" / client / "spec.md")
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as stream:
        if stream.readline(MAX_HEADER_BYTES + 1).removeprefix("\ufeff").strip() != "---":
            return {}
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise ValueError("Specification exceeds the 128 KiB checking limit")
    return parse_spec_metadata(path.read_text(encoding="utf-8"))
