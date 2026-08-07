# Deployment handover

This guide installs the generated contracts and policies into the operator's
Azure subscription. It supports native MCP, policy MCP on APIM Consumption, and
REST on APIM Consumption.

## Requirements

- Python 3.12
- Azure CLI and Azure Developer CLI (`azd`)
- Owner, or equivalent deployment and role-assignment permissions, on the target
  resource group
- available API Management capacity in the selected region
- Copilot Studio with generative orchestration when it is the consumer

## First deployment

```bash
python -m pip install -r tools/requirements.txt
az login
az account show
azd auth login
azd config set alpha.resourceGroupDeployments on
azd env new <environment-name>
azd env set AZURE_RESOURCE_GROUP <resource-group>
azd env set GATEWAY_PROFILE <native-mcp|policy-mcp-consumption|rest-consumption>
azd env set PUBLISHER_EMAIL <contact-email>
azd env set TELEMETRY_MODE <none|new|existing>
```

Optional variables:

- `EXISTING_APIM_NAME`
- `EXISTING_APPINSIGHTS_NAME`
- `EXISTING_APPINSIGHTS_RG`

Before continuing, confirm the Azure CLI account and tenant, subscription,
`azd` environment, resource group, region, and gateway profile.

```bash
# Read-only infrastructure preview and lifecycle plan.
azd provision --preview

# Existing APIM only: review the printed DELETE plan, then apply it explicitly.
python tools/reconcile-all.py --apply --skip-if-unprovisioned

azd up
```

APIM and Key Vault use soft delete. Avoid repeatedly destroying and recreating
demo environments with the same names.

## Verify

```bash
python tools/verify-mcp.py clients/<client-id>
# REST Consumption only:
python tools/verify-rest.py clients/<client-id>
```

Run verification after every deployment and before every demonstration.

## Connect Copilot Studio

For `native-mcp` or `policy-mcp-consumption`:

1. In Copilot Studio, add a Model Context Protocol tool.
2. Use an MCP URL from the `azd` outputs.
3. Select API-key authentication.
4. Set header `Ocp-Apim-Subscription-Key` to the APIM product subscription key.

Policy MCP may output multiple URLs when policy sharding is required. Add all of
them. The profile exposes stateless tools only.

For `rest-consumption`, use `restApiUrls` directly or import the generated
OpenAPI facade into a Custom Connector. A REST URL is not an MCP server URL.

## Retrieve a pilot subscription key

```bash
az rest --method POST \
  --uri "/subscriptions/<subscription>/resourceGroups/<resource-group>/providers/Microsoft.ApiManagement/service/<apim>/subscriptions/<client-id>-pilot/listSecrets?api-version=2024-06-01-preview" \
  --query primaryKey -o tsv
```

Treat the value as a secret. Never commit or paste it into issue reports.

## Move one API from mock to an external backend

Update only the client manifest:

```yaml
backend:
  mode: external
  url: https://api.example.invalid/v1
  outboundAuth:
    type: oauth2-cc
    tokenUrl: https://login.example.invalid/oauth2/token
    clientId: non-secret-client-id
    scope: example.api
    secretRef: example-client-secret
```

Create the secret before provisioning:

```bash
az keyvault secret set --vault-name <vault> \
  --name example-client-secret --value '<secret>'
```

Then run `python tools/deploy-client.py clients/<client-id>`. Other APIs can
remain mocked and the agent contract stays unchanged.

## Production inbound authentication

Set `inboundAuth.mode: entraJwt` and provide the tenant ID and API audience in
the manifest. The kit adds `validate-jwt` at APIM product scope in addition to
the product subscription key.

## Rename or remove resources

Use the reconciler before deployment. It removes native MCP tools before their
source API operations, then removes orphan servers and REST APIs.

```bash
python tools/reconcile-client.py clients/<client-id>          # dry-run
python tools/reconcile-client.py clients/<client-id> --apply  # after review
```

For a removed client, keep its ID in `clients/removed-clients.yaml` until all
persistent environments have been reconciled.

## Fork-safe Azure smoke workflow

The `Azure smoke deploy` workflow is manual. Configure the `azure-smoke` GitHub
Environment in your fork; credentials and environment variables are not
inherited from upstream. See [docs/fork-ci.md](docs/fork-ci.md).
