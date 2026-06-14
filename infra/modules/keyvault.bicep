// =============================================================================
// Key Vault — RBAC mode, Standard SKU, public endpoint (free-tier necessity).
//
// Holds six secrets:
//   ANTHROPIC-API-KEY            — Anthropic key (user-supplied via azd).
//   SQL-ADMIN-PASSWORD-BOOTSTRAP — Bicep-generated; deleted by postprovision.
//   SQL-ADMIN-PASSWORD-EXPORT    — Same value as BOOTSTRAP, retained for the
//                                   BACPAC Export action (ADR-004).
//   STORAGE-CONNECTION-STRING    — Storage account connection string from the
//                                   storage module's @secure() output.
//   STORAGE-ACCOUNT-KEY          — Bare storage account key. Consumed by the
//                                   BACPAC Export trigger via `STORAGE_ACCOUNT_KEY`
//                                   app setting; the Azure SQL Export REST API
//                                   requires the bare key (Etapa-10 code10-CR-01).
//   SWA-FORWARDED-SECRET         — Shared secret for SWA→Function forgery
//                                   protection (§8.2 bullet 4).
//
// Inlines two role assignments:
//   Function MI    → Key Vault Secrets User
//   OIDC SP (dev)  → Key Vault Secrets Officer  (narrower than Contributor)
// =============================================================================

targetScope = 'resourceGroup'

@description('Key Vault name (must be globally unique within the AAD tenant).')
@minLength(3)
@maxLength(24)
param keyVaultName string

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Tenant id for the Key Vault.')
param tenantId string

@description('Function App system-assigned MI principal id. Receives Key Vault Secrets User on the vault.')
param funcMiPrincipalId string

@description('OIDC SP (developer / CI) principal id. Receives Key Vault Secrets Officer so postprovision can write secrets without RG-Contributor-level write to the vault.')
param oidcPrincipalId string = ''

@description('Principal type of the OIDC / deployer principal. `User` for an interactive `azd up` from a developer workstation, `ServicePrincipal` (default) under OIDC. MA-04 fix: previously hardcoded to `ServicePrincipal` which produced a soft-warning RA on user-principal deploys.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param oidcPrincipalType string = 'ServicePrincipal'

@secure()
@description('Anthropic API key value (from azd env). Persisted as `ANTHROPIC-API-KEY`.')
param anthropicApiKey string

@secure()
@description('Bootstrap SQL admin password. Stored as both BOOTSTRAP and EXPORT — postprovision deletes BOOTSTRAP after the AAD-only flip and keeps EXPORT for ADR-004.')
param sqlAdminPassword string

@secure()
@description('Storage account connection string from the storage module @secure() output.')
param storageConnectionString string

@secure()
@description('Storage account key (bare value, no connection-string envelope). Persisted as `STORAGE-ACCOUNT-KEY`. Consumed by the BACPAC Export trigger which feeds it as the `storageKey` field of the Azure SQL Export REST payload — the Export API requires the bare key (Etapa-10 code10-CR-01).')
param storageAccountKey string

@secure()
@description('Shared secret between SWA `forwardingGateway.requiredHeaders` and the Function App request validator.')
param swaForwardedSecret string

@description('Log Analytics workspace resource id; consumed by the diagnostic setting.')
param logAnalyticsWorkspaceId string

@description('Soft-delete retention in days. 7 is the KV minimum; the design accepts the trade-off so `azd down` can fully purge for the thesis cycle.')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 7

@description('Key Vault network ACL default action. `Allow` is the free-tier-compatible default (Y1 Consumption has dynamic egress IPs). Flip to `Deny` together with an `ipAllowlist` parameter once a stable runner / Flex Consumption is available. Etapa-11 arch10-MJ-04: derives the `bypass` field via the conditional in `networkAcls` below, so the future Deny-flip is a one-parameter change.')
@allowed([
  'Allow'
  'Deny'
])
param kvDefaultAction string = 'Allow'

@description('Purge protection. Disabled while the project is in the thesis cycle; flip to true post-defense. security MA-06 trade-off: with purge-protection off, an authorised `Key Vault Secrets Officer` (the OIDC SP) can `secret delete` + `secret purge` to obliterate every secret with no recovery. The 7-day soft-delete grants a small recovery window but a deliberate purge is unrecoverable. This is accepted for the thesis cycle so `azd down` can tear the vault cleanly; STATE.md tracks the "flip to true post-defense" reminder.')
param enablePurgeProtection bool = false

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    // Purge protection is a one-way switch — Azure rejects requests that try
    // to set it to false once enabled, so we only emit the property when the
    // operator explicitly requests it.
    enablePurgeProtection: enablePurgeProtection ? true : null
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      // security MA-05 trade-off: `defaultAction: 'Allow'` is required by
      // `03_arch §8.1` because the free-tier design (Y1 Consumption Function
      // App + GitHub-hosted runners) cannot present a static IP for an
      // allowlist. With `Allow` as the default action, `bypass` is inert
      // (the property is honoured only when `defaultAction = 'Deny'`); RBAC
      // + AAD remain the single auth boundary. Mitigations: vault diagnostic
      // `AuditEvent` to Log Analytics for forensic visibility; OIDC SP role
      // narrowed to `Secrets Officer`; 7-day soft-delete. Follow-up: switch
      // to `Deny` + an explicit IP allowlist once the runner egress IP is
      // stable (self-hosted runner or Flex Consumption).
      //
      // Etapa-11 fix for arch10-MJ-04: derive `bypass` from `defaultAction`
      // explicitly. Today both evaluate together (`defaultAction = 'Allow'`
      // → `bypass = 'None'`, behaviourally identical to the previous
      // hard-coded `'AzureServices'` because the field is inert anyway).
      // The conditional makes the intent explicit in the rendered ARM
      // JSON: a future Deny-flip needs no further code change.
      defaultAction: kvDefaultAction
      bypass: kvDefaultAction == 'Deny' ? 'AzureServices' : 'None'
    }
  }
}

// -----------------------------------------------------------------------------
// Secrets
//
// Note: each secret's `value` is a @secure() input — Bicep keeps it out of the
// deployment-history blob automatically.
// -----------------------------------------------------------------------------

resource secretAnthropic 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'ANTHROPIC-API-KEY'
  properties: {
    value: anthropicApiKey
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

resource secretSqlAdminBootstrap 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'SQL-ADMIN-PASSWORD-BOOTSTRAP'
  properties: {
    value: sqlAdminPassword
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

resource secretSqlAdminExport 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'SQL-ADMIN-PASSWORD-EXPORT'
  properties: {
    value: sqlAdminPassword
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

resource secretStorageConn 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'STORAGE-CONNECTION-STRING'
  properties: {
    value: storageConnectionString
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

resource secretStorageAccountKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'STORAGE-ACCOUNT-KEY'
  properties: {
    value: storageAccountKey
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

resource secretSwaForwarded 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'SWA-FORWARDED-SECRET'
  properties: {
    value: swaForwardedSecret
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

// -----------------------------------------------------------------------------
// Diagnostic settings — AuditEvent to Log Analytics.
// -----------------------------------------------------------------------------

resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: keyVault
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'AuditEvent'
        enabled: true
      }
      {
        category: 'AzurePolicyEvaluationDetails'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// -----------------------------------------------------------------------------
// Role assignments (inlined per MN-11)
// -----------------------------------------------------------------------------

// Built-in role IDs.
var roleKvSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKvSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource funcMiSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(funcMiPrincipalId)) {
  scope: keyVault
  name: guid(keyVault.id, funcMiPrincipalId, roleKvSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsUser)
    principalId: funcMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource oidcSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(oidcPrincipalId)) {
  scope: keyVault
  name: guid(keyVault.id, oidcPrincipalId, roleKvSecretsOfficer)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKvSecretsOfficer)
    principalId: oidcPrincipalId
    // MA-04 fix: principalType threaded from `main.bicep`. AAD increasingly
    // enforces this matches the actual principal kind; previously hardcoded to
    // `ServicePrincipal` even when an engineer ran `azd up` interactively.
    principalType: oidcPrincipalType
  }
}

// -----------------------------------------------------------------------------
// Outputs
// -----------------------------------------------------------------------------

output kvName string = keyVault.name
output kvId string = keyVault.id
output kvUri string = keyVault.properties.vaultUri
output secretUris object = {
  anthropic: secretAnthropic.properties.secretUri
  sqlAdminBootstrap: secretSqlAdminBootstrap.properties.secretUri
  sqlAdminExport: secretSqlAdminExport.properties.secretUri
  storageConn: secretStorageConn.properties.secretUri
  storageAccountKey: secretStorageAccountKey.properties.secretUri
  swaForwarded: secretSwaForwarded.properties.secretUri
}
