targetScope = 'resourceGroup'

param sandboxGroupName string
param functionPrincipalId string
param deployerPrincipalId string

var sandboxDataOwnerRoleId = 'c24cf47c-5077-412d-a19c-45202126392c'

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' existing = {
  name: sandboxGroupName
}

resource functionSandboxDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, functionPrincipalId, sandboxDataOwnerRoleId)
  scope: sandboxGroup
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sandboxDataOwnerRoleId)
    principalId: functionPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerSandboxDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, deployerPrincipalId, sandboxDataOwnerRoleId)
  scope: sandboxGroup
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sandboxDataOwnerRoleId)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}
