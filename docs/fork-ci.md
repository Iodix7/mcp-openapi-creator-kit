# Fork-safe CI and Azure smoke deployment

## Model

- `CI` runs on every push and pull request without Azure credentials.
- No push or merge deploys Azure resources.
- `Azure smoke deploy` is manual and uses the fork owner's GitHub Environment,
  OIDC identity, subscription, and resource group.
- Secrets, variables, environments, and federated credentials are not inherited
  by forks.

## Offline CI

The workflow compiles Python, runs tests, generates artifacts, validates profile
compatibility, compiles Bicep, checks generated-file hygiene and determinism, and
uploads the capability catalog.

## Configure Azure smoke

Create a dedicated development resource group and a GitHub Environment named
`azure-smoke`. Add these environment variables:

| Variable | Required |
|---|---|
| `AZURE_CLIENT_ID` | yes |
| `AZURE_TENANT_ID` | yes |
| `AZURE_SUBSCRIPTION_ID` | yes |
| `AZURE_RESOURCE_GROUP` | yes |
| `AZURE_LOCATION` | yes |
| `AZURE_ENV_NAME` | yes |
| `PUBLISHER_EMAIL` | yes |
| `TELEMETRY_MODE` | optional, default `none` |
| `EXISTING_APIM_NAME` | optional |
| `EXISTING_APPINSIGHTS_NAME` | optional |
| `EXISTING_APPINSIGHTS_RG` | optional |

Do not configure a client secret. The workflow uses OIDC.

## Create the federated identity

Run from PowerShell after signing in to the fork owner's tenant:

```powershell
$owner = "<GITHUB_OWNER>"
$repo = "mcp-openapi-creator-kit"
$subscriptionId = "<AZURE_SUBSCRIPTION_ID>"
$resourceGroup = "<AZURE_RESOURCE_GROUP>"

$app = az ad app create --display-name "mcp-openapi-$owner" | ConvertFrom-Json
az ad sp create --id $app.appId | Out-Null

$params = @{
  name = "github-azure-smoke"
  issuer = "https://token.actions.githubusercontent.com"
  subject = "repo:$owner/${repo}:environment:azure-smoke"
  audiences = @("api://AzureADTokenExchange")
} | ConvertTo-Json -Compress

az ad app federated-credential create --id $app.appId --parameters $params

$scope = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup"
az role assignment create --assignee $app.appId --role Contributor --scope $scope
az role assignment create --assignee $app.appId `
  --role "Role Based Access Control Administrator" --scope $scope
```

Store `$app.appId` as `AZURE_CLIENT_ID`.

## Run

From GitHub Actions, select **Azure smoke deploy**, choose a profile, and retype
the subscription ID. The workflow:

1. runs offline CI;
2. validates required configuration and confirmation;
3. signs in with OIDC;
4. prints the target context;
5. runs read-only `azd provision --preview`;
6. opts into reconciliation only for the real provision step;
7. verifies every active client contract.

The environment is persistent because APIM provisioning and soft-delete make an
ephemeral create/delete cycle unreliable.
