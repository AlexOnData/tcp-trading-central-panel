// =============================================================================
// Static Web App — Free plan, linked-backend to the Function App.
//
// Repository URL is left empty so `azd deploy` / the SWA CI workflow uploads
// the `static/` content directly with the deployment token, instead of
// activating the SWA-managed GitHub integration (which would create a
// duplicate workflow file).
//
// `staticwebapp.config.json` (which carries the `forwardingGateway.requiredHeaders`
// shared-secret block — see `03_arch §8.2 bullet 4`) lives under the SWA app
// source directory and is uploaded by the SWA deploy step, not by this module.
// =============================================================================

targetScope = 'resourceGroup'

@description('Static Web App name.')
param staticWebAppName string

@description('SWA is GA in a subset of regions. West Europe is supported; if the parent template ever picks a fallback that SWA does not support, override with the nearest supported region.')
@allowed([
  'westeurope'
  'northeurope'
  'westus2'
  'centralus'
  'eastus2'
  'eastasia'
])
param location string

@description('Tags applied to every resource.')
param tags object

@description('Resource id of the Function App that backs the linked backend.')
param functionAppResourceId string

@description('Region of the Function App. Free-plan linked backends must match the SWA region (cross-region linked backends require Standard plan).')
param functionAppRegion string

resource swa 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: location
  // `azd-service-name: web` tag links this resource to `services.web` in
  // azure.yaml — required by `azd deploy web` to find the deploy target.
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: {
    // Standard plan required: the linked-backend resource (BYO Function App)
    // is not supported on Free plan (Azure error: "SkuCode 'Free' is invalid").
    // Free plan only supports managed APIs (functions packaged inside swa/api/).
    // Cost: ~$9/month — covered by the PAYG trial credit during thesis defense;
    // tear down via `azd down` afterwards to revert to $0/month (PowerBI
    // "Publish to web" handles the perpetual public dashboard surface).
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    // Empty repo wiring → deployment via API token (`azd deploy` / CI).
    // `provider: 'None'` tells SWA there is no upstream repo integration to
    // manage; the content is uploaded via the SWA deployment-token endpoint
    // (`swa deploy` / GitHub Action). Avoids SWA generating a competing
    // workflow file inside the consumer's GitHub repo.
    repositoryUrl: ''
    branch: ''
    buildProperties: {
      skipGithubActionWorkflowGeneration: true
    }
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Disabled'
    provider: 'None'
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

resource linkedBackend 'Microsoft.Web/staticSites/linkedBackends@2023-12-01' = {
  parent: swa
  name: 'tcp-functions'
  properties: {
    backendResourceId: functionAppResourceId
    region: functionAppRegion
  }
}

output staticWebAppName string = swa.name
output staticWebAppId string = swa.id
output defaultHostname string = swa.properties.defaultHostname
