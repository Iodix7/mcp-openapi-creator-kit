# =============================================================================
# Generator tests (tools/build-facade.py): validations are part of the kit
# contract ("build-time errors, never runtime surprises") and these checks
# lock that behavior. Run with: pytest tools/tests -q
# =============================================================================
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

_TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS))
_spec = importlib.util.spec_from_file_location("build_facade", _TOOLS / "build-facade.py")
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

_spec_vm = importlib.util.spec_from_file_location("verify_mcp", _TOOLS / "verify-mcp.py")
vm = importlib.util.module_from_spec(_spec_vm)
_spec_vm.loader.exec_module(vm)

_spec_vr = importlib.util.spec_from_file_location("verify_rest", _TOOLS / "verify-rest.py")
vr = importlib.util.module_from_spec(_spec_vr)
_spec_vr.loader.exec_module(vr)

_spec_dp = importlib.util.spec_from_file_location(
    "validate_deployment_profile", _TOOLS / "validate-deployment-profile.py")
dp = importlib.util.module_from_spec(_spec_dp)
_spec_dp.loader.exec_module(dp)

_spec_dc = importlib.util.spec_from_file_location("deploy_client", _TOOLS / "deploy-client.py")
dc = importlib.util.module_from_spec(_spec_dc)
_spec_dc.loader.exec_module(dc)


# --- fixture: mini repo with one client and one contract ------------------------

CONTRACT = {
    "openapi": "3.0.3",
    "info": {"title": "Things", "version": "1.0.0", "description": "demo"},
    "servers": [{"url": "https://placeholder.invalid"}],
    "paths": {
        "/v1/things/{thingId}": {
            "get": {
                "operationId": "get-thing",
                "summary": "Legge una thing",
                "parameters": [{
                    "name": "thingId", "in": "path", "required": True,
                    "schema": {"type": "string"},
                }],
                "x-mock": [
                    {"when": {"param": "thingId", "startsWith": "T-"},
                     "respond": {"status": 200}},
                    {"respond": {"status": 404}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["thingId"],
                                "properties": {
                                    "thingId": {"type": "string"},
                                    "size": {"type": "integer"},
                                    "kind": {"type": "string",
                                             "enum": ["plain", "fancy"]},
                                },
                            },
                            "example": {"thingId": "T-1", "size": 3,
                                        "kind": "plain"},
                        }},
                    },
                    "404": {
                        "description": "not found",
                        "content": {"application/problem+json": {
                            "schema": {"type": "object",
                                       "properties": {"title": {"type": "string"}}},
                            "example": {"title": "not found"},
                        }},
                    },
                },
            },
        },
    },
}

MANIFEST = {
    "client": "demo",
    "displayName": "Demo",
    "mcpExposure": {"mode": "facade", "facadeName": "agent"},
    "apis": [{
        "name": "things",
        "displayName": "Things",
        "backend": {"mode": "mock"},
        "mcpTools": ["get-thing"],
    }],
    "standards": {"errorModel": "rfc7807", "writeIdempotency": "required"},
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Isolated mini repo: build_client/main run against patched REPO_ROOT."""
    monkeypatch.setattr(bf, "REPO_ROOT", tmp_path)
    (tmp_path / "infra").mkdir()

    def write(contract=None, manifest=None, client="demo", api="things"):
        contract = contract if contract is not None else copy.deepcopy(CONTRACT)
        manifest = manifest if manifest is not None else copy.deepcopy(MANIFEST)
        api_dir = tmp_path / "apis" / api
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "openapi.yaml").write_text(
            yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
        cdir = tmp_path / "clients" / client
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "mcp-manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        return cdir

    return write


# --- validate_manifest ----------------------------------------------------------

def _manifest(**overrides):
    m = copy.deepcopy(MANIFEST)
    m.update(overrides)
    return m


def test_manifest_valido_passa():
    m = bf.validate_manifest(_manifest(), "demo")
    assert m["mcpExposure"]["mode"] == "facade"
    assert m["inboundAuth"]["mode"] == "subscriptionKey"


def test_client_slug_invalido_muore():
    with pytest.raises(SystemExit):
        bf.validate_manifest(_manifest(client="Demo"), "Demo")


def test_client_diverso_da_cartella_muore():
    with pytest.raises(SystemExit):
        bf.validate_manifest(_manifest(), "altro")


def test_mcp_profile_full_muore():
    with pytest.raises(SystemExit):
        bf.validate_manifest(_manifest(mcpProfile="full"), "demo")


def test_mcp_profile_sconosciuto_muore():
    with pytest.raises(SystemExit):
        bf.validate_manifest(_manifest(mcpProfile="toolsonly"), "demo")


def test_network_profile_invalido_muore():
    with pytest.raises(SystemExit):
        bf.validate_manifest(_manifest(networkProfile="privato"), "demo")


def test_standards_chiave_typo_muore():
    m = _manifest(standards={"erorModel": "rfc7807"})
    with pytest.raises(SystemExit):
        bf.validate_manifest(m, "demo")


def test_standards_valore_typo_muore():
    # Unknown values must not silently disable validation.
    m = _manifest(standards={"errorModel": "rfc-7807"})
    with pytest.raises(SystemExit):
        bf.validate_manifest(m, "demo")
    m = _manifest(standards={"writeIdempotency": "obbligatorio"})
    with pytest.raises(SystemExit):
        bf.validate_manifest(m, "demo")


def test_entra_jwt_senza_audience_muore():
    m = _manifest(inboundAuth={"mode": "entraJwt",
                               "entraJwt": {"tenantId": "t"}})
    with pytest.raises(SystemExit):
        bf.validate_manifest(m, "demo")


# --- check_example (examples are mock data: they must match schema) -------------

SCHEMA = CONTRACT["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"][
    "content"]["application/json"]["schema"]


def test_example_conforme_passa():
    bf.check_example({}, SCHEMA, {"thingId": "T-1", "size": 2, "kind": "fancy"}, "t")


def test_example_string_come_numero_muore():
    with pytest.raises(SystemExit):
        bf.check_example({}, SCHEMA, {"thingId": 9990000001}, "t")


def test_example_required_mancante_muore():
    with pytest.raises(SystemExit):
        bf.check_example({}, SCHEMA, {"size": 2}, "t")


def test_example_enum_fuori_lista_muore():
    with pytest.raises(SystemExit):
        bf.check_example({}, SCHEMA, {"thingId": "T-1", "kind": "weird"}, "t")


def test_example_array_ricorsivo():
    schema = {"type": "array", "items": {"type": "string"}}
    bf.check_example({}, schema, ["a", "b"], "t")
    with pytest.raises(SystemExit):
        bf.check_example({}, schema, ["a", 5], "t")


# --- end-to-end build on mini repo ---------------------------------------------

def test_build_ok_genera_artefatti(repo):
    cdir = repo()
    bf.build_client(cdir)
    gen = cdir / "generated"
    assert (gen / "client.bicep").exists()
    facade_policy = (gen / "facade.policy.xml").read_text(encoding="utf-8")
    assert "context.Operation.UrlTemplate" in facade_policy
    api_policy = (gen / "api-things.policy.xml").read_text(encoding="utf-8")
    assert "<mock-response" not in api_policy
    assert "<return-response>" in api_policy
    assert '"thingId": "T-1"' in api_policy
    # Compiled x-mock rule (lowercase; XML attribute quotes are serialized
    # as &quot;)
    assert "StartsWith(&quot;t-&quot;)" in api_policy


def test_bicep_generato_rende_mcp_condizionale_e_restituisce_rest(repo):
    cdir = repo()
    bf.build_client(cdir)
    bicep = (cdir / "generated" / "client.bicep").read_text(encoding="utf-8")
    assert "param enableNativeMcp bool = true" in bicep
    assert "exposeMcp: enableNativeMcp" in bicep
    assert "output mcpServerUrls array = enableNativeMcp ? [" in bicep
    assert "output restApiUrls array = [" in bicep
    assert "${apim.properties.gatewayUrl}/demo/agent" in bicep


def test_verify_rest_deriva_caso_dalla_prima_regola_xmock(repo, monkeypatch):
    cdir = repo()
    monkeypatch.setattr(vr, "REPO_ROOT", bf.REPO_ROOT)
    manifest = yaml.safe_load((cdir / "mcp-manifest.yaml").read_text())
    cases = list(vr.iter_cases(manifest))
    assert len(cases) == 1
    _, operation_id, base, method, case = cases[0]
    path, _, _, status, media_type, payload = case
    assert operation_id == "get-thing"
    assert base == "demo/agent"
    assert method == "get"
    assert path == "/v1/things/T-?".replace("?", "test")
    assert (status, media_type, payload["thingId"]) == (200, "application/json", "T-1")


def test_profile_consumption_accetta_solo_mock_pubblici(repo):
    cdir = repo()
    dp.validate("rest-consumption", [cdir / "mcp-manifest.yaml"])

    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["backend"] = {"mode": "external", "url": "https://example.test"}
    cdir = repo(manifest=manifest)
    with pytest.raises(SystemExit):
        dp.validate("rest-consumption", [cdir / "mcp-manifest.yaml"])

    violations = dp.validate(
        "rest-consumption", [cdir / "mcp-manifest.yaml"], report_only=True)
    assert len(violations) == 1
    assert "backend.mode=external" in violations[0]


def test_profile_sconosciuto_muore(repo):
    cdir = repo()
    with pytest.raises(SystemExit):
        dp.validate("cheap", [cdir / "mcp-manifest.yaml"])


def test_deploy_client_consumption_disabilita_mcp_senza_keyvault(repo, monkeypatch):
    repo()
    calls = []
    monkeypatch.setattr(dc, "REPO_ROOT", bf.REPO_ROOT)
    monkeypatch.setattr(dc.sys, "argv", ["deploy-client.py", "clients/demo"])
    monkeypatch.setattr(dc, "azd_env", lambda: {
        "apimName": "demo-apim",
        "AZURE_RESOURCE_GROUP": "demo-rg",
        "AZURE_SUBSCRIPTION_ID": "demo-sub",
        "GATEWAY_PROFILE": "rest-consumption",
        "keyVaultName": "",
    })
    monkeypatch.setattr(dc, "run", lambda args, capture=False: calls.append(args) or "")

    dc.main()

    assert any("validate-deployment-profile.py" in call for args in calls for call in args)
    deployment = next(args for args in calls if "deployment" in args)
    reconcile_index = next(i for i, args in enumerate(calls)
                           if any(value.endswith("reconcile-client.py") for value in args))
    deployment_index = next(i for i, args in enumerate(calls) if "deployment" in args)
    assert reconcile_index < deployment_index
    assert "--apply" in calls[reconcile_index]
    assert "enableNativeMcp=false" in deployment
    assert "keyVaultName=" in deployment


def test_deploy_client_policy_profile_aggiunge_deploy_generato(repo, monkeypatch):
    repo()
    calls = []
    monkeypatch.setattr(dc, "REPO_ROOT", bf.REPO_ROOT)
    monkeypatch.setattr(dc.sys, "argv", ["deploy-client.py", "clients/demo"])
    monkeypatch.setattr(dc, "azd_env", lambda: {
        "apimName": "demo-apim",
        "AZURE_RESOURCE_GROUP": "demo-rg",
        "AZURE_SUBSCRIPTION_ID": "demo-sub",
        "GATEWAY_PROFILE": "policy-mcp-consumption",
        "keyVaultName": "",
    })
    monkeypatch.setattr(dc, "run", lambda args, capture=False: calls.append(args) or "")

    dc.main()

    standard = next(args for args in calls
                    if "deployment" in args and "client-demo" in args)
    policy = next(args for args in calls
                  if "deployment" in args and "policy-mcp-client-demo" in args)
    assert "enableNativeMcp=false" in standard
    assert any("build-policy-mcp.py" in value for args in calls for value in args)
    assert "clients/demo/generated/policy-mcp/client.bicep" in policy
    build_policy_index = next(i for i, args in enumerate(calls)
                                        if any(value.endswith("build-policy-mcp.py") for value in args))
    reconcile_index = next(i for i, args in enumerate(calls)
                                    if any(value.endswith("reconcile-client.py") for value in args))
    standard_index = calls.index(standard)
    assert build_policy_index < reconcile_index < standard_index


def test_operationid_underscore_muore(repo):
    contract = copy.deepcopy(CONTRACT)
    op = contract["paths"]["/v1/things/{thingId}"].pop("get")
    op["operationId"] = "get_thing"
    contract["paths"]["/v1/things/{thingId}"]["get"] = op
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["mcpTools"] = ["get_thing"]
    cdir = repo(contract=contract, manifest=manifest)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_example_fuori_schema_blocca_il_build(repo):
    contract = copy.deepcopy(CONTRACT)
    media = contract["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"][
        "content"]["application/json"]
    media["example"] = {"thingId": 12345}  # numero dove lo schema dice string
    cdir = repo(contract=contract)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_risposta_senza_example_muore(repo):
    contract = copy.deepcopy(CONTRACT)
    media = contract["paths"]["/v1/things/{thingId}"]["get"]["responses"]["200"][
        "content"]["application/json"]
    del media["example"]
    cdir = repo(contract=contract)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_scrittura_senza_idempotency_key_muore(repo):
    contract = copy.deepcopy(CONTRACT)
    contract["paths"]["/v1/things"] = {
        "post": {
            "operationId": "create-thing",
            "responses": {"202": {"description": "accettato"}},
        },
    }
    cdir = repo(contract=contract)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_xmock_param_inesistente_muore(repo):
    contract = copy.deepcopy(CONTRACT)
    op = contract["paths"]["/v1/things/{thingId}"]["get"]
    op["x-mock"][0]["when"]["param"] = "address"
    cdir = repo(contract=contract)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_xmock_default_non_ultima_muore(repo):
    contract = copy.deepcopy(CONTRACT)
    op = contract["paths"]["/v1/things/{thingId}"]["get"]
    op["x-mock"] = [op["x-mock"][1], op["x-mock"][0]]  # default first
    cdir = repo(contract=contract)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_xmock_missing_su_path_param_muore(repo):
    contract = copy.deepcopy(CONTRACT)
    op = contract["paths"]["/v1/things/{thingId}"]["get"]
    op["x-mock"][0]["when"] = {"param": "thingId", "missing": True}
    cdir = repo(contract=contract)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_mcptool_non_esistente_muore(repo):
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["mcpTools"] = ["get-thing", "delete-thing"]
    cdir = repo(manifest=manifest)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


def test_backend_hosted_muore(repo):
    manifest = copy.deepcopy(MANIFEST)
    manifest["apis"][0]["backend"] = {"mode": "hosted"}
    cdir = repo(manifest=manifest)
    with pytest.raises(SystemExit):
        bf.build_client(cdir)


# --- verify-mcp: derive expectations from manifest ------------------------------

VM_MANIFEST = {
    "client": "demo",
    "mcpExposure": {"mode": "perApi", "facadeName": "agent"},
    "apis": [
        {"name": "alpha", "mcpTools": ["get-a", "create-a"]},
        {"name": "beta", "mcpTools": ["get-b"]},
    ],
}


def test_verify_expected_servers_perapi():
    servers = vm.expected_servers(copy.deepcopy(VM_MANIFEST))
    assert set(servers) == {"demo-alpha-mcp", "demo-beta-mcp"}
    path, tools = servers["demo-alpha-mcp"]
    assert path == "demo/alpha-mcp/mcp" and tools == {"get-a", "create-a"}


def test_verify_expected_servers_facade():
    m = copy.deepcopy(VM_MANIFEST)
    m["mcpExposure"]["mode"] = "facade"
    servers = vm.expected_servers(m)
    assert set(servers) == {"demo-agent-mcp"}
    path, tools = servers["demo-agent-mcp"]
    assert path == "demo/agent-mcp/mcp"
    assert tools == {"get-a", "create-a", "get-b"}


def test_verify_expected_servers_both():
    m = copy.deepcopy(VM_MANIFEST)
    m["mcpExposure"]["mode"] = "both"
    servers = vm.expected_servers(m)
    assert set(servers) == {"demo-alpha-mcp", "demo-beta-mcp", "demo-agent-mcp"}


def test_verify_expected_policy_servers_da_indice(tmp_path, monkeypatch):
    client_dir = tmp_path / "clients" / "acme"
    generated = client_dir / "generated" / "policy-mcp"
    generated.mkdir(parents=True)
    (generated / "servers.json").write_text(json.dumps({"servers": [{
        "resourceName": "acme-agent-policy-mcp",
        "path": "acme/agent-policy-mcp",
        "tools": ["get-a", "get-b"],
    }]}), encoding="utf-8")
    monkeypatch.setattr(vm, "REPO_ROOT", tmp_path)

    servers = vm.expected_policy_servers(client_dir)

    assert servers == {"acme-agent-policy-mcp": (
        "acme/agent-policy-mcp/mcp", {"get-a", "get-b"})}


def _widgets_contract():
    """Different contract (different operationId): MCP tool names are globally
    unique in APIM, so two clients cannot expose the same one."""
    c = copy.deepcopy(CONTRACT)
    op = c["paths"].pop("/v1/things/{thingId}")
    op["get"]["operationId"] = "get-widget"
    c["paths"]["/v1/widgets/{thingId}"] = op
    return c


def test_indice_autogenera_client_mancante(repo, monkeypatch):
    """Clean clone scenario (#4): targeted build on one client while another
    still has no generated/ - index build does not fail and generates missing."""
    repo()  # demo
    other = copy.deepcopy(MANIFEST)
    other["client"] = "other"
    other["apis"][0]["name"] = "widgets"
    other["apis"][0]["mcpTools"] = ["get-widget"]
    repo(contract=_widgets_contract(), manifest=other,
         client="other", api="widgets")
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py", "clients/demo"])
    bf.main()
    root = bf.REPO_ROOT
    assert (root / "clients" / "other" / "generated" / "client.bicep").exists()
    index = (root / "infra" / "clients.gen.bicep").read_text(encoding="utf-8")
    assert "clients/demo/generated/client.bicep" in index
    assert "clients/other/generated/client.bicep" in index


def _with_components(contract, schemas):
    c = copy.deepcopy(contract)
    c["components"] = {"schemas": schemas}
    return c


PROBLEM_A = {"type": "object", "properties": {"title": {"type": "string"}}}
PROBLEM_B = {"type": "object", "properties": {"title": {"type": "string"},
                                              "errorCode": {"type": "string"}}}


def test_schema_canonico_divergente_muore(repo, monkeypatch):
    """Problem is canonical: divergent structures across contracts must fail build."""
    repo(contract=_with_components(CONTRACT, {"Problem": PROBLEM_A}))
    other = copy.deepcopy(MANIFEST)
    other["client"] = "other"
    other["apis"][0]["name"] = "widgets"
    other["apis"][0]["mcpTools"] = ["get-widget"]
    repo(contract=_with_components(_widgets_contract(), {"Problem": PROBLEM_B}),
         manifest=other, client="other", api="widgets")
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py"])
    with pytest.raises(SystemExit):
        bf.main()


def test_schema_omonimo_non_canonico_warna_ma_non_muore(repo, monkeypatch, capsys):
    """Divergent non-canonical same-name schemas: informational warning,
    build remains green (valid per-client variant case)."""
    repo(contract=_with_components(CONTRACT, {"Extra": PROBLEM_A}))
    other = copy.deepcopy(MANIFEST)
    other["client"] = "other"
    other["apis"][0]["name"] = "widgets"
    other["apis"][0]["mcpTools"] = ["get-widget"]
    repo(contract=_with_components(_widgets_contract(), {"Extra": PROBLEM_B}),
         manifest=other, client="other", api="widgets")
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py"])
    bf.main()  # should not fail
    out = capsys.readouterr().out
    assert "Extra" in out and "WARNING" in out


def test_duplicato_strutturale_suggerito(repo, monkeypatch, capsys):
    """Different names, identical structure: build suggests reuse."""
    money = {"type": "object", "properties": {"value": {"type": "number"},
                                              "currency": {"type": "string"}}}
    repo(contract=_with_components(
        CONTRACT, {"Money": money, "Amount": copy.deepcopy(money)}))
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py"])
    bf.main()
    out = capsys.readouterr().out
    assert "Money" in out and "Amount" in out and "IDENTICA" in out


def test_catalog_schemas_stampa(repo, monkeypatch, capsys):
    repo(contract=_with_components(CONTRACT, {"Problem": PROBLEM_A}))
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py", "--catalog", "schemas"])
    bf.main()
    out = capsys.readouterr().out
    assert "Problem" in out and "[CANONICAL]" in out and "things" in out


def test_operationid_duplicato_tra_clienti_muore(repo, monkeypatch):
    """Platform constraint: MCP tool names are unique across the whole APIM,
    so two clients exposing same operationId must fail BUILD (deployment would
    fail later with non-actionable 502)."""
    repo()  # demo espone get-thing via 'things'
    other = copy.deepcopy(MANIFEST)
    other["client"] = "other"
    repo(manifest=other, client="other")  # same shared 'things' contract
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py"])
    with pytest.raises(SystemExit):
        bf.main()


def test_operationid_rest_non_esposto_puo_ripetersi_tra_clienti(
        repo, monkeypatch):
    """REST operations not selected in mcpTools are not APIM tool names."""
    shared_rest = {
        "operationId": "get-health",
        "responses": {"200": {"description": "ok", "content": {
            "application/json": {"example": {"status": "ok"}}}}},
    }
    first = copy.deepcopy(CONTRACT)
    first["paths"]["/health"] = {"get": copy.deepcopy(shared_rest)}
    repo(contract=first)

    other_contract = _widgets_contract()
    other_contract["paths"]["/health"] = {"get": copy.deepcopy(shared_rest)}
    other = copy.deepcopy(MANIFEST)
    other["client"] = "other"
    other["apis"][0]["name"] = "widgets"
    other["apis"][0]["mcpTools"] = ["get-widget"]
    repo(contract=other_contract, manifest=other, client="other", api="widgets")
    monkeypatch.setattr(bf.sys, "argv", ["build-facade.py"])

    bf.main()
