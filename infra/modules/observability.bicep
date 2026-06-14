// =============================================================================
// Observability — Log Analytics workspace + workspace-based App Insights.
//
// Sized for the 5 GB/month free ingestion grant; daily quota cap enforced
// defensively so the workspace cannot exceed 15 GB/month even on a runaway
// logger. Diagnostic settings on the other modules route into this workspace.
// =============================================================================

targetScope = 'resourceGroup'

@description('Log Analytics workspace name.')
param workspaceName string

@description('Application Insights component name.')
param appInsightsName string

@description('Azure region; must match the parent resource group.')
param location string

@description('Tags applied to both resources.')
param tags object

@description('Daily ingestion cap in GB as a string (passed through `json()` so fractional values like `0.5` survive Bicep typing). 0.5 keeps the workspace at ~15 GB/month worst-case; the rolling free grant is 5 GB/month. CR-03 fix: previously typed `int = 1` (doubling the spec cap).')
param dailyQuotaGb string = '0.5'

@description('Days the workspace retains data. 30 is the maximum for the free tier without paid retention.')
param retentionInDays int = 30

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      // PerGB2018 is the pay-as-you-go SKU that respects the 5 GB/month free
      // grant. Per-node and Capacity Reservation SKUs are not free.
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    workspaceCapping: {
      dailyQuotaGb: json(dailyQuotaGb)
    }
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    DisableIpMasking: false
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
output connectionString string = appInsights.properties.ConnectionString
output instrumentationKey string = appInsights.properties.InstrumentationKey
