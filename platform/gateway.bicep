targetScope = 'resourceGroup'

@description('Explicit new APIM name; the installed CLI refuses existing services.')
@minLength(1)
@maxLength(50)
param apimName string

@description('Explicit Azure region; never inferred from the resource group.')
param location string

param publisherName string
param publisherEmail string

@description('Opaque kit creation marker; not authorization or proof of human approval.')
@minLength(64)
@maxLength(64)
param creationId string

@allowed(['native-mcp', 'rest-consumption', 'policy-mcp-consumption'])
param gatewayProfile string

var native = gatewayProfile == 'native-mcp'

resource gateway 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: apimName
  location: location
  tags: {
    'mcp-kit-owner': 'mcp-openapi-creator-kit'
    'mcp-kit-creation-id': creationId
  }
  sku: {
    name: native ? 'BasicV2' : 'Consumption'
    capacity: native ? 1 : 0
  }
  identity: {
    type: native ? 'SystemAssigned' : 'None'
  }
  properties: {
    publisherName: publisherName
    publisherEmail: publisherEmail
    virtualNetworkType: 'None'
    publicNetworkAccess: 'Enabled'
  }
}

output apimId string = gateway.id
output createdApimName string = gateway.name
output gatewayUrl string = gateway.properties.gatewayUrl
output selectedGatewayProfile string = gatewayProfile
output skuName string = gateway.sku.name
