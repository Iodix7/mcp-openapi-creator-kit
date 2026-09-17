"""Opt-in fictional scenario library; no startup activation or cloud operations."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import yaml

from .assets import kit_root
from .data_paths import safe_data_path, validate_data_tree, workspace_root
from .runtime import command


def library_root() -> Path:
    root = kit_root() / "examples" / "library"
    if not (root / "index.yaml").is_file():
        raise RuntimeError("Bundled example library is missing; reinstall a complete kit wheel.")
    return root


def list_examples() -> dict:
    """Return the packaged catalog, not customer clients or deployment targets."""
    root = library_root()
    index = yaml.safe_load((root / "index.yaml").read_text(encoding="utf-8"))
    if not isinstance(index, dict) or index.get("version") != 1:
        raise ValueError("Unsupported bundled example catalog")
    scenarios = index.get("scenarios")
    if not isinstance(scenarios, list) or len({s["id"] for s in scenarios}) != len(scenarios):
        raise ValueError("Invalid bundled scenario IDs")
    return {
        **index, "activeClients": [], "writes": False,
        "summary": {
            "scenarios": len(scenarios),
            "apis": len({name for item in scenarios for name in item["apis"]}),
            "tools": sum(item["tools"] for item in scenarios),
        },
    }


def builtin_catalog() -> dict:
    """Full original example catalog, never active customer/client records.

    Keep the established builtin source discriminator for dashboard consumers;
    explicit asset provenance distinguishes these contracts from the separate
    neutral import-sample starter, which also has a customer-care API ID.
    """
    from .catalog import build_index

    library = list_examples()
    index = build_index(library_root())
    expected = {name for item in library["scenarios"] for name in item["apis"]}
    if {item["id"] for item in index["scenarios"]} != expected:
        raise ValueError("Bundled example contracts disagree with the library catalog")
    index["clients"] = []
    index["workflow"] = []
    index["summary"]["clients"] = 0
    index["source"] = "builtin-starter-library"
    index["exampleLibrary"] = {**library, "assetRoot": "examples/library"}
    for scenario in index["scenarios"]:
        scenario["usedBy"] = []
        scenario["scenarioContexts"] = []
        scenario["sourceLanguage"] = library["sourceLanguage"]
        scenario["exampleSource"] = f"examples/library/apis/{scenario['id']}/openapi.yaml"
        scenario["exampleScenarioIds"] = [
            item["id"] for item in library["scenarios"] if scenario["id"] in item["apis"]
        ]
    return index


@dataclass(frozen=True)
class ExampleImportPlan:
    scenario: str
    client: str
    files: dict[Path, str]
    tool_map: dict[str, str]
    api_map: dict[str, str]

    def public(self, root: Path) -> dict:
        root = workspace_root(root)
        return {
            "scenario": self.scenario, "client": self.client, "writes": False,
            "files": [str(path.relative_to(root)) for path in sorted(self.files)],
            "apis": self.api_map, "tools": self.tool_map,
            "sampleActivated": False, "deployed": False,
            "next": [
                f"Review docs/{self.client}/example-reference.md and its tool map.",
                f"Write and review docs/{self.client}/spec.md, then preview mcp-kit spec-sync {self.client}.",
                "Choose the consumer/profile and explicitly prepare; importing is not preparation or approval.",
            ],
        }


def plan_import(root: Path, scenario: str, client: str) -> ExampleImportPlan:
    """Validate and plan a selected namespaced variant without writing anything.

    Contract/link/example handling deliberately delegates to prepare-variant.
    Historical narratives stay identifiable reference material, not an invented
    customer specification or automatically rewritten agent instructions.
    """
    root = workspace_root(root)
    validate_data_tree(root)
    records = list_examples()["scenarios"]
    selected = next((item for item in records if item["id"] == scenario), None)
    if selected is None:
        raise ValueError("Unknown example scenario; use examples to list explicit choices.")
    source = library_root()
    docs = safe_data_path(root, root / "docs" / client)
    if docs.exists():
        raise ValueError("Example documentation destination already exists; import never overwrites.")
    variant = command("prepare-variant")
    # prepare() validates destination/client/API/tool collisions and canonical
    # schemas against the entire workspace before returning a write plan.
    generator = command("build-facade")
    previous_root = generator.REPO_ROOT
    try:
        files = variant.prepare(root, selected["sourceClient"], client, source_root=source)
    finally:
        generator.REPO_ROOT = previous_root
    manifest = yaml.safe_load(files[root / "clients" / client / "mcp-manifest.yaml"])
    original = yaml.safe_load(
        (source / "clients" / selected["sourceClient"] / "mcp-manifest.yaml").read_text("utf-8"))
    if [api["name"] for api in original["apis"]] != selected["apis"]:
        raise ValueError("Bundled scenario catalog disagrees with its manifest")
    api_map = {old["name"]: new["name"] for old, new in zip(original["apis"], manifest["apis"], strict=True)}
    tool_map = {
        old: new for old_api, new_api in zip(original["apis"], manifest["apis"], strict=True)
        for old, new in zip(old_api["mcpTools"], new_api["mcpTools"], strict=True)
    }
    if len(tool_map) != selected["tools"]:
        raise ValueError("Bundled scenario tool count disagrees with its manifest")
    references = []
    for relative in selected["documents"]:
        path = safe_data_path(source, source / relative)
        if not path.is_file() or path.suffix != ".md" or path.parent != source / "docs" / "fsi":
            raise ValueError("Bundled narrative must be an allowlisted Markdown document")
        destination = docs / "library" / path.name
        files[destination] = path.read_text(encoding="utf-8")
        references.append(f"- [Original scenario reference: {path.name}](library/{path.name})")
    files[docs / "library" / "import-map.json"] = json.dumps({
        "scenario": scenario, "sourceClient": selected["sourceClient"], "client": client,
        "apis": api_map, "tools": tool_map,
    }, indent=2, ensure_ascii=False) + "\n"
    lines = [
        "# Imported fictional scenario reference", "",
        f"Selected library scenario: `{scenario}`. New local client: `{client}`.", "",
        "This is reference material, not an approved customer specification.",
        "No deployment, credentials, gateway choice or customer approval was imported.",
        "Mocks are deterministic examples, not persisted state or live business decisions.", "",
        "## Exact imported tool mapping", "",
        "| Reference tool | Imported tool |", "|---|---|",
        *(f"| `{old}` | `{new}` |" for old, new in tool_map.items()), "",
        "## API mapping", "",
        *(f"- `{old}` → `{new}`" for old, new in api_map.items()), "",
        "## Narrative sources", "",
        *references,
        "Contract descriptions, response examples and x-mock are the executable scenario source.",
        "Reference documents retain original names/dates; use import-map.json, not global text replacement.",
        "Adapt/review any agent instructions explicitly before configuring a consumer.",
        "Frozen demo dates are intentional; do not imply that KYC, balances or market data are current.", "",
        "## Next steps", "",
        f"Author/review `docs/{client}/spec.md` using the installed scenario template.",
        f"Use `mcp-kit spec-sync {client}` to preview current imported technical sections;",
        "apply with --write only after local-write approval. Do not copy legacy technical tables",
        "into the managed specification. Resolve all TO CLARIFY items before prepare.",
        "Then use workflow-status to choose and prepare the approved consumer/profile.", "",
    ]
    files[docs / "example-reference.md"] = "\n".join(lines)
    for path in files:
        safe_data_path(root, path)
        if path.exists():
            raise ValueError(f"Import destination already exists: {path.relative_to(root)}")
    return ExampleImportPlan(scenario, client, files, tool_map, api_map)


def apply_import(root: Path, plan: ExampleImportPlan) -> dict:
    """Revalidate the exact plan, then create only new data files, with rollback."""
    root = workspace_root(root)
    fresh = plan_import(root, plan.scenario, plan.client)
    if fresh != plan:
        raise ValueError("Example import plan changed; review a fresh plan before writing.")
    directories: list[Path] = []
    written: list[Path] = []
    try:
        for path, text in sorted(plan.files.items()):
            safe_data_path(root, path)
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                safe_data_path(root, directory)
                directory.mkdir()
                directories.append(directory)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                written.append(path)
                stream.write(text)
    except (OSError, ValueError):
        for path in reversed(written):
            path.unlink()
        for directory in reversed(directories):
            directory.rmdir()
        raise
    return {**plan.public(root), "writes": True}
