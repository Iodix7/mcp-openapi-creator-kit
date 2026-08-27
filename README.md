# MCP OpenAPI Creator Kit

Turn OpenAPI 3.0.x interface agreements into deterministic API mocks and MCP tools for
Microsoft Copilot Studio through Azure API Management.

> **Community project:** this repository is maintained independently and is not
> an official Microsoft product. Microsoft, Azure, and Copilot Studio are
> trademarks of Microsoft Corporation.

[MIT License](LICENSE) | [Contributing](CONTRIBUTING.md) |
[Security](SECURITY.md) | [Support](SUPPORT.md)

```text
Copilot Studio --MCP--> Azure API Management --> backend
                                      | mock: policy responses, no compute
                                      | external: customer-owned HTTP system
```

The OpenAPI contract is the source of truth:

- `operationId` values selected in `mcpTools` become MCP tool names;
- response examples are the mock dataset;
- `x-mock` rules select examples from request parameters and headers;
- generated policies and Bicep are disposable build outputs;
- a backend can move from `mock` to `external` without changing the contract.

## Gateway profiles

| Profile | APIM | Consumer | Backends | Additional compute |
|---|---|---|---|---|
| `native-mcp` | Basic v2 by default, or a compatible existing tier | MCP Streamable HTTP | mock or external | none |
| `policy-mcp-consumption` | Consumption | MCP Streamable HTTP implemented by API policy | public, mock-only | none |
| `rest-consumption` | Consumption | REST/OpenAPI or Custom Connector | public, mock-only | none |

`policy-mcp-consumption` exposes stateless tools only. It shards whole tools
across multiple endpoints before an APIM policy document reaches 16 KiB. A
single tool larger than that fails at build time with its measured size.

APIM Consumption has no fixed gateway charge, but usage beyond included quotas
and optional Azure resources can still incur cost.

## Local validation

Requirements:

- Git
- Python 3.12
- Azure CLI with Bicep
- Azure Developer CLI (`azd`) only for Azure preview or deployment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest tools/tests -q
python tools/check-publication.py
python tools/build-facade.py
python tools/build-policy-mcp.py --all --allow-incompatible
python tools/build-catalog.py
az bicep build --file infra/main.bicep
```

On Linux or macOS, activate the environment with
`source .venv/bin/activate`. These checks do not sign in to Azure or create
resources. The generated capability catalog is
`catalog/generated/catalog.html`.

## Local MCP companion for VS Code

The repository includes an installable, read-only MCP server built with MCP
Python SDK v2:

```powershell
python -m pip install -e ".[dev]"
mcp-openapi-creator --workspace .
```

The checked-in `.vscode/mcp.json` configures GitHub Copilot to start the stdio
server automatically. It exposes the constitution, skills, catalog, workspace
status, profile guidance, policy-budget measurements, and a secure local
dashboard URL. It never edits files, runs workspace code, or invokes Azure.
See [docs/local-mcp-server.md](docs/local-mcp-server.md).

## Repository layout

| Path | Purpose |
|---|---|
| `apis/` | Shared OpenAPI contracts; examples and `x-mock` rules define mocks |
| `clients/<id>/mcp-manifest.yaml` | The only hand-authored client configuration |
| `platform/`, `modules/`, `infra/` | Reusable Azure Bicep |
| `tools/` | Generators, validators, lifecycle reconciliation, and smoke tests |
| `src/mcp_openapi_creator_kit/` | Installable read-only MCP server and reusable catalog/policy core |
| `skills/` | Procedures for coding agents: discovery, onboarding, lifecycle |
| `catalog/` | Optional editorial metadata and self-contained HTML template |
| `docs/templates/` | Scenario specification template |

Never edit or commit generated output under `clients/*/generated/`,
`infra/*.gen.bicep`, or `catalog/generated/`.

## Neutral sample

`clients/sample` composes the `apis/customer-care` contract into six tools. It
covers reads, an idempotent write, RFC 7807 errors, confirmation guardrails, and
request-dependent mock responses without representing a real customer.

```bash
python tools/build-facade.py clients/sample
python tools/build-policy-mcp.py clients/sample
```

## Azure deployment

Read [HANDOVER.md](HANDOVER.md) before deploying. Azure CLI and `azd` maintain
separate authentication contexts; verify both.

```bash
az login
az account show
azd auth login
azd config set alpha.resourceGroupDeployments on
azd env new mcp-openapi-dev
azd env set AZURE_RESOURCE_GROUP <resource-group>
azd env set GATEWAY_PROFILE <native-mcp|policy-mcp-consumption|rest-consumption>
azd env set PUBLISHER_EMAIL <contact-email>
azd env set TELEMETRY_MODE none

# native-mcp only, when private networking is required:
azd env set NETWORK_PROFILE <public|hybrid|isolated>
# hybrid:  azd env set VNET_INTEGRATION_SUBNET_ID <subnet-resource-id>
# isolated: azd env set VNET_INJECTION_SUBNET_ID <subnet-resource-id>

# Read-only: generated files and orphan DELETE plans may be printed, never applied.
azd provision --preview

# Review every planned DELETE, then explicitly reconcile existing APIM resources.
python tools/reconcile-all.py --apply --skip-if-unprovisioned

azd up
```

Verify the deployed contract:

```bash
python tools/verify-mcp.py clients/sample
# or, for rest-consumption:
python tools/verify-rest.py clients/sample
```

Before every Azure-changing command, confirm account, tenant, subscription,
`azd` environment, resource group, and `GATEWAY_PROFILE` with the operator.
`TELEMETRY_MODE=existing` also requires `EXISTING_APPINSIGHTS_NAME`.
The azd preprovision hook uses Azure CLI to verify referenced resources. It
checks subnet delegation and, for isolated Premium v2, minimum `/27` sizing and
an attached network security group before ARM deployment starts.

## Contract rules

The build fails before Azure when a contract violates these rules:

- the contract declares an OpenAPI 3.0.x version; unsupported versions fail explicitly;
- operation IDs use kebab-case;
- every selected `mcpTool` exists;
- selected MCP tool names are unique across clients on the same APIM;
- every response has an example;
- errors use `application/problem+json` and RFC 7807-compatible payloads;
- writes require `Idempotency-Key`;
- mock behavior is expressed only through examples and `x-mock`;
- mocks do not claim state or business calculations;
- secrets appear only as Key Vault `secretRef` names.

## Adding a client

1. Copy `clients/sample` to `clients/<id>` and change the manifest.
2. Reuse a contract from `apis/`, or add a new contract.
3. Run the local validation commands.
4. On an already provisioned environment, use
   `python tools/deploy-client.py clients/<id>` for a targeted deployment. The
   command prints the Azure context and requires the subscription ID to be
   retyped before it can reconcile or deploy.

A shared contract is read-only. If two clients on the same APIM need the same
selected tool names, create a client-specific contract variant with distinct
operation IDs.

## Lifecycle safety

The reconciler deletes only APIM APIs that satisfy both ownership checks:

1. resource name starts with `<client>-`;
2. APIM resource has the `<client>` tag.

Dry-run is the default. When removing a client, add its ID to
`clients/removed-clients.yaml` until all persistent environments have been
reconciled.

## Status

Version 1.0 focuses on contract-first mock APIs, tools-only MCP, deterministic
generation, safe lifecycle reconciliation, and fork-safe CI. See
[docs/roadmap.md](docs/roadmap.md) for explicit limitations and
[docs/publication-checklist.md](docs/publication-checklist.md) for the public
go-live gates.
