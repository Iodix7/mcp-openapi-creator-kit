// =============================================================================
// MODULE: api-with-mcp
// Imports an OpenAPI contract into APIM, applies the policy (mock/forward),
// and exposes operations as an MCP server (documented 2025-09-01-preview model:
// API type 'mcp' + child 'tools' resources, one per operation).
// Note: 1 MCP server per API; multi-API aggregation into a single server is
// implemented by the generated facade (mcpExposure.mode: facade).
// =============================================================================

@description('Name of the existing APIM instance')
param apimName string

@description('Client ID, used as naming and path prefix (for example, sample)')
param clientId string

@description('API name (for example, coverage)')
param apiName string

param displayName string

@description('OpenAPI contract YAML content (loadTextContent)')
param specValue string

@description('Policy XML to apply to the API (mock or forward+auth)')
param policyXml string

@allowed(['mock', 'external'])
param backendMode string

@description('Backend URL, required for external')
param backendUrl string = ''

@description('operationIds to expose as MCP tools. They must be APIM-safe (kebab-case): the generator validates this.')
param toolOperations array

@description('If false, deploy only the REST API without MCP server (facade mode)')
param exposeMcp bool = true

@description('APIM tags to associate with API and MCP server (must already exist on the service, for example clientId and backend mode)')
param tagIds array = []

// -----------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

// REST API imported from the contract
resource api 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: '${clientId}-${apiName}'
  // In mock mode the backend is never reached: serviceUrl must be fully
  // omitted (the APIM RP rejects null during validation). union() adds it
  // only in external.
  properties: union(
    {
      displayName: '${displayName} [${clientId}]'
      path: '${clientId}/${apiName}'
      protocols: ['https']
      format: 'openapi'
      value: specValue
      subscriptionRequired: true
      // CRITICAL: without 'query', import translates required query params into
      // template parameters and operation UrlTemplate no longer matches contract
      // paths (breaking generated facade routing).
      translateRequiredQueryParameters: 'query'
    },
    backendMode == 'mock' ? {} : { serviceUrl: backendUrl }
  )
}

// Policy: dynamic mock in demo, forward+auth in external
resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: policyXml
  }
}

// MCP server (documented model: API type 'mcp', tools as child resources)
resource mcpServer 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = if (exposeMcp) {
  parent: apim
  name: '${clientId}-${apiName}-mcp'
  properties: {
    type: 'mcp'
    displayName: '${displayName} MCP [${clientId}]'
    path: '${clientId}/${apiName}-mcp'
    protocols: ['https']
    subscriptionRequired: true
    subscriptionKeyParameterNames: {
      header: 'Ocp-Apim-Subscription-Key'
      query: 'subscription-key'
    }
  }
  dependsOn: [apiPolicy]
}

// One tool per operationId: individually manageable, with tool name equal
// to operationId itself (the generator guarantees a valid name).
resource mcpTools 'Microsoft.ApiManagement/service/apis/tools@2025-09-01-preview' = [
  for op in toolOperations: if (exposeMcp) {
    parent: mcpServer
    name: op
    properties: {
      displayName: op
      description: 'MCP tool for operation ${op} of API ${displayName}'
      operationId: resourceId(
        'Microsoft.ApiManagement/service/apis/operations',
        apimName,
        '${clientId}-${apiName}',
        op
      )
    }
  }
]

// Tag associations: filtering/grouping in the portal API list
resource apiTagLinks 'Microsoft.ApiManagement/service/apis/tags@2024-06-01-preview' = [
  for t in tagIds: {
    parent: api
    name: t
  }
]

resource mcpTagLinks 'Microsoft.ApiManagement/service/apis/tags@2024-06-01-preview' = [
  for t in tagIds: if (exposeMcp) {
    parent: mcpServer
    name: t
  }
]

output apiResourceName string = api.name
output mcpResourceName string = exposeMcp ? '${clientId}-${apiName}-mcp' : ''
output mcpServerUrl string = exposeMcp ? '${apim.properties.gatewayUrl}/${clientId}/${apiName}-mcp/mcp' : ''
