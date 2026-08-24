using 'main.bicep'

// =============================================================================
// Deployment parameters. Environment-specific values live in the azd
// environment and are read through readEnvironmentVariable:
//
//   azd env set PUBLISHER_EMAIL <email>
//   azd env set GATEWAY_PROFILE rest-consumption # native-mcp | rest-consumption | policy-mcp-consumption
//   azd env set NETWORK_PROFILE hybrid             # public | hybrid | isolated
//   azd env set VNET_INTEGRATION_SUBNET_ID <id>    # required for hybrid
//   azd env set VNET_INJECTION_SUBNET_ID <id>      # required for isolated
//   azd env set EXISTING_APIM_NAME <apim>          # empty = create APIM
//   azd env set TELEMETRY_MODE existing            # new (default) | existing | none
//   azd env set EXISTING_APPINSIGHTS_NAME <ai>     # required for existing telemetry
//   azd env set EXISTING_APPINSIGHTS_RG <rg>       # optional, defaults to current RG
//
// A clean clone therefore creates a new isolated environment by default.
// =============================================================================

// azd provides AZURE_PRINCIPAL_ID for the Key Vault data-plane role assignment.
param principalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '')
param azdEnvName = readEnvironmentVariable('AZURE_ENV_NAME', '')

param baseName = 'mcpkit'
param location = readEnvironmentVariable('AZURE_LOCATION', 'westeurope')
param publisherEmail = readEnvironmentVariable('PUBLISHER_EMAIL', 'demo@example.com')
param publisherName = 'MCP Agent Kit'
param gatewayProfile = readEnvironmentVariable('GATEWAY_PROFILE', 'native-mcp')
param networkProfile = readEnvironmentVariable('NETWORK_PROFILE', 'public')
param vnetIntegrationSubnetId = readEnvironmentVariable('VNET_INTEGRATION_SUBNET_ID', '')
param vnetInjectionSubnetId = readEnvironmentVariable('VNET_INJECTION_SUBNET_ID', '')

// Existing APIM in the same resource group; empty creates a new APIM.
param existingApimName = readEnvironmentVariable('EXISTING_APIM_NAME', '')

// Telemetry: new (default) | existing | none
param telemetryMode = readEnvironmentVariable('TELEMETRY_MODE', 'new')
param existingAppInsightsName = readEnvironmentVariable('EXISTING_APPINSIGHTS_NAME', '')
param existingAppInsightsResourceGroup = readEnvironmentVariable('EXISTING_APPINSIGHTS_RG', '')

// param kvPurgeProtection = true          // produzione: protegge il Key Vault
//                                         // dal purge (default false per le demo)
