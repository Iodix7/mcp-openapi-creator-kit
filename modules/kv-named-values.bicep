// =============================================================================
// MODULE: kv-named-values
// Creates APIM named values linked to Key Vault for secretRefs declared
// in the client manifest. Policies reference them as {{secretRef}}.
// PREREQUISITE: the secret must ALREADY exist in Key Vault with the same name
// (az keyvault secret set --vault-name <kv> --name <secretRef> --value ...).
// APIM reads it with its own managed identity (role assigned in platform).
// =============================================================================

param apimName string
param keyVaultName string

@description('Names of Key Vault secrets to expose as named values (from manifest secretRefs)')
param secretRefs array

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

resource namedValues 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = [
  for ref in secretRefs: {
    parent: apim
    name: ref
    properties: {
      displayName: ref
      secret: true
      keyVault: {
        secretIdentifier: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${ref}'
      }
    }
  }
]

output namedValueNames array = secretRefs
