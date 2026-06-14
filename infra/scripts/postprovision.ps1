#Requires -Version 7.0

<#
.SYNOPSIS
Post-provision script for TCP — Trading Central Panel.

Runs after 'azd provision' to finalize RLS setup, AAD-only auth flip, and secret management.

.NOTES
Must be invoked with the azd environment context active (azd env get-values).
Idempotent — safe to run multiple times.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Info {
  param([string]$Message)
  Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-Success {
  param([string]$Message)
  Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
  param([string]$Message)
  Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

# Parse azd environment values.
# Note: `ConvertFrom-StringData` would leave the surrounding double-quotes
# emitted by `azd env get-values` as literal characters in each value,
# breaking every downstream `az` call (e.g. `--server-name '"sql-..."'`
# resolves to ResourceNotFound). Use a regex that strips the quotes.
Write-Info "Reading azd environment..."
$env_values = @{}
foreach ($line in (azd env get-values)) {
  if ($line -match '^([^=]+)=(.*)$') {
    $env_values[$matches[1].Trim()] = $matches[2].Trim('"')
  }
}

$sqlServerName = $env_values['AZURE_SQL_SERVER_NAME']
$sqlDatabaseName = $env_values['AZURE_SQL_DATABASE_NAME']
$resourceGroupName = $env_values['AZURE_RESOURCE_GROUP']
$functionAppName = $env_values['AZURE_FUNCTION_APP_NAME']
$kvName = $env_values['AZURE_KEYVAULT_NAME']
$functionAppPrincipalId = $env_values['AZURE_FUNCTION_APP_PRINCIPAL_ID']
$azurePrincipalId = $env_values['AZURE_PRINCIPAL_ID']

if (-not $sqlServerName -or -not $sqlDatabaseName -or -not $resourceGroupName) {
  throw "Missing required environment variables. Check azd env output."
}
if (-not $azurePrincipalId) {
  throw "AZURE_PRINCIPAL_ID not found in azd env. Run 'azd env set AZURE_PRINCIPAL_ID <object-id>' before postprovision."
}

Write-Info "Parsed configuration:"
Write-Info "  SQL Server: $sqlServerName"
Write-Info "  SQL Database: $sqlDatabaseName"
Write-Info "  Resource Group: $resourceGroupName"
Write-Info "  Function App: $functionAppName"
Write-Info "  Function App MI OID: $functionAppPrincipalId"
Write-Info "  AAD admin candidate OID: $azurePrincipalId"
Write-Info "  Key Vault: $kvName"

$sqlServerFqdn = "$sqlServerName.database.windows.net"

# Step 0a: Register the deploying principal as AAD admin on the SQL server.
# Required because sql.bicep deliberately omits the `administrators` block
# (preferring imperative registration here so the admin identity differs
# across CI vs interactive deploy). Without this step, the `sqlcmd -G` calls
# below fail with `Login failed for token-identified principal`.
# Idempotent: a second call against an existing AAD admin is a no-op.
Write-Info "Step 0a: Registering AAD admin on SQL Server..."

try {
  az sql server ad-admin create `
    --resource-group $resourceGroupName `
    --server-name $sqlServerName `
    --display-name "tcp-deployer" `
    --object-id $azurePrincipalId `
    --output none
  if ($LASTEXITCODE -ne 0) {
    throw "az sql server ad-admin create exited with code $LASTEXITCODE"
  }
  # AAD admin propagation across SQL gateway nodes is empirically 30-60s on
  # cold-deploy paths; a shorter wait risks `Login failed for token-identified
  # principal` on the next sqlcmd -G.
  Start-Sleep -Seconds 30
  Write-Success "AAD admin registered for object id $azurePrincipalId."
} catch {
  throw "Failed to register AAD admin on SQL Server: $_"
}

# Step 0: Apply schema migrations (V001, V002) — idempotent, safe to re-run.
# Must precede Step 1 because the RLS policy + dim_UserRoles table do not exist
# until V001 has applied.
#
# Each file's `__V<n>_CHECKSUM__` placeholder is replaced with the SHA-256
# computed by `scripts/compute_migration_checksum.py` BEFORE piping to sqlcmd
# (RR-09 from docs/security/threat_model.md). The checksum is computed against
# the on-disk file (placeholder included), so the value is stable across
# re-applies — only a substantive edit changes it.
Write-Info "Step 0: Applying schema migrations..."

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
# Avoid `Join-Path` inside array literal: PowerShell parses the comma as part
# of the first call's argument list (treating both paths as multi-element
# ChildPath), producing a single concatenated string like
# `V001__init.sql\Join-Path\V002__synth_logic.sql` instead of a 2-element
# array. String interpolation sidesteps the parser ambiguity entirely.
$migrations = @(
  "$repoRoot\db\migrations\V001__init.sql"
  "$repoRoot\db\migrations\V002__synth_logic.sql"
  "$repoRoot\db\migrations\V003__rls_db_owner_bypass.sql"
)

# Compute checksums for every migration in one shot (the helper enumerates
# all V*.sql under db/migrations/ and emits `<VAR>=<sha256>` lines).
$checksumOutput = python "$repoRoot\scripts\compute_migration_checksum.py"
if ($LASTEXITCODE -ne 0) {
  throw "compute_migration_checksum.py exited with code $LASTEXITCODE"
}
$checksumMap = @{}
foreach ($line in $checksumOutput) {
  if ($line -match '^([A-Z0-9_]+)=([0-9a-f]{64})$') {
    $checksumMap[$matches[1]] = $matches[2]
  }
}
if ($checksumMap.Count -eq 0) {
  throw "compute_migration_checksum.py produced no usable lines."
}

# Add the deployer's public IP to the SQL firewall so `sqlcmd -G` (which runs
# locally from this machine, NOT from Azure) can reach the SQL data-plane.
# Bicep's `AllowAllAzureServices` rule (0.0.0.0/0.0.0.0) only allows Azure-
# tenant traffic; without an explicit IP rule the local connection times out
# at the gateway. Idempotent: re-running for the same IP returns conflict
# (treated as success).
try {
  $myIp = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 10).Content.Trim()
  Write-Info "  Adding deployer IP $myIp to SQL firewall..."
  $fwName = "AllowDeployerIp_$($myIp -replace '\.', '_')"
  az sql server firewall-rule create `
    --resource-group $resourceGroupName `
    --server $sqlServerName `
    --name $fwName `
    --start-ip-address $myIp `
    --end-ip-address $myIp `
    --output none 2>$null
  Start-Sleep -Seconds 10  # firewall rule propagation across SQL gateway nodes
  Write-Success "  Deployer IP firewall rule applied (rule: $fwName)."
} catch {
  Write-Warning "Could not detect/add deployer IP to firewall: $_. sqlcmd may fail."
}

# Switch from `sqlcmd -G` (ActiveDirectoryIntegrated — fails on non-AAD-joined
# Windows machines: "Failed to resolve the UPN for the current windows account")
# to `Invoke-Sqlcmd` with an explicit access token from `az account get-access-token`.
# This pulls the token from the user's `az login` session and works regardless of
# Windows account type. Documented as A-3 in the Etapa-14 cloud-architect audit.
Write-Info "  Acquiring Azure access token for SQL data-plane..."
$accessToken = az account get-access-token --resource https://database.windows.net --query accessToken -o tsv
if ($LASTEXITCODE -ne 0 -or -not $accessToken) {
  throw "Failed to obtain Azure access token via az account get-access-token."
}

if (-not (Get-Module -ListAvailable -Name SqlServer)) {
  Write-Info "  Installing SqlServer PowerShell module (first run only, ~30s)..."
  try {
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser -ErrorAction SilentlyContinue | Out-Null
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue
    Install-Module -Name SqlServer -Scope CurrentUser -Force -AllowClobber -SkipPublisherCheck -Confirm:$false
  } catch {
    throw "Failed to install SqlServer PowerShell module: $_"
  }
}
Import-Module SqlServer -DisableNameChecking

# Force UTF-8 capture of the Python helper's stdout. Migrations contain U+0219
# (`ș`) in `dim_Date` holiday names; Windows default cp1252 cannot represent
# it. Invoke-Sqlcmd handles the PowerShell -> SQL Server path natively via
# Unicode strings — the encoding pin only matters for the Python -> PS pipe.
$previousConsoleOutputEncoding = [Console]::OutputEncoding
$previousOutputEncoding = $OutputEncoding
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

foreach ($migration in $migrations) {
  if (-not (Test-Path $migration)) {
    throw "Migration file not found: $migration"
  }
  $base = Split-Path $migration -Leaf
  $prefix = ($base -split '__', 2)[0]              # V001__init.sql → V001
  $var = "${prefix}_CHECKSUM"
  $checksum = $checksumMap[$var]
  if (-not $checksum) {
    throw "Checksum for $base not produced by helper (looked up key $var)."
  }
  $placeholder = "__${prefix}_CHECKSUM__"

  # Flyway-style idempotency: skip migration if already in schema_history with
  # matching checksum. Re-applying via `CREATE OR ALTER` breaks SCHEMABINDING
  # chains (Msg 3729: "Cannot ALTER X because it is being referenced by Y").
  # On first deploy schema_history doesn't exist yet — the try/catch handles
  # that by falling through to apply.
  $alreadyApplied = $false
  try {
    $rows = Invoke-Sqlcmd -ServerInstance $sqlServerFqdn -Database $sqlDatabaseName `
      -AccessToken $accessToken `
      -Query "SELECT TOP 1 checksum FROM dbo.schema_history WHERE script_name = '$base'" `
      -QueryTimeout 60 -ConnectionTimeout 90 -ErrorAction Stop
    if ($rows -and $rows.checksum) {
      if ($rows.checksum -eq $checksum) {
        Write-Info "  Skipping $base (already applied with matching checksum $($checksum.Substring(0, 8))…)."
        $alreadyApplied = $true
      } else {
        throw "Migration $base checksum mismatch: schema_history records $($rows.checksum.Substring(0, 8))… but file checksum is $($checksum.Substring(0, 8))…. Substantive edit detected — manual review required."
      }
    }
  } catch {
    if ($_.Exception.Message -match "Invalid object name 'dbo\.schema_history'") {
      Write-Info "  schema_history not yet present (first deploy); will be created by $base."
    } elseif ($_.Exception.Message -match "checksum mismatch") {
      throw  # propagate checksum mismatch errors
    } else {
      Write-Info "  schema_history lookup failed ($_); applying $base defensively."
    }
  }
  if ($alreadyApplied) { continue }

  Write-Info "  Applying $base (checksum $($checksum.Substring(0, 8))…)..."
  # arch-MA-04: both platforms render via the same Python helper so the bytes
  # piped to sqlcmd are byte-equivalent on Windows + Linux + cross-OS clones.
  # Capture exit code BEFORE the pipe (code-MA-04: a python crash leaves an
  # empty stream that `sqlcmd -b` would otherwise treat as success).
  $renderedLines = python "$repoRoot\scripts\render_migration.py" --path $migration --placeholder $placeholder --checksum $checksum
  if ($LASTEXITCODE -ne 0) {
    throw "render_migration.py failed for $base with exit code $LASTEXITCODE."
  }
  # PowerShell captures multi-line stdout as an Object[] of strings (one per
  # line). `sqlcmd | pipe` flattens it implicitly, but `Invoke-Sqlcmd -Query`
  # requires a single string — join with newlines explicitly.
  $rendered = $renderedLines -join "`n"
  # Invoke-Sqlcmd with -AccessToken: bypasses `sqlcmd -G`'s ActiveDirectoryIntegrated
  # requirement (which needs an AAD-joined Windows account). -ConnectionTimeout 90
  # covers Azure SQL Serverless cold-start (DB auto-pauses after 60 min idle and
  # takes 30-90s to resume on first connection).
  try {
    Invoke-Sqlcmd -ServerInstance $sqlServerFqdn -Database $sqlDatabaseName `
      -AccessToken $accessToken -Query $rendered `
      -QueryTimeout 300 -ConnectionTimeout 90 -ErrorAction Stop | Out-Null
  } catch {
    throw "Migration $base failed: $_"
  }
}

# Restore previous console encodings after the migrations are applied; the
# remaining postprovision steps don't carry Romanian characters and benefit
# from the default Windows behaviour.
[Console]::OutputEncoding = $previousConsoleOutputEncoding
$OutputEncoding = $previousOutputEncoding

Write-Success "Schema migrations applied (checksums recorded in dbo.schema_history)."

# Step 1: Register the Function App MI in dim_UserRoles for RLS admin scope
Write-Info "Step 1: Registering Function App MI in RLS table..."

$setupSql = @"
BEGIN TRY
  -- Temporarily disable RLS to allow initial setup
  ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = OFF);

  -- Register the Function App's Managed Identity with admin scope
  -- (Idempotent: will not insert if already exists)
  IF NOT EXISTS (
    SELECT 1 FROM dbo.dim_UserRoles
    WHERE aad_object_id = CAST('$functionAppPrincipalId' AS UNIQUEIDENTIFIER)
      AND scope = 'admin'
      AND is_active = 1
  )
  BEGIN
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
    VALUES (CAST('$functionAppPrincipalId' AS UNIQUEIDENTIFIER), NULL, 'admin', 1, SYSDATETIMEOFFSET());
    PRINT 'Registered Function App MI as admin.';
  END
  ELSE
  BEGIN
    PRINT 'Function App MI already registered.';
  END;
END TRY
BEGIN CATCH
  -- If any error occurs, ensure RLS is re-enabled before re-throwing
  ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
  THROW;
END CATCH;

-- Re-enable RLS
ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
"@

try {
  Write-Info "Connecting to SQL and configuring RLS..."
  Invoke-Sqlcmd -ServerInstance $sqlServerFqdn -Database $sqlDatabaseName `
    -AccessToken $accessToken -Query $setupSql `
    -QueryTimeout 300 -ConnectionTimeout 90 -ErrorAction Stop | Out-Null
  Write-Success "RLS setup complete."
} catch {
  Write-Warning "SQL RLS setup failed: $_. Step 0 should have applied V001 already; investigate."
  throw
} finally {
  # Ensure RLS policy is always re-enabled on script completion
  try {
    Write-Info "Ensuring RLS policy is re-enabled..."
    Invoke-Sqlcmd -ServerInstance $sqlServerFqdn -Database $sqlDatabaseName `
      -AccessToken $accessToken `
      -Query "ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);" `
      -QueryTimeout 60 -ConnectionTimeout 90 -ErrorAction SilentlyContinue | Out-Null
    Write-Success "RLS policy re-enabled."
  } catch {
    Write-Warning "Could not verify RLS policy state: $_. Continuing..."
  }
}

# Step 2: Set TCP_GENERATOR_OID app setting on the Function App
Write-Info "Step 2: Setting TCP_GENERATOR_OID app setting..."

try {
  az functionapp config appsettings set `
    --name $functionAppName `
    --resource-group $resourceGroupName `
    --settings "TCP_GENERATOR_OID=$functionAppPrincipalId" `
    --subscription $env:AZURE_SUBSCRIPTION_ID `
    -o none
  Write-Success "TCP_GENERATOR_OID set to $functionAppPrincipalId"
} catch {
  throw "Failed to set app setting: $_"
}

# Step 2b: Restart the Function App to pick up the new app setting
Write-Info "Step 2b: Restarting Function App to load new settings..."

try {
  az functionapp restart `
    --name $functionAppName `
    --resource-group $resourceGroupName `
    --subscription $env:AZURE_SUBSCRIPTION_ID `
    -o none
  Write-Success "Function App restarted."
} catch {
  throw "Failed to restart Function App: $_"
}

# Step 2c: Substitute placeholders in swa/staticwebapp.config.json
# (per Etapa-4 convergence pass-2 security CR-03). The SWA config ships with
# <TENANT_ID> and <value-set-by-postprovision> tokens that must be replaced
# with the real tenant id and the SWA-FORWARDED-SECRET value before SWA upload.
# Moved from function_app/ to swa/ during the Etapa-5 review pass so the file
# travels with the SWA upload bundle.
Write-Info "Step 2c: Substituting SWA config placeholders..."

try {
  $swaConfigPath = Join-Path $repoRoot "swa\staticwebapp.config.json"
  if (-not (Test-Path $swaConfigPath)) {
    throw "staticwebapp.config.json not found at $swaConfigPath"
  }
  $tenantId = $env_values['AZURE_TENANT_ID']
  if (-not $tenantId) {
    throw "AZURE_TENANT_ID not in azd env values; cannot substitute placeholder."
  }

  # KV is in RBAC mode. The Bicep `oidcSecretsOfficer` role assignment may not
  # have propagated yet to the data plane (Azure documents 5-30 min for RBAC
  # propagation in some cases). Idempotently re-assert the role here, then
  # retry the secret read with backoff. `az role assignment create` is a no-op
  # if the role already exists (errors are suppressed via 2>$null).
  $kvResourceId = "/subscriptions/$($env_values['AZURE_SUBSCRIPTION_ID'])/resourceGroups/$resourceGroupName/providers/Microsoft.KeyVault/vaults/$kvName"
  # Officer (not User) so Step 4 can DELETE `SQL-ADMIN-PASSWORD-BOOTSTRAP`.
  # User role is read-only and was insufficient for the cleanup operation.
  Write-Info "  Asserting Key Vault Secrets Officer role for deployer (idempotent)..."
  az role assignment create `
    --assignee-object-id $azurePrincipalId `
    --assignee-principal-type User `
    --role "Key Vault Secrets Officer" `
    --scope $kvResourceId `
    --output none 2>$null
  # Note: 2>$null suppresses "role already exists" errors; real auth/permission
  # errors still surface as a missing secret in the retry loop below.

  Write-Info "  Reading SWA-FORWARDED-SECRET from $kvName (with RBAC propagation backoff)..."
  $swaSecret = $null
  for ($attempt = 1; $attempt -le 6; $attempt++) {
    $swaSecret = az keyvault secret show --vault-name $kvName --name 'SWA-FORWARDED-SECRET' --query value -o tsv 2>$null
    if ($LASTEXITCODE -eq 0 -and $swaSecret) {
      Write-Info "    Secret read OK on attempt $attempt."
      break
    }
    if ($attempt -lt 6) {
      Write-Info "    RBAC not yet propagated (attempt $attempt/6); waiting 30s..."
      Start-Sleep -Seconds 30
    }
  }
  if (-not $swaSecret) {
    throw "SWA-FORWARDED-SECRET unreadable from $kvName after 3 min — check 'az role assignment list --assignee $azurePrincipalId --scope $kvResourceId' manually."
  }

  (Get-Content $swaConfigPath -Raw) `
    -replace '<TENANT_ID>', $tenantId `
    -replace '<value-set-by-postprovision>', $swaSecret `
    | Set-Content -Path $swaConfigPath -NoNewline
  Write-Success "staticwebapp.config.json placeholders substituted."
} catch {
  throw "Failed to substitute SWA config: $_"
}

# Step 3: Flip SQL server to AAD-only authentication
Write-Info "Step 3: Enabling AAD-only authentication on SQL server..."

try {
  # Use az CLI (already required by the rest of this script) rather than
  # Set-AzSqlServerActiveDirectoryOnlyAuthentication, which would force the
  # operator to install the Az.Sql PowerShell module on top of az CLI. The
  # `.sh` counterpart already uses the CLI form — this mirrors it.
  # `az sql server ad-only-auth enable` flag inconsistency: CLI 2.86 rejects
  # both `--server-name` and `--server`. The actual flag is `--name` / `-n`
  # (per Microsoft docs, but the alias names vary across CLI versions).
  az sql server ad-only-auth enable `
    --resource-group $resourceGroupName `
    --name $sqlServerName `
    -o none
  if ($LASTEXITCODE -ne 0) {
    throw "az sql server ad-only-auth enable exited with code $LASTEXITCODE"
  }
  Start-Sleep -Seconds 10  # Allow the change to propagate
  Write-Success "AAD-only authentication enabled."
} catch {
  throw "Failed to enable AAD-only auth: $_"
}

# Step 4: Delete the SQL-ADMIN-PASSWORD-BOOTSTRAP secret (but keep SQL-ADMIN-PASSWORD-EXPORT)
Write-Info "Step 4: Cleaning up bootstrap password..."

try {
  $secretExists = az keyvault secret show `
    --vault-name $kvName `
    --name 'SQL-ADMIN-PASSWORD-BOOTSTRAP' `
    -o json 2>$null | ConvertFrom-Json

  if ($secretExists) {
    az keyvault secret delete `
      --vault-name $kvName `
      --name 'SQL-ADMIN-PASSWORD-BOOTSTRAP' `
      -o none
    if ($LASTEXITCODE -ne 0) {
      throw "az keyvault secret delete exited with code $LASTEXITCODE (RBAC propagation?). Re-run azd up."
    }
    Write-Success "Deleted SQL-ADMIN-PASSWORD-BOOTSTRAP."
  } else {
    Write-Info "SQL-ADMIN-PASSWORD-BOOTSTRAP not found (already deleted)."
  }
} catch {
  Write-Warning "Error deleting bootstrap secret: $_. Continuing..."
}

# Step 5: Verify AAD-only flip
Write-Info "Step 5: Verifying AAD-only authentication..."

try {
  # CLI 2.86 subcommand is `get` (NOT `list`) — returns the singleton AAD-only
  # auth state as a single object with `.azureAdOnlyAuthentication` boolean.
  $aadOnlyStatus = az sql server ad-only-auth get `
    --name $sqlServerName `
    --resource-group $resourceGroupName `
    -o json | ConvertFrom-Json

  if ($aadOnlyStatus -and $aadOnlyStatus.azureAdOnlyAuthentication -eq $true) {
    Write-Success "AAD-only authentication verified as enabled."
  } else {
    throw "AAD-only authentication is not enabled!"
  }
} catch {
  throw "Verification failed: $_"
}

# Verify bootstrap secret is gone
try {
  $secretStillExists = az keyvault secret show `
    --vault-name $kvName `
    --name 'SQL-ADMIN-PASSWORD-BOOTSTRAP' `
    -o json 2>$null

  if ($secretStillExists) {
    throw "SQL-ADMIN-PASSWORD-BOOTSTRAP still exists in Key Vault!"
  } else {
    Write-Success "SQL-ADMIN-PASSWORD-BOOTSTRAP confirmed deleted."
  }
} catch {
  if ($_ -match "not found") {
    Write-Success "SQL-ADMIN-PASSWORD-BOOTSTRAP confirmed deleted."
  } else {
    throw $_
  }
}

# Final summary
Write-Success "Post-provision complete!"
Write-Info "Summary:"
Write-Info "  ✓ Function App MI registered in RLS table as admin"
Write-Info "  ✓ TCP_GENERATOR_OID app setting configured"
Write-Info "  ✓ AAD-only authentication enabled on SQL server"
Write-Info "  ✓ SQL-ADMIN-PASSWORD-BOOTSTRAP removed from Key Vault"
Write-Info "  ✓ SQL-ADMIN-PASSWORD-EXPORT retained for BACPAC exports"
