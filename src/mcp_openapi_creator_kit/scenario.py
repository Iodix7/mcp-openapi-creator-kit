"""Deterministic checks of the scenario template's contract assertions, not prose approval."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from pydantic import Field

from .catalog import operation_record
from .consumer_export import json_bytes, load_client
from .data_paths import safe_data_path
from .policy import HTTP_VERBS
from .workflow import Record

MAX_SPEC_BYTES = 128 * 1024
BLOCK_START = "<!-- mcp-kit:contract-reference:start -->"
BLOCK_END = "<!-- mcp-kit:contract-reference:end -->"
NOTICE = ("Checks cover operation tables, declared response codes/example names and explicit "
          "Tool: references. They do not validate free-form prose, business semantics, execution "
          "of x-mock conditions or human approval. Review the storyline and actual examples.")


class ScenarioIssue(Record):
    line: int | None = None
    message: str


class ScenarioCheck(Record):
    status: Literal["missing", "mismatch", "consistent"]
    issues: list[ScenarioIssue] = Field(default_factory=list)
    notice: str = NOTICE


class SpecSyncConflict(ValueError):
    """Human-owned or ambiguous content cannot be replaced by the generator."""


@dataclass(frozen=True)
class SpecSyncPlan:
    client: str
    path: Path
    before: bytes | None
    after: bytes
    projection_hash: str

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def public(self, root: Path) -> dict:
        relative = self.path.relative_to(root).as_posix()
        return {
            "path": relative, "changed": self.changed, "writes": False,
            "action": "create" if self.before is None else "update" if self.changed else "unchanged",
            "projectionSha256": self.projection_hash, "narrativeReviewRequired": True,
            "diff": "".join(difflib.unified_diff(
                (self.before or b"").decode("utf-8").splitlines(keepends=True),
                self.after.decode("utf-8").splitlines(keepends=True),
                fromfile=relative, tofile=relative)),
        }


def inventory(root: Path, client: str) -> list[dict]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", client):
        raise ValueError("scenario-contract requires a client slug, not a path")
    safe_data_path(root, root / "clients" / client / "mcp-manifest.yaml")
    # Validate links before load_client resolves paths.
    from .data_paths import validate_data_tree
    validate_data_tree(root)
    manifest, specs = load_client(root, client)
    records = []
    for api in manifest["apis"]:
        name = api["name"]
        spec = specs[name]
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            raise ValueError(f"{name}: OpenAPI paths must be an object")
        selected = api.get("mcpTools", [])
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            raise ValueError(f"{name}: mcpTools must be a list of operation IDs")
        seen = set()
        for path, item in spec["paths"].items():
            if not isinstance(item, dict) or "$ref" in item:
                raise ValueError(f"{name}: expected inline OpenAPI path item")
            for method, operation in item.items():
                if method not in HTTP_VERBS:
                    continue
                opid = operation.get("operationId") if isinstance(operation, dict) else None
                if not isinstance(opid, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", opid):
                    raise ValueError(f"{name}: expected kebab-case operationId")
                if opid in seen:
                    raise ValueError(f"{name}: duplicate operationId {opid}")
                seen.add(opid)
                records.append({
                    "contract": name, "selected": opid in selected,
                    **operation_record(spec, path, method, operation, item.get("parameters", [])),
                })
        if set(selected) - seen:
            raise ValueError(f"{name}: selected tools are missing from its contract")
    return sorted(records, key=lambda item: (item["contract"], item["operationId"]))


def reference_markdown(records: list[dict]) -> str:
    """Only contract assertions; the operator/model still owns the scenario narrative."""
    lines = [
        "## Operations", "",
        "| Contract | operationId | Method and path |",
        "|---|---|---|",
    ]
    for item in records:
        route = f"{item['method']} {item['path']}".replace("|", r"\|")
        lines.append(f"| `{item['contract']}` | `{item['operationId']}` | "
                     f"`{route}` |")
    lines.extend(["", "## Mock behavior", "",
                  "| Contract | Operation | Request condition | Status/example |",
                  "|---|---|---|---|"])
    for item in records:
        for response in item["responses"]:
            lines.append(f"| `{item['contract']}` | `{item['operationId']}` | "
                         f"Review actual examples and x-mock | {response['status']} |")
    return "\n".join(lines) + "\n"


def projection_hash(records: list[dict]) -> str:
    return hashlib.sha256(json_bytes(records)).hexdigest()


def managed_reference(records: list[dict]) -> str:
    return (
        BLOCK_START + "\n"
        "Generated by mcp-kit spec-sync from the selected client's OpenAPI/manifest.\n"
        "Do not edit this block; edit source contracts and regenerate. Not human approval.\n"
        f"Contract projection SHA-256: {projection_hash(records)}\n\n"
        + reference_markdown(records) + BLOCK_END
    )


def managed_span(text: str) -> tuple[int, int] | None:
    if BLOCK_START not in text and BLOCK_END not in text:
        return None
    if text.count(BLOCK_START) != 1 or text.count(BLOCK_END) != 1:
        raise SpecSyncConflict("Malformed/duplicate spec-sync markers; restore exactly one complete managed block.")
    starts, ends = [], []
    offset = 0
    visible = dict(_lines(text))
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        value = line.rstrip("\r\n")
        if number in visible:
            if value == BLOCK_START:
                starts.append(offset)
            elif value == BLOCK_END:
                ends.append(offset + len(BLOCK_END))
        offset += len(line)
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise SpecSyncConflict("Spec-sync markers must be standalone ordered lines outside code fences.")
    return starts[0], ends[0]


def _operation_table(headers: list[str]) -> bool:
    return "operationid" in headers and (
        "method and path" in headers or {"method", "path"} <= set(headers))


def plan_spec_sync(root: Path, client: str, records: list[dict] | None = None) -> SpecSyncPlan:
    records = inventory(root, client) if records is None else records
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", client):
        raise ValueError("spec-sync requires a client slug, not a path")
    path = safe_data_path(root, root / "docs" / client / "spec.md")
    if path.exists() and not path.is_file():
        raise ValueError("Scenario specification must be a file")
    if path.is_file() and path.stat().st_size > MAX_SPEC_BYTES:
        raise SpecSyncConflict("Specification exceeds the 128 KiB checking limit")
    before = path.read_bytes() if path.is_file() else None
    text = before.decode("utf-8") if before is not None else ""
    draft = (
        f"# {client} - Scenario draft\n\n"
        "Status: Draft; technical generation is not scenario approval.\n\n"
        "## Persona and outcome\n\n[TO CLARIFY: role, task and desired outcome]\n\n"
        "## Storyline and acceptance criteria\n\n"
        "[TO CLARIFY: user stories, actual tool/example mapping and acceptance criteria]\n\n"
        "## Guardrails\n\n[TO CLARIFY: confirmation before writes and refusal behavior]\n"
    )
    if not text.strip("\ufeff \t\r\n"):
        text += ("\n" if text and not text.endswith("\n") else "") + draft
    span = managed_span(text)
    narrative = text if span is None else text[:span[0]] + text[span[1]:]
    for number, line in _lines(narrative):
        headers = [cell.casefold() for cell in _cells(line)]
        technical = (_operation_table(headers) or
                     bool({"operationid", "operation"} & set(headers)) and
                     bool({"mock behavior", "status/example", "method", "path"} & set(headers)))
        if "|" in line and technical:
            raise SpecSyncConflict(
                f"Unmanaged technical table near line {number}. Nothing was changed. "
                "Review/migrate its narrative and remove the hand-written table before spec-sync; "
                "the generator never deletes unmarked content automatically.")
    newline = "\r\n" if "\r\n" in text else "\n"
    block = managed_reference(records).replace("\n", newline)
    if span is None:
        separator = "" if not text else newline if text.endswith("\n") else newline * 2
        result = text + separator + block + newline
    else:
        result = text[:span[0]] + block + text[span[1]:]
    managed_span(result)
    after = result.encode("utf-8")
    if len(after) > MAX_SPEC_BYTES:
        raise SpecSyncConflict("Generated specification exceeds 128 KiB; review the scenario scope. No content was truncated.")
    return SpecSyncPlan(client, path, before, after, projection_hash(records))


def apply_spec_sync(root: Path, plan: SpecSyncPlan) -> bool:
    def current():
        if plan_spec_sync(root, plan.client) != plan:
            raise ValueError("Specification/contracts changed since preview; inspect a fresh spec-sync plan.")
    current()
    if not plan.changed:
        return False
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=plan.path.parent, prefix=".spec-sync-", suffix=".tmp",
                                         mode="wb", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(plan.after)
        current()
        os.replace(temporary, safe_data_path(root, plan.path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def _lines(text: str) -> list[tuple[int, str]]:
    lines = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append((number, line))
    return lines


def _cells(line: str) -> list[str]:
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
    return [(cell[1:-1] if cell.startswith("`") and cell.endswith("`") else cell).replace(r"\|", "|")
            for cell in cells]


def check_text(text: str, records: list[dict]) -> ScenarioCheck:
    issues = []
    def issue(line, message):
        issues.append(ScenarioIssue(line=line, message=message))

    try:
        span = managed_span(text)
    except SpecSyncConflict as error:
        return ScenarioCheck(status="mismatch", issues=[ScenarioIssue(message=str(error))])
    if span is not None and text[span[0]:span[1]].replace("\r\n", "\n") != managed_reference(records):
        issue(None, "Generated contract reference is stale or edited; preview mcp-kit spec-sync and regenerate with --write.")
    narrative = text if span is None else text[:span[0]] + text[span[1]:]
    if "[TO CLARIFY:" in narrative:
        issue(None, "Resolve the narrative's [TO CLARIFY: ...] placeholders with the operator; generated tables are not a reviewed storyline.")

    by_id: dict[str, list[dict]] = {}
    for record in records:
        by_id.setdefault(record["operationId"], []).append(record)

    def resolve(opid, contract, line):
        choices = [item for item in by_id.get(opid, []) if not contract or item["contract"] == contract]
        if len(choices) != 1:
            issue(line, f"Unknown or ambiguous operation '{opid}'"
                  + (f" in '{contract}'." if contract else "; use exact imported IDs and a Contract column."))
            return None
        return choices[0]

    lines = _lines(text)
    coverage, mocks = set(), set()
    tables = {"operations": 0, "mocks": 0}
    index = 0
    while index + 1 < len(lines):
        number, line = lines[index]
        headers = [cell.casefold() for cell in _cells(line)]
        kind = ("operations" if _operation_table(headers) else
                "mocks" if {"operation", "request condition", "status/example"} <= set(headers) else None)
        if kind is None or "|" not in line:
            index += 1
            continue
        tables[kind] += 1
        separators = _cells(lines[index + 1][1])
        if len(headers) != len(set(headers)) or len(headers) != len(separators) or not all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in separators):
            issue(number, "Malformed scenario table header/separator.")
            index += 1
            continue
        index += 2
        while index < len(lines) and lines[index][1].lstrip().startswith("|"):
            number, line = lines[index]
            index += 1
            cells = _cells(line)
            if len(cells) != len(headers):
                issue(number, "Malformed table row; escape literal pipes as \\|.")
                continue
            row = dict(zip(headers, cells))
            opid = row["operationid" if kind == "operations" else "operation"]
            record = resolve(opid, row.get("contract"), number)
            if record is None:
                continue
            key = (record["contract"], opid)
            if kind == "operations":
                if key in coverage:
                    issue(number, f"Duplicate Operations row for {opid}.")
                coverage.add(key)
                expected = f"{record['method']} {record['path']}"
                actual = row.get("method and path", f"{row.get('method')} {row.get('path')}")
                if actual != expected:
                    issue(number, f"{opid}: expected '{expected}', not '{actual}'.")
            else:
                mocks.add(key)
                status = re.match(r"^([1-5][0-9]{2}|[1-5]XX|default)(?:\s|$)", row["status/example"])
                responses = {response["status"]: response for response in record["responses"]}
                if status is None or status[1] not in responses:
                    issue(number, f"{opid}: use a declared response status: {', '.join(responses)}.")
                elif names := re.findall(r"`([^`]+)`", row["status/example"]):
                    actual = {example["name"] for example in responses[status[1]]["examples"]}
                    for name in names:
                        if name not in actual:
                            issue(number, f"{opid}: unknown response example '{name}' for {status[1]}.")
    for kind, count in tables.items():
        if count == 0:
            issue(None, f"Missing {kind} table. Preview mcp-kit spec-sync; approve --write to generate technical sections.")
    required = {(item["contract"], item["operationId"]) for item in records if item["selected"]}
    if not required:
        required = {(item["contract"], item["operationId"]) for item in records}
    for key in sorted(required - coverage):
        issue(None, f"Missing Operations row for {key[0]}/{key[1]}.")
    for key in sorted(required - mocks):
        issue(None, f"Missing Mock behavior row for {key[0]}/{key[1]}.")
    for number, line in lines:
        if re.match(r"^\s*(?:-\s*)?Tool(?: after confirmation)?:", line, re.IGNORECASE):
            names = re.findall(r"`([^`]+)`", line)
            if not names:
                issue(number, "Tool references must use exact operation IDs in backticks.")
            for name in names:
                resolve(name, None, number)
    return ScenarioCheck(status="mismatch" if issues else "consistent", issues=issues)


def check_spec(root: Path, client: str, records: list[dict]) -> ScenarioCheck:
    path = safe_data_path(root, root / "docs" / client / "spec.md")
    if not path.is_file() or not path.stat().st_size:
        return ScenarioCheck(status="missing", issues=[ScenarioIssue(
            message=f"Write and review docs/{client}/spec.md before preparation.")])
    if path.stat().st_size > MAX_SPEC_BYTES:
        return ScenarioCheck(status="mismatch", issues=[ScenarioIssue(
            message="Specification exceeds the 128 KiB checking limit.")])
    text = path.read_text("utf-8")
    from .scenario_metadata import parse_spec_metadata
    try:
        parse_spec_metadata(text)
    except ValueError as error:
        return ScenarioCheck(status="mismatch", issues=[ScenarioIssue(line=1, message=str(error))])
    return check_text(text, records)


def scenario_report(root: Path, client: str) -> dict:
    records = inventory(root, client)
    try:
        sync = plan_spec_sync(root, client, records).public(root)
    except SpecSyncConflict as error:
        sync = {"writes": False, "conflict": str(error), "narrativeReviewRequired": True}
    return {"client": client, "operations": records, "referenceMarkdown": reference_markdown(records),
            "check": check_spec(root, client, records).model_dump(mode="json", by_alias=True), "specSync": sync}


def require_consistent_spec(root: Path, client: str) -> None:
    check = check_spec(root, client, inventory(root, client))
    if check.status != "consistent":
        details = "\n".join(f"line {item.line or '-'}: {item.message}" for item in check.issues)
        raise ValueError(f"Scenario/contract mismatch; run mcp-kit scenario-contract {client} "
                         f"and correct docs/{client}/spec.md before prepare.\n{details}")
