// =============================================================================
// PLATFORM - shared infrastructure (one per subscription/environment)
// APIM v2 + Key Vault + Log Analytics + App Insights
// =============================================================================

@description('Prefix for naming all resources (for example, "mcpkit")')
@minLength(3)
@maxLength(12)
param baseName string

param location string = resourceGroup().location

@description('Network profile: determines APIM tier. public=BasicV2, hybrid=StandardV2, isolated=PremiumV2')
@allowed(['public', 'hybrid', 'isolated'])
param networkProfile string = 'public'

param publisherEmail string
param publisherName string

@description('Gateway profile: native MCP, REST Consumption, or policy MCP on Consumption')
@allowed(['native-mcp', 'rest-consumption', 'policy-mcp-consumption'])
param gatewayProfile string = 'native-mcp'

// hybrid requires a subnet delegated to Microsoft.Web/serverFarms for outbound VNet integration
param vnetIntegrationSubnetId string = ''

// isolated requires a dedicated subnet for VNet injection (private inbound+outbound)
param vnetInjectionSubnetId string = ''

// EXISTING APIM: if set, the kit does NOT create a new APIM and instead attaches
// to this one (same resource group). Requirements: v2 tier or non-Consumption
// classic tier, with system-assigned managed identity enabled. WARNING: APIM
// App Insights diagnostics are set to MCP-safe (body bytes=0), overriding
// any existing diagnostics config. networkProfile is ignored (network is already
// defined by the existing APIM).
param existingApimName string = ''

var useExistingApim = !empty(existingApimName)
var useConsumption = gatewayProfile != 'native-mcp'

var skuByProfile = {
  public: 'BasicV2'
  hybrid: 'StandardV2'
  isolated: 'PremiumV2'
}

// Deterministic suffix for globally unique names (KV, APIM):
// prevents collisions across kit installations in different tenants/clients.
var nameSuffix = take(uniqueString(resourceGroup().id), 6)

// azd environment name: conventional ARM tag used by azd to recognize
// environment resources. Empty = no tag.
param azdEnvName string = ''
var commonTags = empty(azdEnvName) ? {} : { 'azd-env-name': azdEnvName }

// --- Observability -----------------------------------------------------------
// Telemetry is a CHOICE, not a kit mandate:
//   new      -> the kit creates its own App Insights + Log Analytics (default)
//   existing -> reuses your existing App Insights (even in another resource
//               group in the same subscription): telemetry remains where you
//               already monitor it. Requires existingAppInsightsName.
//   none     -> no logger/diagnostics created by the kit. WARNING on existing
//               APIM: if a global diagnostics setting already logs bodies,
//               it remains active and may break MCP streaming.
// With new/existing, APIM diagnostics are always set to MCP-safe.
@allowed(['new', 'existing', 'none'])
param telemetryMode string = 'new'

param existingAppInsightsName string = ''

@description('Resource group of existing App Insights; empty = same RG as deployment')
param existingAppInsightsResourceGroup string = ''

var useExistingAi = telemetryMode == 'existing'
var telemetryOn = telemetryMode != 'none'

resource appInsightsExisting 'Microsoft.Insights/components@2020-02-02' existing = if (useExistingAi) {
  name: existingAppInsightsName
  scope: resourceGroup(empty(existingAppInsightsResourceGroup)
    ? resourceGroup().name
    : existingAppInsightsResourceGroup)
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (telemetryMode == 'new') {
  name: '${baseName}-law'
  location: location
  tags: commonTags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = if (telemetryMode == 'new') {
  name: '${baseName}-ai'
  location: location
  tags: commonTags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// --- Key Vault (RBAC) ---------------------------------------------------------
// Purge protection: enable in production. Default OFF because it prevents
// vault recreation for 90 days after teardown — incompatible with the
// azd down / azd up demo cycle (once ON, it cannot be disabled).
param kvPurgeProtection bool = false

// Deployer principal (passed by azd via AZURE_PRINCIPAL_ID): receives
// Key Vault Secrets Officer, required to write secretRef secrets
// (vault is RBAC-only: Owner is NOT sufficient for data plane).
param deployerPrincipalId string = ''

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = if (!useConsumption) {
  name: '${baseName}-kv-${nameSuffix}'
  location: location
  tags: commonTags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: kvPurgeProtection ? true : null
  }
}

// --- API Management (v2) -------------------------------------------------------
// hybrid  -> outbound VNet integration (delegated subnet, virtualNetworkType External)
// isolated -> inbound+outbound VNet injection (virtualNetworkType Internal)
var vnetType = networkProfile == 'hybrid'
  ? 'External'
  : networkProfile == 'isolated' ? 'Internal' : 'None'
var vnetSubnetId = networkProfile == 'hybrid'
  ? vnetIntegrationSubnetId
  : networkProfile == 'isolated' ? vnetInjectionSubnetId : ''

resource apimExisting 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = if (useExistingApim) {
  name: existingApimName
}

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' = if (!useExistingApim) {
  name: '${baseName}-apim-${nameSuffix}'
  location: location
  tags: commonTags
  sku: {
    name: useConsumption ? 'Consumption' : skuByProfile[networkProfile]
    capacity: useConsumption ? 0 : 1
  }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    virtualNetworkType: useConsumption ? 'None' : vnetType
    virtualNetworkConfiguration: !useConsumption && !empty(vnetSubnetId)
      ? { subnetResourceId: vnetSubnetId }
      : null
  }
}

var apimResolvedName = useExistingApim ? existingApimName : '${baseName}-apim-${nameSuffix}'

// APIM managed identity reads secrets (named values -> Key Vault).
// With existing APIM, system-assigned MI must be enabled (deployment
// fails here with a clear error if missing).
// NOTE: if APIM is deleted and recreated (new MI, same name) while
// if the KV survives, redeploy fails with RoleAssignmentUpdateNotPermitted:
// and KV survives, redeploy fails with RoleAssignmentUpdateNotPermitted:
// delete the orphaned role assignment on KV and rerun provisioning.
resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useConsumption) {
  name: guid(keyVault.id, apimResolvedName, 'kv-secrets-user')
  scope: keyVault
  properties: {
    // ARM evaluates only the selected ternary branch: conditional access is safe
    #disable-next-line BCP318
    principalId: useExistingApim ? apimExisting.identity.principalId : apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
    )
  }
}

// Deployer can write required secrets (az keyvault secret set)
// from manifest secretRefs before named values are provisioned.
resource kvSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useConsumption && !empty(deployerPrincipalId)) {
  name: guid(keyVault.id, deployerPrincipalId, 'kv-secrets-officer')
  scope: keyVault
  properties: {
    principalId: deployerPrincipalId
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'b86a8fe4-44ce-4948-aee5-eccb2c155cd7' // Key Vault Secrets Officer
    )
  }
}

// --- Logger + diagnostics ------------------------------------------------------
// Namespaced name 'mcpkit-appinsights': on existing APIM it does not overwrite
// any existing 'appinsights' logger already configured by the client.
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = if (telemetryOn) {
  name: '${apimResolvedName}/mcpkit-appinsights'
  properties: {
    loggerType: 'applicationInsights'
    credentials: {
      // ARM evaluates only the selected ternary branch
      #disable-next-line BCP318
      instrumentationKey: useExistingAi ? appInsightsExisting.properties.InstrumentationKey : appInsights.properties.InstrumentationKey
    }
    isBuffered: true
  }
  dependsOn: [apim]
}

// CRITICAL for MCP: frontend response body bytes = 0 at global scope.
// Logging response bodies buffers and breaks MCP streaming.
// NOTE: on existing APIM this resource (fixed name 'applicationinsights')
// overrides any existing diagnostics configuration.
resource apimDiagnostics 'Microsoft.ApiManagement/service/diagnostics@2024-06-01-preview' = if (telemetryOn) {
  name: '${apimResolvedName}/applicationinsights'
  properties: {
    #disable-next-line BCP318
    loggerId: apimLogger.id
    alwaysLog: 'allErrors'
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } } // Do NOT increase: breaks MCP servers
    }
    backend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
  }
}

output apimName string = apimResolvedName
#disable-next-line BCP318
output apimGatewayUrl string = useExistingApim ? apimExisting.properties.gatewayUrl : apim.properties.gatewayUrl
#disable-next-line BCP318
output keyVaultName string = useConsumption ? '' : keyVault.name
output nativeMcpEnabled bool = !useConsumption
output policyMcpEnabled bool = gatewayProfile == 'policy-mcp-consumption'
