// =============================================================================
// TCP — Trading Central Panel — root deployment template (Etapa 4).
//
// Target scope: subscription (creates the resource group, then orchestrates
// every module that lives inside it).
//
// Module dependency graph (post-CR-02 ordering — storage precedes functions
// so its @secure() connection string can land in `AzureWebJobsStorage` as a
// plain value, avoiding the host-startup KV-reference deadlock):
//
//   observability ─┬─► storage ─► functions ─┬─► storage_rbac
//                  │                          ├─► sql
//                  │                          ├─► keyvault
//                  │                          └─► swa
//                  │
//                  └─► (Log Analytics workspace feeds every module's
//                       diagnostic settings)
//
// KV references for the three "lazy" secrets (Anthropic, SWA shared secret,
// SQL admin export password) are still built deterministically from the KV
// name — by the time those settings are read at first invocation, the KV +
// secrets + Func-MI RBAC are in place. Only `AzureWebJobsStorage` is the raw
// connection string because the host resolves it before KV refs work.
// =============================================================================

targetScope = 'subscription'

// -----------------------------------------------------------------------------
// Inputs
// -----------------------------------------------------------------------------

@description('Short environment marker baked into resource names, e.g. "prod" or "dev".')
@minLength(2)
@maxLength(6)
param environmentName string = 'prod'

@description('Azure region for the resource group and every contained resource. The architecture targets West Europe; only North Europe is a tested fallback.')
@allowed([
  'westeurope'
  'northeurope'
])
param location string = 'westeurope'

@description('AAD object id of the principal running the deploy (developer or GitHub Actions OIDC SP). Receives Owner at RG scope and is registered as the AAD admin on the SQL server so bootstrap scripts and `sqlcmd -G` succeed.')
param principalId string = ''

@description('Type of the deploy principal. `User` when an engineer runs `azd up` interactively; `ServicePrincipal` (default) under CI/OIDC.')
@allowed([
  'User'
  'ServicePrincipal'
])
param principalType string = 'ServicePrincipal'

@description('AAD tenant id. Defaults to the current subscription tenant; override only when deploying cross-tenant.')
param tenantId string = subscription().tenantId

@secure()
@description('Anthropic API key passed via `azd env set ANTHROPIC_API_KEY`. Persisted immediately into Key Vault and read by the Function App via a KV reference; never written to app settings as plaintext.')
param anthropicApiKey string

@secure()
@description('Bootstrap SQL admin password. Pass an explicit value via `azd env set SQL_ADMIN_PASSWORD_BOOTSTRAP <value>` after the first successful provision (the postprovision script captures the generated value and persists it back). When empty, Bicep generates a fresh GUID-based password on the very first deploy. CR-01 fix: defaulting to an empty string keeps the value stable across re-deploys; `newGuid()` is invoked only when no value is supplied. After bootstrap, SQL auth is disabled and this credential is only used by the Azure-managed BACPAC Export action — see ADR-004.')
param sqlAdminPassword string = ''

@secure()
@description('Shared secret injected by SWA `forwardingGateway.requiredHeaders` and validated by the Function App. Pass an explicit value via `azd env set SWA_FORWARDED_SECRET <value>` after first deploy. CR-01 fix: defaulting to an empty string keeps the secret stable across re-deploys; `newGuid()` is invoked only when no value is supplied.')
param swaForwardedSecret string = ''

@description('Azure subscription id. Defaults to the current subscription; threaded through to the Function App app settings so `bacpac_export.py` can resolve the SQL Management endpoint.')
param subscriptionId string = subscription().subscriptionId

@description('Resource tags applied to every resource. The `owner` and `repo` default values are intentional placeholders enumerated in `docs/PLACEHOLDERS.md §1.6` (repo URL) and `§1.7` (owner tag); the user resolves both at thesis submission and re-runs `azd provision` to propagate the new tags. Pass a custom `tags` object via the parameters file if you want to override sooner.')
param tags object = {
  project: 'tcp'
  env: environmentName
  owner: 'TODO'
  costcenter: 'thesis'
  managedBy: 'azd'
  repo: 'TODO'
}

@description('Email recipients for the Etapa-8 alert action group. When empty no action group is created and alerts fire silently into the portal log. Set via: azd env set NOTIFICATION_EMAILS ["alex@example.com"]')
param notificationEmails array = []

@description('Network ACL default action for both the Key Vault and the Storage Account. `Allow` is the free-tier-compatible default (Y1 Consumption has dynamic egress IPs); flip to `Deny` once a stable runner / Flex Consumption is available. Etapa-11 fix for code11-MI-06: a single parameter now flips both resources together — the "one-parameter flip" promise from arch10-MJ-04.')
@allowed([
  'Allow'
  'Deny'
])
param networkDefaultAction string = 'Allow'

// CR-01: resolve secrets exactly once at compile time. If the operator supplied
// a value (steady state), use it verbatim; otherwise generate a Bicep-derived
// default. F-10 fix: Bicep restricts `newGuid()` to parameter defaults only
// (BCP065), so vars must use `uniqueString()`, which is deterministic on the
// subscription id + environment + purpose tuple. Determinism is the desired
// semantic anyway — re-deploys reuse the same secret value without operator
// intervention. The `Aa1!` suffix on the SQL password meets the server-side
// complexity requirements (uppercase + lowercase + digit + special character).
// Bicep does not allow `@secure()` on vars — the values flow into `@secure()`
// module parameters below, which keeps them out of the deployment history.
var resolvedSqlAdminPassword = empty(sqlAdminPassword) ? 'P${uniqueString(subscription().id, environmentName, 'sqlAdmin')}!Aa1' : sqlAdminPassword
var resolvedSwaForwardedSecret = empty(swaForwardedSecret) ? uniqueString(subscription().id, environmentName, 'swa-forwarded-secret') : swaForwardedSecret

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

// Compact 3-letter region suffix used in every resource name. Bicep does not
// support user-defined functions at subscription scope across all api versions
// — inline `?:` chain keeps it readable and self-contained.
var regionShortMap = {
  westeurope: 'weu'
  northeurope: 'neu'
}
var shortRegion = contains(regionShortMap, location) ? regionShortMap[location] : 'xxx'

var resourcePrefix = 'tcp-${environmentName}-${shortRegion}'
var resourceGroupName = 'rg-${resourcePrefix}'

// Deterministic resource names. Storage account drops hyphens (Azure rule).
var names = {
  resourceGroup: resourceGroupName
  logAnalytics: 'log-${resourcePrefix}'
  appInsights: 'ai-${resourcePrefix}'
  keyVault: 'kv-${resourcePrefix}'
  storage: 'sttcp${environmentName}${shortRegion}'
  sqlServer: 'sql-${resourcePrefix}'
  sqlDatabase: 'sqldb-${resourcePrefix}'
  appServicePlan: 'asp-${resourcePrefix}'
  functionApp: 'func-${resourcePrefix}'
  staticWebApp: 'swa-${resourcePrefix}'
}

// -----------------------------------------------------------------------------
// Resource Group
// -----------------------------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: names.resourceGroup
  location: location
  tags: tags
}

// Deployer (developer / OIDC SP) gets Owner at RG scope for the duration of
// the thesis project (per `03_arch §5`). The role assignment is scoped to the
// RG, not the subscription, so it cannot escalate beyond TCP. F-10 fix:
// implemented via `modules/rg_role_assignment.bicep` because a role-assignment
// resource declared at subscription scope cannot target a child RG directly
// (BCP139); modules are the canonical Bicep cross-scope mechanism.
module deployerRgOwner 'modules/rg_role_assignment.bicep' = if (!empty(principalId)) {
  name: 'deployer-rg-owner'
  scope: rg
  params: {
    principalId: principalId
    principalType: principalType
    // Built-in role: Owner
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8e3af657-a8ff-443c-a75c-2fe8c4bcb635')
    // Stable GUID — re-deploys must produce the same role assignment id.
    roleAssignmentName: guid(rg.id, principalId, 'Owner')
  }
}

// -----------------------------------------------------------------------------
// 1. Observability (Log Analytics + App Insights) — no dependencies
// -----------------------------------------------------------------------------

module observability 'modules/observability.bicep' = {
  name: 'observability'
  scope: rg
  params: {
    workspaceName: names.logAnalytics
    appInsightsName: names.appInsights
    location: location
    tags: tags
  }
}

// -----------------------------------------------------------------------------
// 2. Function App skeleton (plan + site + system-assigned MI)
//
// KV-reference app settings are wired here using the deterministic KV name
// (the KV is created in step 5 below). Azure resolves the references lazily;
// by the time the function executes, KV + secrets + RBAC are all in place.
// -----------------------------------------------------------------------------

// CR-02 fix: storage is provisioned BEFORE the Function App so the storage
// connection string can be injected directly as a plain `@secure()` value into
// `AzureWebJobsStorage` (instead of a KV reference). The Functions runtime
// reads this setting at boot — before KV-reference resolution is wired —
// which previously caused a host startup deadlock on first deploy. The Function
// MI principal id is passed AFTER `functions` resolves, via the second storage
// pass below (a no-op for the account itself; only the RBAC line runs).
module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    storageAccountName: names.storage
    location: location
    tags: tags
    // Empty principalId on first pass — the role assignment inside `storage` is
    // guarded by `!empty(funcMiPrincipalId)`. Re-applied below once the MI is known.
    funcMiPrincipalId: ''
    logAnalyticsWorkspaceId: observability.outputs.workspaceId
    storageDefaultAction: networkDefaultAction
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  scope: rg
  params: {
    planName: names.appServicePlan
    functionAppName: names.functionApp
    location: location
    tags: tags
    keyVaultName: names.keyVault
    sqlServerFqdn: '${names.sqlServer}${environment().suffixes.sqlServerHostname}'
    sqlDatabaseName: names.sqlDatabase
    sqlServerName: names.sqlServer
    appInsightsConnectionString: observability.outputs.connectionString
    logAnalyticsWorkspaceId: observability.outputs.workspaceId
    // CR-02 fix: inject the raw storage connection string directly so the
    // Functions host can boot without waiting on KV-reference resolution.
    azureWebJobsStorageConnectionString: storage.outputs.connectionStringSecretValue
    storageAccountName: storage.outputs.storageName
    bacpacContainerName: storage.outputs.bacpacContainerName
    subscriptionId: subscriptionId
    resourceGroupName: resourceGroupName
  }
}

// Function MI → Storage Blob Data Contributor on the `bacpac-exports` container.
// Inlined here (scoped via `existing` symbols against the resource group) so we
// don't have to re-invoke `storage.bicep` purely to wire one role assignment.
module storageRbac 'modules/storage_rbac.bicep' = {
  name: 'storage-rbac'
  scope: rg
  params: {
    storageAccountName: storage.outputs.storageName
    bacpacContainerName: storage.outputs.bacpacContainerName
    funcMiPrincipalId: functions.outputs.principalId
  }
}

// -----------------------------------------------------------------------------
// 4. SQL (server + Free Offer database + AAD admin + role assignments)
// -----------------------------------------------------------------------------
//
// CR-04 fix: the AAD admin block has been moved out of Bicep (`administrators`
// is no longer set in `sql.bicep`). The postprovision script registers the AAD
// admin imperatively via `az sql server ad-admin create` and then flips the
// server to `azureADOnlyAuthentication = true`. This prevents the Bicep
// template from silently resetting the server back to SQL-auth on every
// re-deploy. `principalId` is still threaded through so the postprovision
// script can read it from the output bundle.

module sql 'modules/sql.bicep' = {
  name: 'sql'
  scope: rg
  params: {
    sqlServerName: names.sqlServer
    sqlDatabaseName: names.sqlDatabase
    location: location
    tags: tags
    funcMiPrincipalId: functions.outputs.principalId
    sqlAdminPassword: resolvedSqlAdminPassword
    logAnalyticsWorkspaceId: observability.outputs.workspaceId
  }
}

// -----------------------------------------------------------------------------
// 5. Key Vault (secrets + RBAC for Function MI and OIDC SP)
// -----------------------------------------------------------------------------

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    keyVaultName: names.keyVault
    location: location
    tags: tags
    tenantId: tenantId
    funcMiPrincipalId: functions.outputs.principalId
    oidcPrincipalId: principalId
    // MA-04 fix: thread principal type through so the OIDC SP role assignment
    // uses the correct value (User on interactive `azd up`, ServicePrincipal in CI).
    oidcPrincipalType: principalType
    anthropicApiKey: anthropicApiKey
    sqlAdminPassword: resolvedSqlAdminPassword
    storageConnectionString: storage.outputs.connectionStringSecretValue
    // Etapa-10 code10-CR-01 fix: the BACPAC Export trigger needs the bare
    // storage account key (the Azure SQL Export REST API requires it).
    storageAccountKey: storage.outputs.storageAccountKey
    swaForwardedSecret: resolvedSwaForwardedSecret
    logAnalyticsWorkspaceId: observability.outputs.workspaceId
    kvDefaultAction: networkDefaultAction
  }
}

// -----------------------------------------------------------------------------
// 6. Static Web App (Free) — linked backend to the Function App
// -----------------------------------------------------------------------------

module swa 'modules/swa.bicep' = {
  name: 'swa'
  scope: rg
  params: {
    staticWebAppName: names.staticWebApp
    location: location
    tags: tags
    functionAppResourceId: functions.outputs.functionAppId
    functionAppRegion: location
  }
}

// -----------------------------------------------------------------------------
// 7. Observability — workbook + alert rules (Etapa 8)
// -----------------------------------------------------------------------------
//
// `workbook` and `alerts` are the last modules to provision so they can pin
// against the resource ids of every prior module: App Insights (queries +
// metric scope) and SQL Database (the metric alert resource id). Action group
// is conditional on `notificationEmails` so the first deploy still succeeds
// without an email recipient configured.

// Explicit dependsOn closes the no-op-re-deploy race surfaced by arch-MA-02:
// Bicep's implicit output-ref edges keep these last on first deploy, but on a
// zero-change re-apply the workbook + alerts can race against in-flight
// metadata operations on observability / sql.
module workbook 'modules/workbook.bicep' = {
  name: 'workbook'
  scope: rg
  dependsOn: [observability]
  params: {
    location: location
    tags: tags
    appInsightsId: observability.outputs.appInsightsId
  }
}

module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  scope: rg
  dependsOn: [observability, sql]
  params: {
    location: location
    tags: tags
    appInsightsId: observability.outputs.appInsightsId
    logAnalyticsWorkspaceId: observability.outputs.workspaceId
    sqlDatabaseId: sql.outputs.databaseId
    notificationEmails: notificationEmails
  }
}

// -----------------------------------------------------------------------------
// Outputs — consumed by `azd env get-values` and by `postprovision.ps1`.
// -----------------------------------------------------------------------------

// `AZURE_*` prefix matches the azd output convention and the names the
// `postprovision.{ps1,sh}` scripts consume via `azd env get-values`.
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output AZURE_SUBSCRIPTION_ID string = subscriptionId
output AZURE_TENANT_ID string = tenantId
output AZURE_FUNCTION_APP_NAME string = functions.outputs.functionAppName
output AZURE_FUNCTION_APP_PRINCIPAL_ID string = functions.outputs.principalId
output AZURE_FUNCTION_APP_TENANT_ID string = functions.outputs.tenantId
output AZURE_FUNCTION_APP_DEFAULT_HOSTNAME string = functions.outputs.defaultHostname
// MA-02: system-assigned MI does not expose `clientId` on the resource symbol.
// The postprovision script resolves the appId via
// `az ad sp show --id <principalId> --query appId -o tsv` and writes it back
// into the Function App's `AZURE_CLIENT_ID` app setting. Bicep emits only the
// principalId (object id) and a hint pseudonym output for discoverability.
output AZURE_FUNCTION_APP_CLIENT_ID string = ''
output AZURE_KEYVAULT_URI string = keyvault.outputs.kvUri
output AZURE_KEYVAULT_NAME string = keyvault.outputs.kvName
output AZURE_SQL_SERVER_FQDN string = sql.outputs.serverFqdn
output AZURE_SQL_SERVER_NAME string = sql.outputs.serverName
output AZURE_SQL_DATABASE_NAME string = sql.outputs.databaseName
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.storageName
output AZURE_STATIC_WEB_APP_HOSTNAME string = swa.outputs.defaultHostname
output AZURE_STATIC_WEB_APP_NAME string = swa.outputs.staticWebAppName
output AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING string = observability.outputs.connectionString
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = observability.outputs.workspaceId
output AZURE_OBSERVABILITY_WORKBOOK_ID string = workbook.outputs.workbookId
output AZURE_OBSERVABILITY_ALERT_RULES array = alerts.outputs.alertRuleNames
