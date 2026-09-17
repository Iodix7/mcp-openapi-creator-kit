"""Installed dispatcher regressions: rejected plans never mutate customer data."""
import copy
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from mcp_openapi_creator_kit.assets import kit_root
from mcp_openapi_creator_kit.cli import main
from mcp_openapi_creator_kit.policy import build_client_plan, write_client_plan
from mcp_openapi_creator_kit.runtime import command
from test_build_facade import CONTRACT, MANIFEST


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")}


def write_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


@pytest.fixture
def customer(tmp_path):
    root = tmp_path / "customer"
    write_yaml(root / "apis" / "things" / "openapi.yaml", CONTRACT)
    write_yaml(root / "clients" / "demo" / "mcp-manifest.yaml", MANIFEST)
    generated = root / "clients" / "demo" / "generated"
    (generated / "policy-mcp").mkdir(parents=True)
    (generated / "policy-mcp" / "prior.policy.xml").write_text("preserve old policy")
    (generated / "client.bicep").write_text("preserve old bicep")
    (tmp_path / "outside-agent-policy-mcp.policy.xml").write_text("outside sentinel")
    return root


def rejected(root, arguments, capsys, match=None):
    before = snapshot(root.parent)
    with pytest.raises(SystemExit) as error:
        main(["--workspace", str(root), *arguments])
    assert error.value.code in (1, 2)
    stderr = capsys.readouterr().err
    assert stderr and "Traceback" not in stderr
    if match:
        assert match in stderr
    assert snapshot(root.parent) == before


@pytest.mark.parametrize("builder", ["build", "build-policy", "variant"])
@pytest.mark.parametrize("field", ["client", "api", "facade"])
@pytest.mark.parametrize("value", [
    "../../../../../outside", "..\\..\\..\\outside", "/outside",
    "C:\\outside", "%2e%2e%2foutside", "..%5coutside", "bad:name",
])
def test_manifest_path_names_rejected_before_any_write(customer, builder, field, value, capsys):
    path = customer / "clients" / "demo" / "mcp-manifest.yaml"
    manifest = copy.deepcopy(MANIFEST)
    if field == "client":
        manifest["client"] = value
    elif field == "api":
        manifest["apis"][0]["name"] = value
    else:
        manifest["mcpExposure"]["facadeName"] = value
    write_yaml(path, manifest)
    args = ["variant", "demo", "copy", "--write"] if builder == "variant" else [builder, "clients/demo"]
    rejected(customer, args, capsys, "ERROR")


@pytest.mark.parametrize("builder", ["build", "build-policy", "variant"])
def test_manifest_identity_must_match_directory(customer, builder, capsys):
    path = customer / "clients" / "demo" / "mcp-manifest.yaml"
    write_yaml(path, {**MANIFEST, "client": "different"})
    args = ["variant", "demo", "copy", "--write"] if builder == "variant" else [builder, "clients/demo"]
    rejected(customer, args, capsys, "must match folder")


@pytest.mark.parametrize("builder", ["build", "build-policy"])
@pytest.mark.parametrize("raw", ["../outside", "clients/../../outside", "clients/%2e%2e/outside"])
def test_selected_directory_cannot_escape(customer, builder, raw, capsys):
    rejected(customer, [builder, raw], capsys)


@pytest.mark.parametrize("builder", ["build", "build-policy"])
def test_absolute_directory_outside_customer_rejected(customer, builder, capsys):
    rejected(customer, [builder, str(customer.parent)], capsys)


@pytest.mark.parametrize("builder", ["build", "build-policy"])
@pytest.mark.parametrize("kind", ["input", "output", "module", "stale"])
def test_hardlinks_rejected_before_cleanup_or_writes(customer, builder, kind, capsys):
    generated = customer / "clients" / "demo" / "generated"
    paths = {
        "input": customer / "apis" / "things" / "openapi.yaml",
        "output": generated / ("policy-mcp/servers.json" if builder == "build-policy" else "facade.policy.xml"),
        "module": generated / "kit-modules" / "api-with-mcp.bicep",
        "stale": generated / "policy-mcp" / "stale.policy.xml",
    }
    path = paths[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("linked sentinel")
    os.link(path, customer.parent / "linked-outside")
    rejected(customer, [builder, "clients/demo"], capsys, "Hard-linked")


@pytest.mark.parametrize("builder", ["build", "build-policy"])
def test_all_clients_preflight_precedes_first_write(customer, builder, capsys):
    write_yaml(customer / "clients" / "zz" / "mcp-manifest.yaml",
               {**MANIFEST, "client": "../../outside"})
    arguments = [builder, "--all"] if builder == "build-policy" else [builder]
    rejected(customer, arguments, capsys, "ERROR")


@pytest.mark.parametrize("builder", ["build", "build-policy"])
def test_missing_later_input_preserves_previous_output(customer, builder, capsys):
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"].append({**manifest["apis"][0], "name": "missing"})
    write_yaml(customer / "clients" / "demo" / "mcp-manifest.yaml", manifest)
    rejected(customer, [builder, "clients/demo"], capsys)


@pytest.mark.parametrize("resource", ["../../../../../outside", "nested/../../../outside", "/outside"])
def test_policy_writer_independently_contains_all_derived_outputs(customer, resource):
    client = customer / "clients" / "demo"
    plan = build_client_plan(customer, client)
    # A later shard must not escape even if a caller bypasses manifest validation.
    plan["servers"].append({**plan["servers"][0], "resourceName": resource})
    before = snapshot(customer.parent)
    with pytest.raises(ValueError, match="escapes"):
        write_client_plan(client, plan)
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("kind", ["shard", "stale", "module", "index"])
def test_policy_writer_independently_rejects_hardlinks(customer, kind):
    client = customer / "clients" / "demo"
    plan = build_client_plan(customer, client)
    plan["servers"].append({**plan["servers"][0], "resourceName": "demo-agent-policy-mcp-2"})
    path = {
        "shard": client / "generated/policy-mcp/demo-agent-policy-mcp-2.policy.xml",
        "stale": client / "generated/policy-mcp/stale.policy.xml",
        "module": client / "generated/kit-modules/api-with-mcp.bicep",
        "index": client / "generated/policy-mcp/servers.json",
    }[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("prior output")
    os.link(path, customer.parent / "linked-outside")
    before = snapshot(customer.parent)
    with pytest.raises(ValueError, match="Hard-linked"):
        write_client_plan(client, plan)
    assert snapshot(customer.parent) == before


@pytest.mark.parametrize("builder", ["build", "build-policy"])
def test_symlinked_generated_directory_rejected(customer, builder, capsys):
    link = customer / "clients" / "demo" / "generated" / "kit-modules"
    outside = customer.parent / "external-modules"
    outside.mkdir()
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    rejected(customer, [builder, "clients/demo"], capsys, "Symlink")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
@pytest.mark.parametrize("builder", ["build", "build-policy"])
def test_junction_generated_directory_rejected(customer, builder, capsys):
    link = customer / "clients" / "demo" / "generated" / "kit-modules"
    outside = customer.parent / "external-modules"
    outside.mkdir()
    subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(outside)],
                   check=True, capture_output=True)
    try:
        assert link.is_junction()
        rejected(customer, [builder, "clients/demo"], capsys, "junction")
    finally:
        link.rmdir()


@pytest.mark.parametrize("write", [False, True])
def test_import_checks_combined_canonical_definitions_without_writes(tmp_path, write, capsys):
    root = tmp_path / "customer"
    spec = copy.deepcopy(CONTRACT)
    spec["components"] = {"schemas": {"Problem": {"type": "string"}}}
    write_yaml(root / "apis" / "existing" / "openapi.yaml", spec)
    # Customer promotion settings cannot disable packaged canonical governance.
    write_yaml(root / "apis" / "canonical-schemas.yaml", {"schemas": []})
    args = ["import-sample", "acme", *(["--write"] if write else [])]
    rejected(root, args, capsys, "CANONICAL schema 'Problem'")
    assert not (root / "clients").exists()
    assert not (root / "docs").exists()


def test_matching_canonical_import_and_variant_succeed(tmp_path, capsys):
    root = tmp_path / "customer"
    sample = yaml.safe_load((kit_root() / "apis/customer-care/openapi.yaml").read_text("utf-8"))
    spec = copy.deepcopy(CONTRACT)
    spec["components"] = {"schemas": {"Problem": sample["components"]["schemas"]["Problem"]}}
    write_yaml(root / "apis" / "existing" / "openapi.yaml", spec)
    original = snapshot(root)
    assert main(["--workspace", str(root), "import-sample", "acme", "--write"]) == 0
    assert main(["--workspace", str(root), "variant", "acme", "other", "--write"]) == 0
    assert all(snapshot(root)[name] == value for name, value in original.items())
    assert not (root / "docs").exists()


def test_schema_overlay_compares_proposed_contracts_without_root_mutation(tmp_path, monkeypatch):
    bf = command("build-facade")
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    one = {"components": {"schemas": {"Problem": {"type": "string"}}}}
    two = {"components": {"schemas": {"Problem": {"type": "object"}}}}
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit):
        bf.check_schema_library({"first": one, "second": two})
    assert snapshot(tmp_path) == before
    bf.check_schema_library({"first": one, "second": copy.deepcopy(one)})


def test_variant_rejects_conflicting_planned_contracts_without_writes(customer, capsys):
    manifest = copy.deepcopy(MANIFEST)
    manifest["mcpExposure"]["mode"] = "perApi"
    manifest["apis"].append({**manifest["apis"][0], "name": "second", "mcpTools": ["get-second"]})
    write_yaml(customer / "clients/demo/mcp-manifest.yaml", manifest)
    first = copy.deepcopy(CONTRACT)
    first["components"] = {"schemas": {"Problem": {"type": "string"}}}
    second = copy.deepcopy(CONTRACT)
    second["paths"]["/v1/things/{thingId}"]["get"]["operationId"] = "get-second"
    second["components"] = {"schemas": {"Problem": {"type": "object"}}}
    write_yaml(customer / "apis/things/openapi.yaml", first)
    write_yaml(customer / "apis/second/openapi.yaml", second)
    rejected(customer, ["variant", "demo", "other", "--write"], capsys, "Conflicting component")


def test_variant_checks_existing_and_proposed_canonical_schemas(customer, capsys):
    first = copy.deepcopy(CONTRACT)
    first["components"] = {"schemas": {"Problem": {"type": "string"}}}
    existing = copy.deepcopy(CONTRACT)
    existing["components"] = {"schemas": {"Problem": {"type": "object"}}}
    write_yaml(customer / "apis/things/openapi.yaml", first)
    write_yaml(customer / "apis/unselected/openapi.yaml", existing)
    rejected(customer, ["variant", "demo", "other", "--write"], capsys, "CANONICAL schema 'Problem'")
