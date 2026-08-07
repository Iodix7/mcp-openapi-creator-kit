// Stateless MCP Streamable HTTP endpoint on APIM Consumption via policy.
param apimName string
param resourceName string
param displayName string
param apiPath string
param productName string
param clientTag string
param policyXml string

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

resource product 'Microsoft.ApiManagement/service/products@2024-06-01-preview' existing = {
  parent: apim
  name: productName
}

resource api 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: resourceName
  properties: {
    displayName: displayName
    description: 'Stateless MCP generated from client contracts; no backend compute.'
    path: apiPath
    protocols: ['https']
    format: 'openapi'
    value: loadTextContent('policy-mcp.openapi.yaml')
    subscriptionRequired: true
  }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: policyXml
  }
}

resource productApi 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = {
  parent: product
  name: api.name
  dependsOn: [policy]
}

resource apiTag 'Microsoft.ApiManagement/service/apis/tags@2024-06-01-preview' = {
  parent: api
  name: clientTag
}

output serverUrl string = '${apim.properties.gatewayUrl}/${apiPath}/mcp'
output apiResourceName string = api.name
