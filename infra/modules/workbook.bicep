// =============================================================================
// Application Insights workbook — Etapa 8 observability surface.
//
// Loads `infra/observability/workbook.json` (the canonical operations
// dashboard, mirrored from `infra/observability/kusto/*.kql`) into Azure
// Monitor as a `Microsoft.Insights/workbooks` resource so it appears in the
// portal at `Monitor → Workbooks → Recent`.
//
// The workbook is `shared` (`sharedTypeKind: 'shared'`) so any operator with
// `Microsoft.Insights/workbooks/read` on the resource group can open it; this
// matches the RBAC posture in `03_architecture.md §5`.
// =============================================================================

targetScope = 'resourceGroup'

@description('Azure region; matches the parent resource group.')
param location string

@description('Tags applied to the workbook resource.')
param tags object

@description('Resource id of the App Insights component the workbook queries by default.')
param appInsightsId string

@description('Display name shown in the Azure Monitor workbook gallery.')
param workbookDisplayName string = 'TCP — Operations dashboard'

// Deterministic GUID derived from the AppInsights id + a stable token so the
// workbook re-deploys cleanly without orphaning prior copies.
var workbookId = guid(appInsightsId, 'tcp-ops-workbook')

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: workbookId
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: workbookDisplayName
    serializedData: loadTextContent('../observability/workbook.json')
    version: '1.0'
    sourceId: appInsightsId
    category: 'workbook'
  }
}

output workbookId string = workbook.id
output workbookName string = workbook.name
