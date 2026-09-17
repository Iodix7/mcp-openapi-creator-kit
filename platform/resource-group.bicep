targetScope = 'subscription'

param resourceGroupName string
param location string
param creationId string

resource group 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: resourceGroupName
  location: location
  tags: {
    'mcp-kit-owner': 'mcp-openapi-creator-kit'
    'mcp-kit-creation-id': creationId
  }
}
