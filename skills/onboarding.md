# Skill: onboard and deploy a client

Use this procedure after the scenario and contracts are clear. Read `AGENTS.md`
and `HANDOVER.md` first.

## Prerequisites

Verify, do not assume:

```bash
python --version  # must be 3.12
az account show
azd auth login
```

Azure CLI and `azd` have independent contexts. Before any Azure-changing
command, show and confirm account, tenant, subscription, `azd` environment,
resource group, region, and gateway profile.

## Ask for consumer experience first

Ask one blocking question:

> How must the consumer connect?
>
> A. Public mock MCP with no fixed APIM gateway charge
> B. Native MCP with real backends or private networking
> C. REST/OpenAPI or Custom Connector

Map the answer directly:

| Choice | Profile | Default APIM | Constraints |
|---|---|---|---|
| A | `policy-mcp-consumption` | Consumption | public, mock-only, stateless tools |
| B | `native-mcp` | Basic v2 | supports external backends and network profiles |
| C | `rest-consumption` | Consumption | public, mock-only, no MCP resource |

Consumption profiles do not provide private networking. Explain that their
endpoints are internet reachable and protected by APIM authentication/policy.
Use `native-mcp` when private connectivity or real backends are required.

## Collect configuration

1. New or existing APIM. For existing APIM, confirm tier, resource group,
   region, system-assigned identity for native MCP, and existing diagnostics.
2. Telemetry: `none`, `new`, or `existing` Application Insights.
3. Client ID: lowercase slug matching `clients/<id>`.
4. Contracts: neutral sample or approved user contracts. Contracts must use
    OpenAPI 3.0.x; the build rejects unsupported versions.
5. Exposure: `facade` for one endpoint, or `perApi` for separate governance.
6. Each backend: `mock` or, with native MCP, `external` URL and auth scheme.
7. Inbound auth: `subscriptionKey` for pilots or `entraJwt` for production.
8. Region and APIM publisher email.

Never request a secret value in chat or write one to the repository. A manifest
contains only `secretRef`; the operator creates that secret in Key Vault before
deployment.

## Build before Azure

```bash
python -m pytest tools/tests -q
python tools/build-facade.py
python tools/build-policy-mcp.py --all --allow-incompatible
python tools/build-catalog.py
python tools/validate-deployment-profile.py --profile <profile>
az bicep build --file infra/main.bicep
```

Resolve every validation error. Do not weaken contracts to bypass a gate.

For policy MCP, report each policy size. If one tool exceeds 16 KiB, offer:

1. reduce its example/schema with user approval;
2. switch to `native-mcp`;
3. use another MCP runtime.

Do not split one tool or silently remove fields.

## First environment deployment

```bash
azd config set alpha.resourceGroupDeployments on
azd env new <environment>
azd env set AZURE_RESOURCE_GROUP <resource-group>
azd env set GATEWAY_PROFILE <profile>
azd env set PUBLISHER_EMAIL <email>
azd env set TELEMETRY_MODE <none|new|existing>

# native-mcp private networking only:
azd env set NETWORK_PROFILE <public|hybrid|isolated>
# hybrid requires VNET_INTEGRATION_SUBNET_ID
# isolated requires VNET_INJECTION_SUBNET_ID

azd provision --preview
python tools/reconcile-all.py --apply --skip-if-unprovisioned
azd up
```

Preview is read-only. Review every planned DELETE before explicit reconciliation.
A new environment with no `apimName` skips cleanup.

For a new client on an already provisioned environment, use:

```bash
python tools/deploy-client.py clients/<id>
```

## Verify and hand off

```bash
python tools/verify-mcp.py clients/<id>
# or for REST Consumption:
python tools/verify-rest.py clients/<id>
```

Return the endpoint URLs, authentication header name, contract verification
result, and any remaining manual Copilot Studio connection step. Never print a
subscription key or secret.
