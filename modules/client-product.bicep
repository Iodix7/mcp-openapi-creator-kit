// =============================================================================
// MODULE: client-product
// One APIM Product per client: this is the isolation and delivery unit.
// "Give the client only what they need" = subscriptions on this product.
// =============================================================================

param apimName string
param clientId string
param displayName string

@description('Resource names of APIs and MCP servers to associate with the product')
param apiResourceNames array

@description('Per-subscription rate limit (calls/minute) from standards manifest')
param callsPerMinute int = 60

@description('Inbound auth from manifest: subscriptionKey (pilot) or entraJwt (production)')
@allowed(['subscriptionKey', 'entraJwt'])
param inboundAuthMode string = 'subscriptionKey'

@description('Client Entra ID tenant (required with inboundAuthMode=entraJwt)')
param jwtTenantId string = ''

@description('Expected audience in token (required with inboundAuthMode=entraJwt)')
param jwtAudience string = ''

@description('APIM tags to associate with the product (must already exist on the service)')
param tagIds array = []

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

resource product 'Microsoft.ApiManagement/service/products@2024-06-01-preview' = {
  parent: apim
  name: '${clientId}-product'
  properties: {
    displayName: displayName
    description: 'Access to client ${clientId} APIs and MCP servers'
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}

resource productApis 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = [
  for name in apiResourceNames: {
    parent: product
    name: name
  }
]

// entraJwt: centralized validate-jwt at product level (in addition to
// subscription key, defense in depth). subscriptionKey: key only.
var jwtPolicy = inboundAuthMode == 'entraJwt'
  ? '<validate-jwt header-name="Authorization" failed-validation-httpcode="401" failed-validation-error-message="Entra ID token missing or invalid"><openid-config url="${environment().authentication.loginEndpoint}${jwtTenantId}/v2.0/.well-known/openid-configuration" /><audiences><audience>${jwtAudience}</audience></audiences></validate-jwt>'
  : ''

// Product-level rate limit: protects backends from aggressive agent retries
resource productPolicy 'Microsoft.ApiManagement/service/products/policies@2024-06-01-preview' = {
  parent: product
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><base />${jwtPolicy}<rate-limit calls="${callsPerMinute}" renewal-period="60" /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
  }
}

// Pilot subscription: retrieve the key post-deploy (never in repo) with:
// az rest --method POST --uri "<subscription-resource-id>/listSecrets?api-version=2024-06-01-preview" --query primaryKey
// (az apim subscription show does NOT return keys; see HANDOVER.md)
resource subscription 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = {
  parent: apim
  name: '${clientId}-pilot'
  properties: {
    scope: product.id
    displayName: '${displayName} - Pilot subscription'
    state: 'active'
  }
  dependsOn: [productApis]
}

resource productTagLinks 'Microsoft.ApiManagement/service/products/tags@2024-06-01-preview' = [
  for t in tagIds: {
    parent: product
    name: t
  }
]

output productName string = product.name
output subscriptionResourceName string = subscription.name
