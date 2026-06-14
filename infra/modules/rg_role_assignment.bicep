// =============================================================================
// Generic resource-group-scoped role assignment helper module.
//
// F-10 fix: extracted from main.bicep because a `Microsoft.Authorization/
// roleAssignments` resource declared at subscription scope cannot use
// `scope: rg` to target a child resource group (BCP139). The canonical Bicep
// pattern is to invoke this module from main.bicep with `scope: rg` set on
// the module reference; this file then declares the role assignment at
// `targetScope = 'resourceGroup'`, where it is a valid first-class resource.
// =============================================================================

targetScope = 'resourceGroup'

@description('AAD object id of the principal receiving the role.')
param principalId string

@description('Principal kind. `User` for interactive deploys; `ServicePrincipal` for CI/OIDC; `Group` for AAD security groups.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param principalType string

@description('Fully-qualified role definition resource id (use `subscriptionResourceId` in the caller).')
param roleDefinitionId string

@description('Stable name (GUID) for the role assignment so re-deploys are idempotent.')
param roleAssignmentName string

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: roleAssignmentName
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: principalType
  }
}

output roleAssignmentId string = roleAssignment.id
