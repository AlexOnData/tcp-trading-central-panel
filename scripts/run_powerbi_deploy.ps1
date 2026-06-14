# Wrapper that reads the PowerBI SP credentials saved by setup_powerbi_sp.ps1
# and runs powerbi/deploy.ps1 with all required env vars populated.

$ErrorActionPreference = 'Stop'

# Ensure `az` and `TabularEditor.exe` are on PATH for the deploy.ps1 subshell.
# We deliberately DO NOT add pbi-tools to PATH: pbi-tools 1.2 only supports
# legacy V3 PBIX format and rejects the TMDL/PBIP layout in `powerbi/model/`
# ("PBIX file/project does not contain a V3 model"). TabularEditor 2 supports
# TMDL natively. deploy.ps1 prefers pbi-tools when found, so we hide it.
$azDir = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin'
$teDir = 'C:\Users\marce\bin\TabularEditor'
foreach ($d in @($azDir, $teDir)) {
  if (-not (Test-Path $d)) { throw "Required tool dir missing: $d" }
}
$env:Path = "$azDir;$teDir;$env:Path"

# Load SP creds from temp file
$creds = Get-Content -Raw 'C:\Users\marce\AppData\Local\Temp\powerbi_sp.json' | ConvertFrom-Json

# Set env vars expected by deploy.ps1
$env:POWERBI_TENANT_ID = $creds.TenantId
$env:POWERBI_CLIENT_ID = $creds.ClientId
$env:POWERBI_CLIENT_SECRET = $creds.ClientSecret
$env:POWERBI_SP_OBJECT_ID = $creds.SpObjectId
$env:POWERBI_WORKSPACE_NAME = 'TCP - Trading Central Panel'

# Pull Azure resource identifiers from azd env
$env:AZURE_TENANT_ID = $creds.TenantId
$env:AZURE_SQL_SERVER_FQDN = 'sql-tcp-prod-weu.database.windows.net'
$env:AZURE_SQL_DATABASE_NAME = 'sqldb-tcp-prod-weu'
$env:AZURE_STATIC_WEB_APP_HOSTNAME = 'zealous-stone-05c10c103.7.azurestaticapps.net'

Write-Host "=== Env vars set for deploy.ps1 ==="
Write-Host "  POWERBI_TENANT_ID:               $env:POWERBI_TENANT_ID"
Write-Host "  POWERBI_CLIENT_ID:               $env:POWERBI_CLIENT_ID"
Write-Host "  POWERBI_CLIENT_SECRET:           (length $($env:POWERBI_CLIENT_SECRET.Length))"
Write-Host "  POWERBI_SP_OBJECT_ID:            $env:POWERBI_SP_OBJECT_ID"
Write-Host "  POWERBI_WORKSPACE_NAME:          $env:POWERBI_WORKSPACE_NAME"
Write-Host "  AZURE_SQL_SERVER_FQDN:           $env:AZURE_SQL_SERVER_FQDN"
Write-Host "  AZURE_SQL_DATABASE_NAME:         $env:AZURE_SQL_DATABASE_NAME"
Write-Host "  AZURE_STATIC_WEB_APP_HOSTNAME:   $env:AZURE_STATIC_WEB_APP_HOSTNAME"
Write-Host ""

Write-Host "=== Launching powerbi/deploy.ps1 ==="
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
& pwsh -NoProfile -File (Join-Path $repoRoot 'powerbi\deploy.ps1')
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "=== deploy.ps1 exit code: $exit ==="
exit $exit
