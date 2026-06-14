// =============================================================================
// Storage Account — Functions runtime backing + BACPAC export container.
//
// SKU: Standard_LRS (the cheapest GA SKU; free for 12 months from subscription
// creation, then ~$0.05/month at the expected steady-state size).
//
// Inlines the Function MI → Storage Blob Data Contributor role assignment on
// the `bacpac-exports` container only (per ADR-004 + §5 of `03_architecture`).
// =============================================================================

targetScope = 'resourceGroup'

@description('Storage account name. Lowercase alphanumeric, no hyphens, ≤ 24 chars.')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Function App system-assigned MI principal id (object id). When non-empty, the BACPAC-container role assignment is materialised; otherwise the storage account is provisioned without RBAC (the Function MI is unknown on the first pass — see `storage_rbac.bicep` for the second-pass assignment).')
param funcMiPrincipalId string = ''

@description('Log Analytics workspace resource id; consumed by the diagnostic setting.')
param logAnalyticsWorkspaceId string

@description('Days a BACPAC blob lives before lifecycle management deletes it. 28 matches `03_arch §11` and ADR-004.')
param bacpacRetentionDays int = 28

@description('Storage account network ACL default action. Same trade-off as `keyvault.bicep kvDefaultAction` (Etapa-11 arch10-MJ-04 fix). Free-tier default is `Allow`; the BACPAC export trigger needs the public endpoint to reach the SQL Management REST API. Flip to `Deny` once a stable IP / Flex Consumption removes the constraint.')
@allowed([
  'Allow'
  'Deny'
])
param storageDefaultAction string = 'Allow'

var bacpacContainerName = 'bacpac-exports'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    // security MA-01 / arch CR-02 trade-off: `allowSharedKeyAccess: true` is
    // required because `AzureWebJobsStorage` is injected as a connection
    // string (account-key path). Identity-based `AzureWebJobsStorage` (which
    // would let us flip this to `false`) requires additional Storage Blob /
    // Queue / Table Data roles on the Function MI and a documented migration
    // path; deferred to a follow-up pass per `docs/security/credentials_rotation.md`.
    // The MI's RBAC on the BACPAC container is the documented narrow-grant
    // path; the storage key remains in KV for the runtime backing store only.
    allowSharedKeyAccess: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      // Free tier rules out private endpoints; the Function runtime needs the
      // public endpoint to reach `AzureWebJobsStorage`.
      //
      // Etapa-11 arch10-MJ-04 fix: derive `bypass` from the (parameterised)
      // `defaultAction`. With `Allow` the field is inert (the property is
      // honoured only when `defaultAction = 'Deny'`); the conditional makes
      // the future Deny-flip a one-parameter change.
      defaultAction: storageDefaultAction
      bypass: storageDefaultAction == 'Deny' ? 'AzureServices' : 'None'
    }
    encryption: {
      services: {
        blob: {
          enabled: true
        }
        file: {
          enabled: true
        }
      }
      keySource: 'Microsoft.Storage'
    }
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource bacpacContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: bacpacContainerName
  properties: {
    publicAccess: 'None'
    metadata: {
      purpose: 'weekly-bacpac-export'
    }
  }
}

// Lifecycle: delete BACPACs older than `bacpacRetentionDays`.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-old-bacpacs'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                '${bacpacContainerName}/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: bacpacRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
}

// Diagnostic setting → Log Analytics. Blob R/W/D events feed audit and the
// BACPAC failure alert (§12.2 query 8).
resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: blobServices
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'StorageRead'
        enabled: true
      }
      {
        category: 'StorageWrite'
        enabled: true
      }
      {
        category: 'StorageDelete'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'Transaction'
        enabled: true
      }
    ]
  }
}

// -----------------------------------------------------------------------------
// Role assignments (inlined per MN-11)
// -----------------------------------------------------------------------------

// Built-in role ID: Storage Blob Data Contributor
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

// -----------------------------------------------------------------------------
// Outputs
// -----------------------------------------------------------------------------

output storageName string = storage.name
output storageId string = storage.id
output bacpacContainerName string = bacpacContainerName

// The connection string is materialised here from listKeys() — exposed as a
// @secure() output so the keyvault module can persist it without ever
// surfacing it in the deployment-history blob.
@secure()
output connectionStringSecretValue string = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

// Separate @secure() output of the bare account key (no connection-string
// envelope). Consumed by the BACPAC Export trigger (`bacpac_export.py:52`,
// env var `STORAGE_ACCOUNT_KEY`) which feeds it as the `storageKey` field
// of the Azure SQL Export REST payload. The Export API requires the bare
// key — `storageKeyType: 'StorageAccessKey'` does not accept a connection
// string. Etapa-10 fix (code10-CR-01).
@secure()
output storageAccountKey string = storage.listKeys().keys[0].value
