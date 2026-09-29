"""Read-only, derived agreement coverage. No new preparation or approval gate."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import Field
import yaml

from .consumer_export import load_client
from .data_paths import safe_data_path
from .rest_runtime import EXTENSION, parse
from .runtime import command, isolated_command
from .scenario_metadata import MAX_HEADER_BYTES, MAX_SPEC_BYTES, _UniqueLoader
from .targets import StrictModel

PROFILES = ("native-mcp", "policy-mcp-consumption", "rest-consumption")
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Requirement(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")]
    source: Text
    description: Text
    kind: Literal["persistence", "idempotency", "callback", "availability", "business-rule", "custom"]
    operationId: str | None = None
    mandatory: bool = True
    scope: Literal["in", "out"] = "in"
    decision: Text | None = None
    reviewed: bool = False


class Scope(StrictModel):
    intent: Literal["demo", "full-ia", "unspecified"] = "unspecified"
    requirements: Annotated[list[Requirement], Field(max_length=200)] = Field(default_factory=list)


def read_scope(root, client):
    path = safe_data_path(root, root / "docs" / client / "spec.md")
    if not path.is_file():
        return Scope(), ""
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise ValueError("Specification exceeds the 128 KiB coverage limit")
    text = path.read_text("utf-8")
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return Scope(), text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
    header = "\n".join(lines[1:end])
    if not re.search(r"""^(?:coverage|"coverage"|'coverage')\s*:""", header, re.MULTILINE):
        return Scope(), text
    if end is None or len(header.encode("utf-8")) > MAX_HEADER_BYTES:
        raise ValueError("Close coverage frontmatter within 16 KiB")
    try:
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(header)):
            raise ValueError("Coverage frontmatter cannot use YAML aliases or anchors")
        data = yaml.load(header, Loader=_UniqueLoader)
    except (yaml.YAMLError, RecursionError) as error:
        raise ValueError("Invalid coverage frontmatter") from error
    scope = parse(Scope, data["coverage"], "spec.coverage")
    if len({r.id for r in scope.requirements}) != len(scope.requirements):
        raise ValueError("Coverage requirement IDs must be unique")
    if any(r.scope == "out" and not r.decision for r in scope.requirements):
        raise ValueError("Out-of-scope requirements need the user's recorded scope decision")
    return scope, text


def _pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def coverage_report(root: Path, client: str, profile: str | None = None) -> dict:
    try:
        return _coverage_report(root, client, profile)
    except (ValueError, OSError, yaml.YAMLError) as error:
        message = ("Invalid YAML in coverage inputs; inspect the contract/specification."
                   if isinstance(error, yaml.YAMLError) else str(error))
        return _blocked_report(client, profile, [message])


def _blocked_report(client, profile, issues, policies=None, intent="unspecified"):
    return {
        "version": 1, "client": client, "profile": profile if profile in PROFILES else None,
        "intent": intent, "writes": False, "sourceHash": None,
        "preflight": {"status": "blocked", "technicalIssues": issues, "policies": policies or []},
        "summary": {"limitations": [], "decisions": []},
        "demo": {"status": "technical-blocker", "readyLive": False},
        "fullAgreement": {"status": "not-verified", "complete": False},
        "requirements": [], "evidence": {"offline": "failed", "live": "not-recorded"},
        "conversation": "Fix the reported input issue; missing coverage is not successful verification.",
    }


def _preflight(root, client, selected):
    technical, policies, errors = [], [], io.StringIO()

    def quiet(module):
        def capture(*values, sep=" ", end="\n", **kwargs):
            errors.write(sep.join(map(str, values)) + end)
        module.print = capture
        return module

    try:
        if selected:
            quiet(isolated_command("validate-deployment-profile", root)).validate(
                selected, [root / "clients" / client / "mcp-manifest.yaml"],
                environment={"EXISTING_APIM_NAME": "capability-check-only"})
        outputs = quiet(isolated_command("build-facade", root)).build_client(root / "clients" / client, write=False)
        for path, text in outputs.items():
            if path.name.endswith(".policy.xml") and not path.name.startswith("facade"):
                size = len(text.encode("utf-8"))
                policies.append({"artifact": path.relative_to(root).as_posix(), "sizeBytes": size,
                                 "limitBytes": 16384 if selected == "rest-consumption" else None})
                if selected == "rest-consumption" and size > 16384:
                    technical.append(f"{path.name} measures {size} bytes; Consumption limit is 16384.")
        if selected == "policy-mcp-consumption":
            from .policy import build_client_plan
            plan = build_client_plan(root, root / "clients" / client)
            for server in plan["servers"]:
                policies.append({"artifact": server["endpointPath"], "sizeBytes": server["sizeBytes"], "limitBytes": 16384})
    except SystemExit:
        technical.append(errors.getvalue().strip() or "Offline contract/build validation failed; run mcp-kit prepare for details.")
    except (ValueError, RuntimeError, OSError) as error:
        technical.append(str(error))
    return technical, policies


def _coverage_report(root: Path, client: str, profile: str | None = None) -> dict:
    manifest, specs = load_client(root, client)
    scope, narrative = read_scope(root, client)
    selected = profile or ("rest-consumption" if manifest.get("targets", {}).get("consumer") == "rest" else None)
    if selected is not None and selected not in PROFILES:
        raise ValueError("Coverage profile must be a supported gateway profile")
    # Compile before projecting fields: incomplete contracts must report errors, not crash the dashboard.
    technical, policies = _preflight(root, client, selected)
    if technical:
        return _blocked_report(client, selected, technical, policies, scope.intent)
    bf = command("build-facade")
    rows, limitations, decisions = [], [], []
    operations, ambiguous = {}, set()

    def add(api, op, suffix, source, capability, reason, checks, *, mandatory=True, in_scope=True, reviewed=True):
        item = {"id": f"{api}/{op}/{suffix}", "api": api, "operationId": op,
                "source": source, "planned": capability, "reason": reason,
                "mandatory": mandatory, "inDemoScope": in_scope, "reviewed": reviewed,
                "verification": {"status": "not-run", "checks": checks}}
        rows.append(item)
        return item

    for api in manifest["apis"]:
        name, backend = api["name"], api["backend"]["mode"]
        spec = specs[name]
        runtime = spec.get(EXTENSION)
        version = runtime.get("version") if isinstance(runtime, dict) else None
        origin = f"apis/{name}/openapi.yaml#"
        for path, method, op in bf.iter_operations(spec):
            oid = op["operationId"]
            if oid in operations:
                ambiguous.add(oid)
            operations[oid] = (name, backend)
            pointer = f"{origin}/paths/{_pointer(path)}/{method}"
            add(name, oid, "response", pointer + "/responses",
                "simulated" if backend == "mock" else "backend",
                "Declared examples selected without persistence." if backend == "mock" else
                "Response and business semantics are delegated to the existing backend, not verified.",
                ["Call every reachable example branch; check status, media type and unchanged example fields."])
            for index, param in enumerate(bf.operation_parameters(spec, path, op)):
                # Path-level parameters retain their actual declaration location.
                local = [bf.resolve_ref(spec, p) for p in op.get("parameters", [])]
                location = (pointer + "/parameters/" + str(local.index(param)) if param in local else
                            f"{origin}/paths/{_pointer(path)}/parameters/" + str(index))
                add(name, oid, f"input-{index}", location,
                    "gateway" if version in (1, 2) else "backend" if backend == "external" else "unsupported",
                    "Runtime enforces supported presence/type/format constraints." if version else
                    "An OpenAPI declaration alone does not enable runtime validation.",
                    ["Valid value accepted; missing/invalid/duplicate scalar input rejected as specified."])
            if "requestBody" in op:
                add(name, oid, "body", pointer + "/requestBody",
                    "gateway" if version == 2 else "backend" if backend == "external" else "unsupported",
                    "Runtime v2 validates bounded nested JSON through the imported operation schema." if version == 2 else
                    "Body validation is not implemented by a static response example.",
                    ["Valid nested JSON accepted.", "Wrong scalar types, missing required fields, invalid arrays/numbers rejected.",
                     "Malformed JSON, unsupported content type and oversized body rejected."])
            if runtime:
                for feature, checks in (
                    ("correlation", ["Echo a canonical caller UUID; generate a UUID for absent/invalid input.",
                                     "Check both header and body, including early authentication errors."]),
                    ("headers", ["Check every selected response's declared headers; check actual 429 Retry-After."]),
                    ("errors", ["Exercise missing/invalid key, disallowed subscription and invalid input in order."]),
                    ("throttling", ["Exhaust the configured API/operation limit with an authorized test subscription."])):
                    add(name, oid, feature, pointer + "/responses" if feature == "headers" else
                        f"clients/{client}/mcp-manifest.yaml#/apis/{manifest['apis'].index(api)}/runtime"
                        if feature == "throttling" else origin + "/" + EXTENSION,
                        "gateway", "Declarative REST runtime; inherited policies and pre-routing errors require live acceptance.", checks)
            if "callbacks" in op:
                add(name, oid, "callbacks", pointer + "/callbacks", "backend",
                    "The kit does not implement callback delivery, retries or asynchronous job lifecycle.",
                    ["Observe actual delivery, retries, duplicates and callback authentication."])
            if method in ("post", "put", "patch", "delete") and backend == "mock":
                limitations.append("Mock writes return examples: no persistence, business calculation or real idempotency.")
        if backend == "mock":
            limitations.append("Response examples are simulations, not a stateful backend.")

    for requirement in scope.requirements:
        if requirement.operationId and requirement.operationId not in operations:
            raise ValueError(f"Coverage requirement {requirement.id} references an unknown operationId")
        if requirement.operationId in ambiguous:
            raise ValueError(f"Coverage requirement {requirement.id} references an operationId repeated across APIs")
        targets = [operations[requirement.operationId]] if requirement.operationId else list(operations.values())
        backend = bool(targets) and all(mode == "external" for _, mode in targets)
        planned = "backend" if backend or requirement.kind in {"persistence", "idempotency", "callback", "business-rule"} else "clarify"
        row = add(targets[0][0] if requirement.operationId else "*", requirement.operationId or "*",
                  f"clause-{requirement.id}", requirement.source, planned,
                  requirement.description + (" Existing backend behavior is not verified." if backend else
                      " This requires a backend or an explicitly scoped simulation; the stateless mock does not implement it."),
                  ["Observe the requested outcome with representative positive and negative calls; record actual evidence."],
                  mandatory=requirement.mandatory, in_scope=requirement.scope == "in", reviewed=requirement.reviewed)
        row["scopeDecision"] = requirement.decision
        if requirement.scope == "in" and requirement.mandatory and (not backend or not requirement.reviewed):
            decisions.append(f"{requirement.id}: clarify the essential outcome or record the already agreed simulation scope.")
    from .scenario import functional_fit
    selected_ids = {oid for api in manifest["apis"] for oid in api.get("mcpTools", [])}
    fit = functional_fit(narrative, [{"operationId": oid, "selected": oid in selected_ids} for oid in operations])
    decisions.extend(item["message"] for item in fit["issues"])

    for row in rows:
        if row["planned"] == "unsupported":
            limitations.append(row["reason"])
    source_hash = hashlib.sha256(json.dumps(
        {"manifest": manifest, "specs": specs, "narrative": narrative, "profile": selected},
        sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {
        "version": 1, "client": client, "profile": selected, "intent": scope.intent,
        "sourceHash": source_hash, "writes": False,
        "preflight": {"status": "passed" if selected else "profile-not-selected",
                      "technicalIssues": technical, "policies": policies},
        "summary": {"limitations": list(dict.fromkeys(limitations)), "decisions": list(dict.fromkeys(decisions))},
        "demo": {"status": "scope-decision-needed" if decisions else
                 "planned-for-agreed-scope" if scope.intent == "demo" else "scope-not-recorded",
                 "readyLive": False},
        "fullAgreement": {"status": "not-verified", "complete": False,
                          "notice": "OpenAPI coverage is not complete extraction of prose. Reviewed mandatory IA clauses "
                                    "and actual acceptance evidence are required; build/deploy is not live verification."},
        "requirements": rows,
        "conversation": "Reuse the user's existing demo scope; do not add a mode questionnaire or per-row approval. "
                        "Summarize unchanged limitations once. Ask only about an unresolved essential outcome. "
                        "Technical failures and operational consents still apply.",
        "evidence": {"offline": "in-memory validation, not deployment", "live": "not-recorded",
                     "approval": "not-established; recorded scope text is not authenticated consent"},
    }
