"""Synthetic Azure boundary for installed acceptance; never invokes Azure.

These are explicit synthetic contracts, not captured live-provider responses.
Unknown commands, URLs, methods and API versions fail closed.
"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

SUB = "00000000-0000-0000-0000-000000000000"
TENANT = "00000000-0000-0000-0000-000000000001"
BASE = f"/subscriptions/{SUB}/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/fixture-apim"
TOOLS = [
    "get-customer-context", "get-activation-order", "get-technical-appointment",
    "get-open-cases", "check-commercial-eligibility", "create-reschedule-request",
]
PREVIEW_ARGUMENTS = [
    "deploy", "clients/acme", "--subscription", SUB, "--tenant", TENANT,
    "--resource-group", "fixture", "--apim-name", "fixture-apim", "--profile",
    "native-mcp", "--confirm-subscription", SUB,
]


def resource_ids(external=False):
    """Independent expected native facade resources for the fixed acme starter."""
    ids = {
        f"{BASE}/tags/acme", f"{BASE}/tags/acme-{'external' if external else 'mock'}",
        f"{BASE}/products/acme-product", f"{BASE}/products/acme-product/policies/policy",
        f"{BASE}/products/acme-product/tags/acme", f"{BASE}/subscriptions/acme-pilot",
    }
    for name, tags, native in (
        ("acme-customer-care-acme", ["acme", "acme-external" if external else "acme-mock"], False),
        ("acme-agent", ["acme"], False),
        ("acme-agent-mcp", ["acme"], True),
    ):
        rid = f"{BASE}/apis/{name}"
        ids.add(rid)
        ids.update(f"{rid}/tags/{tag}" for tag in tags)
        ids.add(f"{BASE}/products/acme-product/apis/{name}")
        if native:
            ids.update(f"{rid}/tools/acme-{tool}" for tool in TOOLS)
        else:
            ids.add(f"{rid}/policies/policy")
    if external:
        ids.add(f"{BASE}/namedValues/acme-key")
    return sorted(ids)


class SyntheticAzure:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.calls = []
        self.account = {"id": SUB, "tenantId": TENANT, "user": "operator@example.test"}
        self.gateway = {
            "id": BASE,
            "sku": {"name": "BasicV2"},
            "identity": {"type": "SystemAssigned", "principalId": "fixture-principal"},
            "properties": {"provisioningState": "Succeeded", "publicNetworkAccess": "Enabled"},
        }
        self.routes = {name: [] for name in (
            "/diagnostics", "/apis", "/products", "/tags", "/subscriptions", "/namedValues",
            "/apis/acme-customer-care-acme/tags", "/apis/acme-customer-care-acme/diagnostics",
        )}
        self.external = False
        self.allow_apply = False
        self.secrets = [{"id": "https://fixture-vault.vault.azure.net/secrets/acme-key",
                         "attributes": {"enabled": True}}]

    def run(self, args, capture=False):
        args = [str(arg) for arg in args]
        self.calls.append(args)
        child = [sys.executable, "-I", "-m", "mcp_openapi_creator_kit.cli",
                 "--workspace", str(self.root), "build", "clients/acme"]
        if args == child:
            result = subprocess.run(args, cwd=self.root, text=True, capture_output=True,
                                    timeout=60, check=True)
            return result.stdout
        if args[:3] == ["az", "account", "show"]:
            return json.dumps(self.account)
        assert args and args[0] == "az", "Non-Azure/non-isolated child command rejected"
        assert "--subscription" in args and args[args.index("--subscription") + 1] == SUB
        if args[:2] == ["az", "rest"]:
            method = args[args.index("--method") + 1]
            uri = urlsplit(args[args.index("--uri") + 1])
            assert not uri.netloc and not uri.scheme
            assert parse_qs(uri.query) == {"api-version": ["2024-06-01-preview"]}
            if method == "DELETE":
                assert self.allow_apply and uri.path == BASE + "/apis/acme-retired"
                self.routes["/apis"] = [item for item in self.routes["/apis"] if item["name"] != "acme-retired"]
                return ""
            assert method == "GET", "Mutation rejected by offline boundary"
            if uri.path == BASE:
                return json.dumps(self.gateway)
            assert uri.path.startswith(BASE + "/")
            suffix = uri.path[len(BASE):]
            assert suffix in self.routes, f"Unmodeled ARM collection: {suffix}"
            return json.dumps({"value": self.routes[suffix]})
        if args[:4] == ["az", "deployment", "group", "list"]:
            assert args[args.index("--resource-group") + 1] == "fixture"
            return "[]"
        if args[:4] in (["az", "deployment", "group", "what-if"],
                        ["az", "deployment", "group", "create"]):
            if args[3] == "create":
                assert self.allow_apply, "Apply disabled in this fixture"
            assert args[args.index("--resource-group") + 1] == "fixture"
            assert args[args.index("--name") + 1] == "client-acme"
            assert Path(args[args.index("--template-file") + 1]) == self.root / "clients/acme/generated/client.bicep"
            assert "--mode" in args and args[args.index("--mode") + 1] == "Incremental"
            if args[3] == "create":
                assert "enableNativeMcp=true" in args and "keyVaultName=" in args
                return ""
            assert args[args.index("--result-format") + 1] == "ResourceIdOnly"
            return json.dumps({"status": "Succeeded", "changes": [
                {"resourceId": rid, "changeType": "Create",
                 "after": {"secret": "SYNTHETIC-DO-NOT-LOG"}}
                for rid in resource_ids(self.external)]})
        if args[:3] == ["az", "keyvault", "show"]:
            assert args[args.index("--name") + 1] == "fixture-vault"
            return json.dumps({
                "id": f"/subscriptions/{SUB}/resourceGroups/fixture/providers/Microsoft.KeyVault/vaults/fixture-vault",
                "properties": {"tenantId": TENANT, "accessPolicies": [{
                    "objectId": "fixture-principal", "permissions": {"secrets": ["get", "list"]}}]},
            })
        if args[:4] == ["az", "keyvault", "secret", "list"]:
            assert args[args.index("--vault-name") + 1] == "fixture-vault"
            return json.dumps(self.secrets)
        raise AssertionError(f"Unmodeled external command: {args[:4]}")


def run_scenarios(root):
    """Run the real installed dispatcher, generators, safety checks and reconciler."""
    import yaml
    from mcp_openapi_creator_kit.cli import main
    from mcp_openapi_creator_kit.runtime import command

    root = Path(root)
    deploy = command("deploy-client")
    records = []
    other_before = {str(p.relative_to(root)): p.read_bytes()
                    for p in (root / "clients/other").rglob("*") if p.is_file()}
    arguments = ["--workspace", str(root), "deploy", "clients/acme",
                 "--subscription", SUB, "--tenant", TENANT, "--resource-group", "fixture",
                 "--apim-name", "fixture-apim", "--profile", "native-mcp",
                 "--confirm-subscription", SUB]

    def attempt(name, azure, extra=(), error=None, applied=False):
        out, err = io.StringIO(), io.StringIO()
        exit_code = 0
        with patch.object(deploy, "run", azure.run), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main([*arguments, *extra])
            except SystemExit as exc:
                exit_code = exc.code
        record = {"scenario": name, "exitCode": exit_code, "calls": azure.calls,
                  "stdout": out.getvalue(), "stderr": err.getvalue()}
        records.append(record)
        logs = root.parent / "logs"
        logs.mkdir(exist_ok=True)
        (logs / "deployment-scenarios.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        assert exit_code == (1 if error else 0), record
        if error:
            assert error in err.getvalue(), record
        elif not applied:
            assert "Review token:" in out.getvalue() and "Preview only" in out.getvalue()
        else:
            assert "acme: deployment completed" in out.getvalue()
        assert "SYNTHETIC-DO-NOT-LOG" not in out.getvalue() + err.getvalue()
        if not applied:
            assert not any("create" in args or "DELETE" in args for args in azure.calls)
        assert not any(args[0] == "azd" for args in azure.calls)
        record["passed"] = True
        (logs / "deployment-scenarios.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        return out.getvalue()

    happy = SyntheticAzure(root)
    preview = attempt("basicv2-first-client-no-vault", happy)
    assert not any("keyvault" in args for args in happy.calls)
    assert other_before == {str(p.relative_to(root)): p.read_bytes()
                            for p in (root / "clients/other").rglob("*") if p.is_file()}
    assert not (root / "clients/sample").exists() and not (root / "infra").exists()
    assert any("/diagnostics?" in " ".join(args) for args in happy.calls)
    assert any(args[:4] == ["az", "deployment", "group", "list"] for args in happy.calls)
    token = preview.split("Review token: ")[1].splitlines()[0]
    for field in ("id", "tenantId"):
        wrong = SyntheticAzure(root)
        wrong.account[field] = "00000000-0000-0000-0000-000000000009"
        attempt("wrong-" + field, wrong, error="differs from requested context")
        assert not any("rest" in args or "deployment" in args for args in wrong.calls)
    collision = SyntheticAzure(root)
    collision.routes["/apis"] = [{"name": "acme-customer-care-acme", "properties": {"type": "http"}}]
    attempt("unowned-api-collision", collision, error="API occupancy blocked")
    assert not any("what-if" in args for args in collision.calls)
    attempt("no-review-approval", SyntheticAzure(root), ["--yes"], error="Missing/stale review token")
    stale = SyntheticAzure(root)
    stale.gateway["properties"]["etag"] = "changed-after-preview"
    attempt("changed-inventory-token", stale, ["--yes", "--review-token", token],
            error="Missing/stale review token")
    diagnostics = SyntheticAzure(root)
    diagnostics.routes["/diagnostics"] = [{
        "name": "existing", "properties": {"frontend": {"response": {"body": {"bytes": 32}}}}}]
    attempt("preserve-unsafe-diagnostics", diagnostics, error="Unsafe response-body")
    assert not any("what-if" in args for args in diagnostics.calls)
    manifest_path = root / "clients/acme/mcp-manifest.yaml"
    original = manifest_path.read_bytes()
    manifest = yaml.safe_load(original)
    manifest["apis"][0]["backend"] = {
        "mode": "external", "url": "https://backend.example.invalid",
        "outboundAuth": {"type": "apiKey", "headerName": "X-Api-Key", "secretRef": "acme-key"},
    }
    try:
        manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
        for missing in (False, True):
            external = SyntheticAzure(root)
            external.external = True
            if missing:
                external.secrets = []
            attempt("external-secret-" + ("missing" if missing else "present"), external,
                    ["--key-vault-name", "fixture-vault"],
                    error="Secret metadata missing/disabled" if missing else None)
            if missing:
                assert not any("what-if" in args for args in external.calls)
    finally:
        manifest_path.write_bytes(original)
    owned = SyntheticAzure(root)
    owned.routes["/apis"] = [
        {"name": "acme-retired", "properties": {"type": "http"}},
        {"name": "unrelated-api", "properties": {"type": "http"}},
    ]
    owned.routes["/apis/acme-retired/tags"] = [{"name": "acme"}]
    owned.routes["/apis/unrelated-api/tags"] = []
    reviewed = attempt("owned-orphan-preview", owned)
    token = reviewed.split("Review token: ")[1].splitlines()[0]
    owned.calls.clear()
    owned.allow_apply = True
    attempt("reviewed-synthetic-apply", owned, ["--yes", "--review-token", token], applied=True)
    deletes = [args for args in owned.calls if "DELETE" in args]
    creates = [args for args in owned.calls if args[:4] == ["az", "deployment", "group", "create"]]
    assert len(deletes) == len(creates) == 1
    assert owned.calls.index(deletes[0]) < owned.calls.index(creates[0])
    assert owned.routes["/apis"] == [{"name": "unrelated-api", "properties": {"type": "http"}}]
    return records


if __name__ == "__main__":
    from mcp_openapi_creator_kit.cli import main
    from mcp_openapi_creator_kit.runtime import command
    root = Path(sys.argv[1]).resolve()
    arguments = json.loads(sys.argv[2])
    assert arguments == PREVIEW_ARGUMENTS, "Only the fixed synthetic preview is supported"
    print("SYNTHETIC AZURE PREVIEW: no live resource calls or deployment")
    with patch.object(command("deploy-client"), "run", SyntheticAzure(root).run):
        raise SystemExit(main(["--workspace", str(root), *arguments]))
