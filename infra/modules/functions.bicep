// =============================================================================
// Function App — Linux Python 3.12 on Consumption (Y1) plan.
//
// Hosts five triggers: TimerTrigger_DailyGenerator, WarmupTrigger,
// HttpTrigger_AskAssistant, TimerTrigger_BacpacExport, HttpTrigger_Ping.
//
// KV-reference app settings are populated using the deterministic KV name
// passed in from `main.bicep`. The KV resource itself is created in a
// sibling module that runs in parallel; KV references resolve lazily, so by
// the time the function executes, the KV + secrets + Func-MI RBAC are in
// place. The `TCP_GENERATOR_OID` setting is intentionally empty here — the
// postprovision script populates it once the MI principal id is known and
// the corresponding row is inserted into `dim_UserRoles`.
// =============================================================================

targetScope = 'resourceGroup'

@description('App Service Plan name.')
param planName string

@description('Function App name.')
param functionAppName string

@description('Azure region.')
param location string

@description('Tags applied to every resource in this module.')
param tags object

@description('Key Vault name. Used to build deterministic KV reference URIs in app settings.')
param keyVaultName string

@description('SQL server FQDN, e.g. `sql-tcp-prod-weu.database.windows.net`.')
param sqlServerFqdn string

@description('SQL database name.')
param sqlDatabaseName string

@description('SQL server short name (without the `.database.windows.net` suffix); consumed by `bacpac_export.py` to build the Management REST URL.')
param sqlServerName string

@description('Application Insights connection string emitted by the observability module.')
param appInsightsConnectionString string

@description('Log Analytics workspace resource id; required for the diagnostic setting.')
param logAnalyticsWorkspaceId string

@description('Runtime python version. Y1 Consumption Linux supports 3.10 / 3.11 / 3.12 as of the pinned api version.')
param pythonVersion string = '3.12'

@secure()
@description('Storage account connection string for `AzureWebJobsStorage`. CR-02 fix: injected directly as a `@secure()` value (NOT a KV reference) because the Functions host resolves this setting at boot, before KV-reference resolution and Func-MI RBAC on KV are wired. Comes from `storage.bicep` @secure() output.')
param azureWebJobsStorageConnectionString string

@description('Storage account name. Used to construct `TCP_BACPAC_CONTAINER_URI` for the BACPAC export trigger.')
param storageAccountName string

@description('BACPAC container name. Used to construct `TCP_BACPAC_CONTAINER_URI`.')
param bacpacContainerName string

@description('Azure subscription id. Exposed as `AZURE_SUBSCRIPTION_ID` env var; consumed by `bacpac_export.py`.')
param subscriptionId string

@description('Resource group name. Exposed as `AZURE_RESOURCE_GROUP` env var; consumed by `bacpac_export.py`.')
param resourceGroupName string

// Deterministic KV reference URIs (no version pin → always the latest secret).
// `AzureWebJobsStorage` is intentionally NOT a KV reference (CR-02) — the
// Functions host resolves it before KV-reference resolution is wired.
var kvUriRoot = 'https://${keyVaultName}${environment().suffixes.keyvaultDns}'
var kvRef = {
  anthropic: '@Microsoft.KeyVault(SecretUri=${kvUriRoot}/secrets/ANTHROPIC-API-KEY/)'
  swaForwarded: '@Microsoft.KeyVault(SecretUri=${kvUriRoot}/secrets/SWA-FORWARDED-SECRET/)'
  sqlAdminExport: '@Microsoft.KeyVault(SecretUri=${kvUriRoot}/secrets/SQL-ADMIN-PASSWORD-EXPORT/)'
  storageAccountKey: '@Microsoft.KeyVault(SecretUri=${kvUriRoot}/secrets/STORAGE-ACCOUNT-KEY/)'
}

// HTTPS URI for the BACPAC container; consumed by `bacpac_export.py`.
var bacpacContainerUri = 'https://${storageAccountName}.blob.${environment().suffixes.storage}/${bacpacContainerName}'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  // Y1 Dynamic is the Consumption plan SKU. `reserved: true` marks it Linux.
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
    family: 'Y'
    size: 'Y1'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  // `azd-service-name: api` tag links this resource to `services.api` in
  // azure.yaml — required by `azd deploy api` to find the deploy target.
  tags: union(tags, { 'azd-service-name': 'api' })
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    reserved: true
    httpsOnly: true
    clientAffinityEnabled: false
    publicNetworkAccess: 'Enabled'
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      linuxFxVersion: 'Python|${pythonVersion}'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      // Y1 Consumption forces alwaysOn off; setting it explicitly avoids drift.
      alwaysOn: false
      http20Enabled: true
      use32BitWorkerProcess: false
      cors: {
        // Empty list means CORS denied by default. SWA's linked backend uses
        // the platform-internal Microsoft backbone, not a browser CORS hop,
        // so no origin entries are required.
        allowedOrigins: []
        supportCredentials: false
      }
      // appSetting names match `function_app/triggers/*.py` constants —
      // keep in sync; CR-04 fix aligned Bicep to the Python contract.
      appSettings: [
        // Functions runtime essentials. CR-02 fix: `AzureWebJobsStorage` is
        // the raw connection string (NOT a KV reference) — the Functions host
        // reads this at process start, before KV-reference resolution and
        // Func-MI → KV RBAC are wired.
        {
          name: 'AzureWebJobsStorage'
          value: azureWebJobsStorageConnectionString
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        // Anchors the daily NCRONTAB cron in Europe/Bucharest, DST-safe.
        {
          name: 'WEBSITE_TIME_ZONE'
          value: 'E. Europe Standard Time'
        }
        {
          name: 'PYTHONPATH'
          value: '.'
        }
        {
          name: 'PYTHON_ENABLE_WORKER_EXTENSIONS'
          value: '1'
        }
        // MA-03 fix: `SCM_DO_BUILD_DURING_DEPLOYMENT` removed — conflicts with
        // `WEBSITE_RUN_FROM_PACKAGE=1` (the documented Y1 + azd pattern is to
        // build the zip locally and upload via run-from-package, not via Kudu).
        // Observability
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        // Anthropic. MN-11 follow-up: parameterise `ANTHROPIC_BASE_URL`.
        {
          name: 'ANTHROPIC_API_KEY'
          value: kvRef.anthropic
        }
        {
          name: 'ANTHROPIC_BASE_URL'
          value: 'https://api.anthropic.com'
        }
        // Azure context — consumed by `bacpac_export.py` to build the SQL
        // Management REST URL. Names match the Python constants in
        // `function_app/triggers/bacpac_export.py` (`_ENV_SUBSCRIPTION_ID`,
        // `_ENV_RESOURCE_GROUP`).
        {
          name: 'AZURE_SUBSCRIPTION_ID'
          value: subscriptionId
        }
        {
          name: 'AZURE_RESOURCE_GROUP'
          value: resourceGroupName
        }
        // SQL connection coordinates. CR-04 fix: env-var names now match the
        // Python constants `_ENV_SQL_SERVER_NAME` and `_ENV_SQL_DATABASE_NAME`
        // in `bacpac_export.py`. `TCP_SQL_SERVER` (FQDN) is kept for backward
        // compatibility with `tcp/db.py` driver construction in Etapa 5.
        {
          name: 'TCP_SQL_SERVER'
          value: sqlServerFqdn
        }
        {
          name: 'TCP_SQL_DATABASE'
          value: sqlDatabaseName
        }
        {
          name: 'TCP_SQL_SERVER_NAME'
          value: sqlServerName
        }
        {
          name: 'TCP_SQL_DATABASE_NAME'
          value: sqlDatabaseName
        }
        {
          name: 'TCP_SQL_ADMIN_LOGIN'
          value: 'tcpadmin'
        }
        // BACPAC export target — matches `_ENV_BACPAC_CONTAINER_URI` in
        // `bacpac_export.py`. Storage key is held in KV (`STORAGE-CONNECTION-STRING`);
        // the Python code re-derives it from the conn string or via MI auth.
        {
          name: 'TCP_BACPAC_CONTAINER_URI'
          value: bacpacContainerUri
        }
        // Populated by `postprovision.{ps1,sh}` once the MI principal id is
        // registered in `dim_UserRoles` with `scope='admin'` (per ADR-003).
        {
          name: 'TCP_GENERATOR_OID'
          value: ''
        }
        // SWA `forwardingGateway` shared-secret header value, used by the
        // HTTP trigger to reject forged-principal requests (§8.2 bullet 4).
        {
          name: 'SWA_FORWARDED_SECRET'
          value: kvRef.swaForwarded
        }
        // CR-04 fix: env-var name now matches `_ENV_SQL_ADMIN_PASSWORD =
        // "SQL_ADMIN_PASSWORD_EXPORT"` in `bacpac_export.py`. Used only by
        // the BACPAC Export trigger (ADR-004).
        {
          name: 'SQL_ADMIN_PASSWORD_EXPORT'
          value: kvRef.sqlAdminExport
        }
        // Etapa-10 code10-CR-01 fix: the BACPAC Export trigger reads the
        // bare storage account key here (`_ENV_BACPAC_STORAGE_KEY =
        // "STORAGE_ACCOUNT_KEY"` in `bacpac_export.py`). Without this app
        // setting, `BacpacConfig.from_env()` would raise on first Sunday
        // 08:00 RO fire — the Function App boots, but the BACPAC trigger
        // would never complete a single export.
        {
          name: 'STORAGE_ACCOUNT_KEY'
          value: kvRef.storageAccountKey
        }
      ]
    }
  }
}

// Diagnostic setting → Log Analytics. Keeps Function logs in the same KQL
// pane as the rest of the platform.
resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: functionApp
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'FunctionAppLogs'
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

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output principalId string = functionApp.identity.principalId
// `tenantId` is the home tenant of the system-assigned MI; convenient for the
// postprovision script which uses it to construct the AAD admin SQL statement.
output tenantId string = functionApp.identity.tenantId
// System-assigned MIs do not expose a separate `clientId` on the Bicep
// resource symbol — the `principalId` (object id) is the canonical handle for
// RBAC. The postprovision script resolves the `appId` (clientId) via
// `az ad sp show --id <principalId> --query appId -o tsv` when needed; that
// value is what `DefaultAzureCredential` matches inside the Function process.
output defaultHostname string = functionApp.properties.defaultHostName
