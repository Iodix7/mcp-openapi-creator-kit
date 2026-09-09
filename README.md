# MCP OpenAPI Creator Kit

Guide an AI coding agent from scenario discovery to OpenAPI 3.0.x interface
agreements, deterministic API mocks, and MCP tools hosted in Azure API Management.
Use the repository's procedures directly, or connect its local MCP companion to
GitHub Copilot in VS Code for workflow instructions, catalog discovery, compatibility
reports, and a browser dashboard.

> **Community project:** this repository is maintained independently and is not
> an official Microsoft product. Microsoft, Azure, and Copilot Studio are
> trademarks of Microsoft Corporation.

[MIT License](LICENSE) | [Contributing](CONTRIBUTING.md) |
[Security](SECURITY.md) | [Support](SUPPORT.md)

```text
Developer --VS Code / GitHub Copilot--> local MCP companion
                                       | procedures, catalog, reports, dashboard

Copilot Studio --MCP--> Azure API Management --> backend
                                      | mock: policy responses, no compute
                                      | external: customer-owned HTTP system
```

The local companion helps the developer use the kit; it is not the business MCP
endpoint deployed in APIM. Its tools are read-only. File edits and generator or
deployment commands are separate actions performed by the developer or coding
agent using its own tools and the repository's approval procedures.

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

## Optional AI Gateway preview plans

Optional `targets` metadata is independent of the three gateway profiles.
An isolated AI Gateway **tier** preview exporter produces selected OpenAPI
projections, compatibility reports and import plans; management apply is
explicitly blocked until its resource contract is verified.
The local MCP companion remains a read-only workflow, catalog and dashboard
server for the kit, distinct from the business MCP endpoints deployed in APIM.
Existing VS Code and Copilot Studio workflows are unchanged.

See [consumer targets and restrictions](docs/consumer-targets.md) and the
[offline AI Gateway pilot and live gates](docs/pilots/README.md).

After installation, inspect a client's target configuration without writing files:

```powershell
mcp-export-target sample --report
```

For a client explicitly configured with `targets.gateway: ai-gateway-preview`,
export its plan with `mcp-export-target <client-id>`. The CLI writes only to
`clients/<client-id>/generated/targets/`; it does not provision a gateway or
publish tools. `--apply` is intentionally blocked. Existing clients do not need
target metadata, and the sample is not configured for the preview by default.
M365 plugin export is not included.

## Local validation

Requirements:

- Git
- Python 3.12 or later
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

The repository includes an installable, read-only stdio MCP server built with MCP
Python SDK v2. To get started without an Azure subscription or deployment:

```powershell
git clone https://github.com/Iodix7/mcp-openapi-creator-kit.git
cd mcp-openapi-creator-kit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Open the cloned folder in VS Code with GitHub Copilot and the Python extension.
Select `.venv` using **Python: Select Interpreter**, then start or restart
`mcp-openapi-creator` from **MCP: List Servers** and review any trust prompts.
The checked-in `.vscode/mcp.json` starts the server with the selected interpreter;
you do not need to run a second server manually in a terminal.

The companion exposes the constitution, discovery/onboarding/lifecycle prompts,
and these tools:

| Tools | Purpose |
|---|---|
| `workspace-status`, `catalog-search` | Inspect client configuration and discover contract capabilities |
| `recommend-profile`, `policy-budget` | Compare existing profiles and measure policy-MCP size |
| `target-capabilities`, `target-report` | Inspect target compatibility and preview artifacts in memory |
| `dashboard-get-url`, `dashboard-refresh` | Open or refresh the local catalog and target-report dashboard |

For example, ask Copilot: **"Use mcp-openapi-creator to inspect this workspace and
give me the dashboard URL."** Open the URL returned by `dashboard-get-url` in
your browser. It uses a token-protected loopback address and remains available
while the MCP server is running. After changing contracts or manifests, ask
Copilot to call `dashboard-refresh`. No MCP App is required.

The server never edits workspace files or invokes workspace Python, shell,
generator, or Azure commands. The dashboard renders the workspace's HTML and
JavaScript template in your browser, so use a trusted workspace and template.
These restrictions apply to the companion, not to separate coding-agent tools.
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
| `experimental/ai-gateway-preview/` | Isolated preview planning entry point; management apply is blocked |
| `docs/pilots/` | Offline AI Gateway pilot and prerequisites for a separately approved live trial |

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
   `python tools/deploy-client.py clients/<id>` to print the Azure context and
   reconciliation preview. Review every planned DELETE, then rerun with
   `python tools/deploy-client.py clients/<id> --yes` for the targeted
   deployment. Both commands require the subscription ID to be retyped.

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

The latest tagged release is **v1.1.1**. It includes the local MCP companion and
dashboard alongside contract-first mock APIs, tools-only MCP, deterministic
generation, safe lifecycle reconciliation, and fork-safe CI.

The current `main` branch additionally includes experimental AI Gateway tier
plans, target reports in the companion/dashboard, and offline pilot coverage.
These additions are not a new tagged release. AI Gateway management apply and
live Azure/tenant pilot verification are not provided by the offline checks.
The three existing APIM profiles remain separate from this experiment. See
[docs/roadmap.md](docs/roadmap.md) for explicit limitations and
[docs/publication-checklist.md](docs/publication-checklist.md) for the public
go-live gates.
