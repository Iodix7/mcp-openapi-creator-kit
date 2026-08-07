using 'main.bicep'

// =============================================================================
// Parametri di deploy. I valori SPECIFICI di un ambiente (email, APIM
// esistente, telemetria) NON si committano qui: vivono nell'environment azd
// e arrivano via readEnvironmentVariable. Per impostarli:
//
//   azd env set PUBLISHER_EMAIL <email>
//   azd env set GATEWAY_PROFILE rest-consumption # native-mcp | rest-consumption | policy-mcp-consumption
//   azd env set EXISTING_APIM_NAME <apim>          # vuoto = il kit crea l'APIM
//   azd env set TELEMETRY_MODE existing            # new (default) | existing | none
//   azd env set EXISTING_APPINSIGHTS_NAME <ai>     # solo con TELEMETRY_MODE=existing
//   azd env set EXISTING_APPINSIGHTS_RG <rg>       # solo se l'AI e' in un RG diverso
//
// Cosi' un clone pulito con `azd up` crea un ambiente NUOVO e isolato, senza
// ereditare per sbaglio l'infrastruttura di qualcun altro.
// =============================================================================

// azd popola AZURE_PRINCIPAL_ID con l'identita' loggata: le serve il ruolo
// data-plane sul Key Vault per scrivere i segreti (vault RBAC-only)
param principalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '')
param azdEnvName = readEnvironmentVariable('AZURE_ENV_NAME', '')

param baseName = 'mcpkit'
param location = readEnvironmentVariable('AZURE_LOCATION', 'westeurope')
param publisherEmail = readEnvironmentVariable('PUBLISHER_EMAIL', 'demo@example.com')
param publisherName = 'MCP Agent Kit'
param gatewayProfile = readEnvironmentVariable('GATEWAY_PROFILE', 'native-mcp')
param networkProfile = 'public'            // public | hybrid | isolated

// Aggancio a un APIM gia' esistente (stesso RG, v2 o classico non-Consumption,
// managed identity abilitata — vedi platform.bicep). Vuoto = APIM nuovo.
param existingApimName = readEnvironmentVariable('EXISTING_APIM_NAME', '')

// Telemetria: new (default, il kit crea App Insights) | existing | none
param telemetryMode = readEnvironmentVariable('TELEMETRY_MODE', 'new')
param existingAppInsightsName = readEnvironmentVariable('EXISTING_APPINSIGHTS_NAME', '')
param existingAppInsightsResourceGroup = readEnvironmentVariable('EXISTING_APPINSIGHTS_RG', '')

// param kvPurgeProtection = true          // produzione: protegge il Key Vault
//                                         // dal purge (default false per le demo)
