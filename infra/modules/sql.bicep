// =============================================================================
// SQL Server + Azure SQL Database (Free Offer).
//
// API pinned to `2023-08-01-preview` to access the `useFreeLimit` and
// `freeLimitExhaustionBehavior` properties (per `03_arch §4.2 MN-02`).
//
// Server is created with SQL auth enabled at bootstrap (so the first schema
// apply via `sqlcmd` works). The postprovision script flips the server to
// AAD-only once the schema is in place (per ADR-003 / `03_arch §6.5`).
//
// Inlines Function MI → SQL DB Contributor at database scope (per ADR-004).
// =============================================================================

targetScope = 'resourceGroup'

@description('Logical SQL server name (`sql-tcp-<env>-<region>`).')
param sqlServerName string

@description('Database name (`sqldb-tcp-<env>-<region>`).')
param sqlDatabaseName string

@description('Azure region.')
param location string

@description('Tags applied to every resource.')
param tags object

@description('Function App MI principal id. Receives SQL DB Contributor at database scope; also registered as a contained DB user by the postprovision SQL bootstrap (per ADR-003).')
param funcMiPrincipalId string

@secure()
@description('Bootstrap SQL admin password. Used only during the first schema apply; after the AAD-only flip the credential is retained solely for the BACPAC Export API call (ADR-004).')
param sqlAdminPassword string

@description('Log Analytics workspace resource id; consumed by the diagnostic setting.')
param logAnalyticsWorkspaceId string

@description('SQL admin login. Hardcoded to `tcpadmin` for predictability across re-deploys.')
param sqlAdminLogin string = 'tcpadmin'

@description('Database collation. UTF-8, Romanian-safe, case-insensitive.')
param collation string = 'Latin1_General_100_CI_AS_SC_UTF8'

@description('Auto-pause delay in minutes. 60 matches the Free Offer cap.')
param autoPauseDelayMinutes int = 60

@description('Min capacity in vCores. 0.5 is the Free Offer floor.')
param minCapacity string = '0.5'

@description('Max database size in bytes. 32 GiB is the Free Offer cap.')
param maxSizeBytes int = 34359738368

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: location
  tags: tags
  properties: {
    version: '12.0'
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: 'Disabled'
    // CR-04 fix: the AAD admin + `azureADOnlyAuthentication` flip are no longer
    // declared in Bicep. The postprovision script registers the AAD admin
    // imperatively via `az sql server ad-admin create` after the first schema
    // apply, and then flips the server to AAD-only with
    // `az sql server ad-only-auth enable`. Declaring `administrators` here
    // re-applied `azureADOnlyAuthentication: false` on every redeploy, silently
    // reverting the post-bootstrap hardening. This module now sets only the
    // SQL-auth admin (needed for the bootstrap schema apply and BACPAC export).
  }
}

// security MJ-04 / threat-model annotation:
// The `AllowAllAzureServices` virtual firewall rule permits every Azure
// tenant's outbound traffic (not just our Function App MI) to reach the
// server's TLS endpoint. Authentication is still required, but the surface is
// the entire Azure backbone. This rule is necessary because the Function App's
// Consumption-plan outbound IPs are dynamic and there is no service-tag-scoped
// firewall rule available on Y1. Mitigations:
//   1. AAD-only auth flipped on by postprovision within minutes of the first
//      deploy (~120-bit GUID password during the bootstrap window).
//   2. SQL audit events flow to Log Analytics for forensic visibility.
//   3. Bootstrap-window duration documented in `docs/security/bootstrap_window.md`.
// Follow-up: migrate to Flex Consumption (outbound static IPs) and replace
// this rule with an explicit IP allowlist.
resource fwAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: sqlDatabaseName
  location: location
  tags: tags
  sku: {
    // Free Offer — Serverless General Purpose, Gen5, 1 vCore.
    name: 'GP_S_Gen5_1'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 1
  }
  properties: {
    collation: collation
    maxSizeBytes: maxSizeBytes
    autoPauseDelay: autoPauseDelayMinutes
    minCapacity: json(minCapacity)
    zoneRedundant: false
    readScale: 'Disabled'
    requestedBackupStorageRedundancy: 'Local'
    isLedgerOn: false
    // Free Offer flags TEMPORARILY DISABLED (2026-05-18). The subscription
    // is PayAsYouGo (verified via subscriptionPolicies.quotaId), but ARM
    // still returns `SkuCode 'Free' is invalid` on `useFreeLimit: true` —
    // likely a quota-refresh lag after Free Trial → PAYG conversion. The DB
    // deploys as paid GP_S_Gen5_1 serverless (auto-pause after 60 min idle),
    // costing ~$3-9/month under demo workload (well within remaining credit).
    // Re-enable both flags via a follow-up `azd provision` once Azure
    // recognises the Free Offer eligibility (typically 24-72h post-upgrade).
    // useFreeLimit: true
    // freeLimitExhaustionBehavior: 'AutoPause'
  }
}

// Diagnostic setting → Log Analytics. Audit-events + insights for the
// observability dashboard and the SQL vCore-budget Kusto query.
resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: sqlDatabase
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'SQLSecurityAuditEvents'
        enabled: true
      }
      {
        category: 'SQLInsights'
        enabled: true
      }
      {
        category: 'AutomaticTuning'
        enabled: true
      }
      {
        category: 'DatabaseWaitStatistics'
        enabled: true
      }
      {
        category: 'Errors'
        enabled: true
      }
      {
        category: 'QueryStoreRuntimeStatistics'
        enabled: true
      }
      {
        category: 'Timeouts'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'Basic'
        enabled: true
      }
      {
        category: 'InstanceAndAppAdvanced'
        enabled: true
      }
    ]
  }
}

// -----------------------------------------------------------------------------
// Role assignments (inlined per MN-11)
// -----------------------------------------------------------------------------

// Built-in role: SQL DB Contributor (control-plane only; does not grant
// data-plane access — the Function App MI gets its data-plane permissions by
// being mapped to the `tcp_generator` and `tcp_ai_assistant` DB roles inside
// the database, via the postprovision SQL bootstrap.)
var roleSqlDbContributor = '9b7fa17d-e63e-47b0-bb0a-15c516ac86ec'

resource funcMiSqlDbContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(funcMiPrincipalId)) {
  scope: sqlDatabase
  name: guid(sqlDatabase.id, funcMiPrincipalId, roleSqlDbContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSqlDbContributor)
    principalId: funcMiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// -----------------------------------------------------------------------------
// Outputs
// -----------------------------------------------------------------------------

output serverName string = sqlServer.name
output serverFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = sqlDatabase.name
output databaseId string = sqlDatabase.id
