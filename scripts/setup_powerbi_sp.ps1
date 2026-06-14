# One-time setup of AAD app registration for PowerBI Service Principal.
# Creates the SP, generates a client secret, and prints the values needed
# for `powerbi/deploy.ps1`. The secret is shown ONCE; copy it immediately.

$ErrorActionPreference = 'Stop'
$az = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'

$DisplayName = 'tcp-powerbi-sp'
$TenantId = '59e2da43-b57e-4087-98a0-b549f8737142'

Write-Host "=== Create AAD app registration (single-tenant) ==="
$app = & $az ad app create `
  --display-name $DisplayName `
  --sign-in-audience AzureADMyOrg `
  --output json | ConvertFrom-Json
Write-Host "App created. App ID: $($app.appId)"

Write-Host ""
Write-Host "=== Ensure Service Principal exists in tenant ==="
# `az ad app create` creates the application object; the SP (enterprise app
# entry) must exist separately for PowerBI to recognise it.
$sp = & $az ad sp create --id $app.appId --output json 2>&1 | ConvertFrom-Json
if (-not $sp.id) {
  # SP may already exist; look it up
  $sp = & $az ad sp show --id $app.appId --output json | ConvertFrom-Json
}
Write-Host "Service Principal Object ID: $($sp.id)"

Write-Host ""
Write-Host "=== Generate client secret (2 years) ==="
$secret = & $az ad app credential reset --id $app.appId --years 2 --output json | ConvertFrom-Json
Write-Host "Secret created (value below — copy it now, it will not be shown again)."

Write-Host ""
Write-Host "=== Summary (for deploy.ps1) ==="
Write-Host "POWERBI_TENANT_ID:       $TenantId"
Write-Host "POWERBI_CLIENT_ID:       $($app.appId)"
Write-Host "POWERBI_CLIENT_SECRET:   $($secret.password)"
Write-Host "POWERBI_SP_OBJECT_ID:    $($sp.id)"
Write-Host ""
Write-Host "These are also being written to a temp file for the next step to read:"
$out = [PSCustomObject]@{
  TenantId = $TenantId
  ClientId = $app.appId
  ClientSecret = $secret.password
  SpObjectId = $sp.id
}
$out | ConvertTo-Json | Set-Content -Path 'C:\Users\marce\AppData\Local\Temp\powerbi_sp.json' -NoNewline
Write-Host "Wrote C:\Users\marce\AppData\Local\Temp\powerbi_sp.json"
