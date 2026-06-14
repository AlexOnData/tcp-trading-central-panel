# One-time setup of AAD app registration for SWA Standard plan custom provider.
# SWA Standard does not support pre-configured providers; this script creates
# the AAD app, sets the reply URL, generates a client secret, and stores it in
# SWA app settings.

$ErrorActionPreference = 'Stop'
$az = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'

$swaUrl = 'https://zealous-stone-05c10c103.7.azurestaticapps.net'
$replyUrl = "$swaUrl/.auth/login/aad/callback"

Write-Host "=== Create AAD app registration ==="
$app = & $az ad app create `
  --display-name 'tcp-swa-auth' `
  --sign-in-audience AzureADMyOrg `
  --web-redirect-uris $replyUrl `
  --enable-id-token-issuance true `
  --output json | ConvertFrom-Json
Write-Host "App created. App ID: $($app.appId)"

Write-Host ""
Write-Host "=== Generate client secret (2 years) ==="
$secret = & $az ad app credential reset --id $app.appId --years 2 --output json | ConvertFrom-Json
Write-Host "Secret created (value redacted)."

Write-Host ""
Write-Host "=== Set SWA app settings (AZURE_CLIENT_ID_AAD + AZURE_CLIENT_SECRET_AAD) ==="
& $az staticwebapp appsettings set `
  --name swa-tcp-prod-weu `
  --setting-names "AZURE_CLIENT_ID_AAD=$($app.appId)" "AZURE_CLIENT_SECRET_AAD=$($secret.password)" `
  --output none
Write-Host "SWA app settings updated."

Write-Host ""
Write-Host "=== Summary ==="
Write-Host "AAD App ID:    $($app.appId)"
Write-Host "Tenant ID:     59e2da43-b57e-4087-98a0-b549f8737142"
Write-Host "Reply URL:     $replyUrl"
Write-Host "SWA settings:  AZURE_CLIENT_ID_AAD + AZURE_CLIENT_SECRET_AAD set"
