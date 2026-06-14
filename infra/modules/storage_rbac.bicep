// =============================================================================
// Storage RBAC — Function MI → Storage Blob Data Contributor on `bacpac-exports`.
//
// Separated from `storage.bicep` so the storage account can be provisioned
// BEFORE the Function App (CR-02 fix: avoids the KV-reference boot deadlock for
// `AzureWebJobsStorage`), and the role assignment lands once the Function MI
// principal id is known. Scoped to the container, not the storage account,
// per ADR-004 + `03_arch §5`.
// =============================================================================

targetScope = 'resourceGroup'

@description('Storage account name (already provisioned by `storage.bicep`).')
param storageAccountName string

@description('BACPAC container name (already provisioned by `storage.bicep`).')
param bacpacContainerName string

@description('Function App system-assigned MI principal id (object id).')
param funcMiPrincipalId string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource bacpacContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobServices
  name: bacpacContainerName
}

// Built-in role: Storage Blob Data Contributor
var roleStorageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource funcMiBacpacContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(funcMiPrincipalId)) {
  // Scope narrowed to the container, not the storage account. Function MI can
  // only write to `bacpac-exports`, not to the Functions runtime containers.
  scope: bacpacContainer
  name: guid(bacpacContainer.id, funcMiPrincipalId, roleStorageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataContributor)
    principalId: funcMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}
