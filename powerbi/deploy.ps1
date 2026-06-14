#Requires -Version 7.0

<#
.SYNOPSIS
PowerBI deployment script for TCP — Trading Central Panel (Etapa 7).

.DESCRIPTION
End-to-end, idempotent PowerBI deployment via the REST API (per ADR-001).
The script numbers nine phases (Step 0 .. Step 8):

  Step 0  — Preflight: az CLI login, PowerBI bearer, optional SQL reachability.
  Step 1  — Workspace bootstrap (lookup or create + add deploy SP as Admin).
  Step 2  — Compile the TMDL model to `build/dataset.bim` (pbi-tools / TE3 / pre-built).
  Step 3  — Publish the dataset via multipart import.
  Step 4  — Take dataset ownership + set M parameters (SqlServer, SqlDatabase) +
            verify data-source binding (OAuth on-behalf-of SP).
  Step 5  — Trigger an immediate refresh so credential / connectivity errors surface
            now instead of at the 07:30 RO scheduled run the next weekday.
  Step 6  — Configure Scheduled Refresh (Mon-Fri 07:30 Europe/Bucharest).
  Step 7  — Compile + publish the PBIR report; substitute the SWA hostname into the
            AI Assistant page hyperlink; rebind the report to the dataset.
  Step 8  — Verify: last refresh + final report URL.

The script is safe to re-run: every mutating step is preceded by an existence check,
and every PowerBI REST call goes through `Helpers.psm1` (`Invoke-PowerBIRequest`)
which adds bearer auth, transient retry, multipart upload, and 401-refresh.

REQUIRED ENVIRONMENT VARIABLES / AZD ENV SETTINGS:

  POWERBI_TENANT_ID                — Entra tenant id where the PowerBI SP lives.
  POWERBI_CLIENT_ID                — App registration id of the PowerBI SP.
  POWERBI_CLIENT_SECRET            — (optional, fallback) client secret if not using federated OIDC.
  POWERBI_SP_OBJECT_ID             — (optional) the SP's enterprise application object id;
                                     used to add the SP as a workspace Admin. If omitted, the
                                     script attempts to resolve it from POWERBI_CLIENT_ID.
  POWERBI_WORKSPACE_NAME           — (default "TCP — Trading Central Panel").
  AZURE_SQL_SERVER_FQDN            — from `azd env get-values` (populated by Bicep main outputs).
  AZURE_SQL_DATABASE_NAME          — from `azd env get-values`.
  AZURE_TENANT_ID                  — from `azd env get-values` (Entra tenant for the SQL OAuth audience).
  AZURE_STATIC_WEB_APP_HOSTNAME    — from `azd env get-values`; substituted into the
                                     AI Assistant hyperlink visual at Step 7.

PRE-REQUISITES: see docs/runbooks/powerbi_deploy.md §2 + §4.

INVOCATION:
  cd <repo-root>
  pwsh -File powerbi/deploy.ps1

EXIT CODES:
  0  — success.
  >0 — any failure (the script aborts on the first error; see ROLLBACK at the bottom).

Cross-references:
  - docs/decisions/ADR-001-powerbi-deployment.md   (this script's primary contract).
  - docs/decisions/ADR-003-rls-session-context.md  (RLS contract: SP must exist in dim_UserRoles).
  - docs/design/03_architecture.md §3.3 / §4.2 / §5 (BI path + RBAC matrix).
  - docs/design/02_database_design.md §10           (`tcp_bi_reader` role).
  - docs/security/credentials_rotation.md §2.7      (PowerBI SP rotation procedure).

ROLLBACK:
  Partial rollback (keep workspace, drop dataset/report) is the recommended path:
    az rest --method DELETE --uri "https://api.powerbi.com/v1.0/myorg/groups/<wsId>/datasets/<datasetId>"
    az rest --method DELETE --uri "https://api.powerbi.com/v1.0/myorg/groups/<wsId>/reports/<reportId>"
  Full rollback (also deletes the workspace and any manual visual layout):
    az rest --method DELETE --uri "https://api.powerbi.com/v1.0/myorg/groups/<workspaceId>"
  Then re-run `pwsh -File powerbi/deploy.ps1`.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Module + helpers
# ---------------------------------------------------------------------------
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$BuildDir = Join-Path $RepoRoot 'powerbi\build'
$ModelDir = Join-Path $RepoRoot 'powerbi\model'
$ReportDir = Join-Path $RepoRoot 'powerbi\report'
$HelpersModule = Join-Path $PSScriptRoot 'scripts\Helpers.psm1'

if (-not (Test-Path $HelpersModule)) {
    throw "Helpers module not found at $HelpersModule. Did the powerbi/scripts/ folder ship?"
}
Import-Module $HelpersModule -Force

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Assert-LastSuccess {
    param([string]$Operation)
    if (-not $?) {
        throw "Operation failed: $Operation (last command reported `$? = $false)."
    }
}

# ---------------------------------------------------------------------------
# Step 0: Preflight
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 0: Preflight checks'
Write-Info '============================================================'

# 0a — az CLI login.
Write-Info '0a: verifying az CLI session...'
$null = az account show -o json 2>$null
if (-not $?) {
    Write-Warn 'No active az session. Attempting service-principal login from env vars.'
    if (-not $env:POWERBI_TENANT_ID -or -not $env:POWERBI_CLIENT_ID) {
        throw 'POWERBI_TENANT_ID + POWERBI_CLIENT_ID are required when no az session is active.'
    }
    if ($env:POWERBI_CLIENT_SECRET) {
        Write-Warn 'Falling back to client-secret SP login (federated OIDC is preferred — see ADR-001).'
        az login --service-principal `
            --username $env:POWERBI_CLIENT_ID `
            --password $env:POWERBI_CLIENT_SECRET `
            --tenant $env:POWERBI_TENANT_ID `
            --allow-no-subscriptions -o none
        Assert-LastSuccess 'az login --service-principal'
    }
    else {
        throw 'No az session and no POWERBI_CLIENT_SECRET. Run an OIDC-federated `az login` first (CI) or set POWERBI_CLIENT_SECRET locally.'
    }
}
$account = az account show -o json | ConvertFrom-Json
Write-Success "az session active (tenant=$($account.tenantId), user/sp=$($account.user.name))."

# 0b — Resolve azd env values. Skip silently if azd is not installed (env-only mode).
Write-Info '0b: loading azd env values (if azd is available)...'
$azdEnv = @{}
$azdExe = Get-Command azd -ErrorAction SilentlyContinue
if ($azdExe) {
    try {
        $envLines = azd env get-values 2>$null
        if ($?) {
            foreach ($line in $envLines) {
                if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*"?(.*?)"?\s*$') {
                    $azdEnv[$Matches[1]] = $Matches[2]
                }
            }
            Write-Success "Loaded $($azdEnv.Count) azd env values."
        }
        else {
            Write-Warn 'azd env get-values failed — falling back to process env vars only.'
        }
    }
    catch {
        Write-Warn "azd env get-values raised an error: $_. Continuing with process env only."
    }
}
else {
    Write-Warn 'azd not on PATH — relying on process env vars only.'
}

function Resolve-Setting {
    param([string]$Name, [string]$Default = $null)
    $val = $azdEnv[$Name]
    if (-not $val) { $val = [Environment]::GetEnvironmentVariable($Name) }
    if (-not $val -and $Default) { $val = $Default }
    return $val
}

$workspaceName = Resolve-Setting 'POWERBI_WORKSPACE_NAME' 'TCP — Trading Central Panel'
$sqlServerFqdn = Resolve-Setting 'AZURE_SQL_SERVER_FQDN'
$sqlDatabase = Resolve-Setting 'AZURE_SQL_DATABASE_NAME'
$tenantId = Resolve-Setting 'AZURE_TENANT_ID'
$spClientId = Resolve-Setting 'POWERBI_CLIENT_ID'
$spObjectId = Resolve-Setting 'POWERBI_SP_OBJECT_ID'
$swaHostname = Resolve-Setting 'AZURE_STATIC_WEB_APP_HOSTNAME'

foreach ($pair in @(
        @{ Name = 'AZURE_SQL_SERVER_FQDN'; Value = $sqlServerFqdn },
        @{ Name = 'AZURE_SQL_DATABASE_NAME'; Value = $sqlDatabase },
        @{ Name = 'AZURE_TENANT_ID'; Value = $tenantId },
        @{ Name = 'POWERBI_CLIENT_ID'; Value = $spClientId }
    )) {
    if (-not $pair.Value) {
        throw "Required setting '$($pair.Name)' is missing from azd env and process env."
    }
}

if (-not $swaHostname) {
    Write-Warn 'AZURE_STATIC_WEB_APP_HOSTNAME not set — the AI Assistant hyperlink will retain the <SWA_HOSTNAME> placeholder. Set the value and re-run Step 7 to fix.'
}

Write-Info "Resolved configuration:"
Write-Info "  Workspace name : $workspaceName"
Write-Info "  SQL FQDN       : $sqlServerFqdn"
Write-Info "  SQL Database   : $sqlDatabase"
Write-Info "  Tenant id      : $tenantId"
Write-Info "  SP client id   : $spClientId"
Write-Info "  SWA hostname   : $(if ($swaHostname) { $swaHostname } else { '<unset — placeholder will remain>' })"

# 0c — Acquire PowerBI bearer token (also validates the SP can call the PBI API).
Write-Info '0c: acquiring PowerBI bearer token...'
$pbiToken = Get-PowerBIToken
Write-Success 'PowerBI bearer token acquired.'

# 0d — Resolve the SP object id if not supplied (used for the workspace Admin grant).
if (-not $spObjectId) {
    Write-Info "0d: resolving SP object id from client id $spClientId..."
    $sp = az ad sp show --id $spClientId -o json 2>$null | ConvertFrom-Json
    if ($? -and $sp -and $sp.id) {
        $spObjectId = $sp.id
        Write-Success "Resolved SP object id: $spObjectId"
    }
    else {
        Write-Warn "Could not resolve SP object id via 'az ad sp show'. Workspace Admin grant will be skipped — set POWERBI_SP_OBJECT_ID manually if you need it."
    }
}

# 0e — SQL TCP reachability probe (informational; the dataset refresh in Step 5 is the
# authoritative end-to-end check). A failure here is non-fatal — the operator can decide
# to abort.
Write-Info "0e: probing SQL TCP reachability on $($sqlServerFqdn):1433 (non-fatal)..."
try {
    $tcpTest = Test-NetConnection -ComputerName $sqlServerFqdn -Port 1433 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($tcpTest) {
        Write-Success "TCP 1433 reachable on $sqlServerFqdn."
    }
    else {
        Write-Warn "TCP 1433 on $sqlServerFqdn did not respond. The dataset refresh in Step 5 will fail with a 'Couldn't connect to data source' error if this persists — check Azure SQL firewall rules (AllowAllAzureServices) and the FQDN."
    }
}
catch {
    Write-Warn "TCP reachability probe raised an error (non-fatal): $_"
}

# ---------------------------------------------------------------------------
# Step 1: Workspace bootstrap
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 1: Workspace bootstrap'
Write-Info '============================================================'

# PowerBI's filter syntax uses single quotes around the value:  ?$filter=name eq 'foo'
$filterPath = "/groups?`$filter=name eq '$workspaceName'"
$workspaces = Invoke-PowerBIRequest -Method GET -Path $filterPath -Token $pbiToken
$workspaceId = $null
if ($workspaces -and $workspaces.value -and $workspaces.value.Count -gt 0) {
    $workspaceId = $workspaces.value[0].id
    Write-Success "Workspace already exists: $workspaceName (id=$workspaceId)."
}
else {
    Write-Info "Creating workspace '$workspaceName'..."
    $created = Invoke-PowerBIRequest -Method POST -Path '/groups?workspaceV2=true' `
        -Body @{ name = $workspaceName } -Token $pbiToken
    if (-not $created -or -not $created.id) {
        throw "Workspace creation returned no id."
    }
    $workspaceId = $created.id
    Write-Success "Workspace created: $workspaceName (id=$workspaceId)."
}

# Add the deploy SP as Admin on the workspace (idempotent — 200 or 409 both fine).
if ($spObjectId) {
    Write-Info "Granting SP $spObjectId Admin on workspace $workspaceId..."
    try {
        $null = Invoke-PowerBIRequest -Method POST `
            -Path "/groups/$workspaceId/users" `
            -Body @{
                identifier           = $spObjectId
                principalType        = 'App'
                groupUserAccessRight = 'Admin'
            } `
            -Token $pbiToken
        Write-Success 'SP added as workspace Admin.'
    }
    catch {
        # `Invoke-RestMethod -ErrorAction Stop` puts the HTTP status reason in
        # `Exception.Message` and the response body (with the PowerBI error
        # code) in `ErrorDetails.Message`. Inspect both.
        $msg = $_.Exception.Message
        $body = if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { '' }
        $combined = "$msg`n$body"
        # PowerBI returns HTTP 400 with `AddingAlreadyExistsGroupUserNotSupportedError`
        # when the principal is already a workspace user (not 409 as one might expect).
        if ($combined -match '409' -or $combined -match 'Conflict' -or
            $combined -match 'AddingAlreadyExists' -or $combined -match 'already') {
            Write-Info 'SP is already a workspace Admin — skipping.'
        }
        else {
            throw
        }
    }
}

# ---------------------------------------------------------------------------
# Step 2: Compile the TMDL model
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 2: Compile TMDL model'
Write-Info '============================================================'

if (-not (Test-Path $BuildDir)) {
    New-Item -ItemType Directory -Path $BuildDir | Out-Null
}
$bimPath = Join-Path $BuildDir 'dataset.bim'
$prebuiltBim = Test-Path $bimPath

if ($prebuiltBim) {
    Write-Info "Pre-built $bimPath detected (will be used if compile tools are not on PATH)."
}

$pbiToolsExe = Get-Command pbi-tools -ErrorAction SilentlyContinue
$teExe = Get-Command TabularEditor -ErrorAction SilentlyContinue
if (-not $teExe) { $teExe = Get-Command TabularEditor.exe -ErrorAction SilentlyContinue }

if ($prebuiltBim) {
    # Prefer pre-built .bim when present — it is the canonical artifact and may
    # have been hand-crafted (e.g. by scripts/tmdl_to_bim.py) to bypass strict
    # TMDL parsers in pbi-tools/TabularEditor that reject modern TMDL features.
    Write-Info "Using pre-built $bimPath (regenerate with scripts/tmdl_to_bim.py if needed)."
}
elseif ($pbiToolsExe) {
    Write-Info 'Compiling TMDL with pbi-tools...'
    # pbi-tools 1.x verb: `compile <project-dir>` (NOT `compile-model`). The
    # `-pbixOutPath` switch can also produce a .pbix; for the import flow we
    # ask for the raw TMSL-compiled `.bim` via `-outPath` + format hint.
    & $pbiToolsExe.Source compile $ModelDir -outPath $bimPath
    Assert-LastSuccess 'pbi-tools compile (model)'
}
elseif ($teExe) {
    Write-Info 'Compiling TMDL with Tabular Editor 3...'
    & $teExe.Source (Join-Path $ModelDir 'database.tmdl') -B $bimPath
    Assert-LastSuccess 'TabularEditor compile (model)'
}
else {
    throw @"
TMDL compile requires pbi-tools 1.x (`dotnet tool install --global pbi-tools`) OR
Tabular Editor 3 (TabularEditor.exe on PATH). Neither was found on PATH, and no
pre-built powerbi/build/dataset.bim is present.

Fix one of these and re-run:
  1. Install pbi-tools:    dotnet tool install --global pbi-tools
  2. Install Tabular Editor 3 and add it to PATH.
  3. Open powerbi/model/database.tmdl in PowerBI Desktop, save .pbix, then
     export the .bim via External Tools → Tabular Editor 2 (free) and place
     the result at powerbi/build/dataset.bim.
"@
}

if (-not (Test-Path $bimPath)) {
    throw "Expected $bimPath to exist after compilation, but it does not."
}
$bimBytes = (Get-Item $bimPath).Length
Write-Success "TMDL compiled: $bimPath ($bimBytes bytes)."

# ---------------------------------------------------------------------------
# Step 3: Publish dataset (multipart import)
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 3: Publish dataset'
Write-Info '============================================================'

# Substitute only the <TENANT_ID> placeholder pre-upload (the SQL connection parameters
# are first-class M parameters and are set via UpdateParameters in Step 4 — see M-4 in
# the cloud-architect review).
$bimRaw = Get-Content $bimPath -Raw
$bimSubstituted = $bimRaw -replace '<TENANT_ID>', $tenantId
$bimReadyPath = Join-Path $BuildDir 'dataset.ready.bim'
Set-Content -Path $bimReadyPath -Value $bimSubstituted -NoNewline -Encoding utf8
Write-Success "Tenant id substituted → $bimReadyPath."

$datasetDisplayName = $workspaceName
$importPath = "/groups/$workspaceId/imports?datasetDisplayName=$([System.Web.HttpUtility]::UrlEncode($datasetDisplayName))&nameConflict=CreateOrOverwrite"

Write-Info "Uploading dataset to workspace $workspaceId..."
$importResult = Invoke-PowerBIRequest -Method POST -Path $importPath -FilePath $bimReadyPath -Token $pbiToken

if (-not $importResult -or -not $importResult.id) {
    throw 'Dataset import response missing id.'
}
$datasetImportId = $importResult.id
Write-Success "Dataset import accepted (importId=$datasetImportId). Polling..."

$importFinal = Wait-ForImport -WorkspaceId $workspaceId -ImportId $datasetImportId -TimeoutSeconds 600
if (-not $importFinal.datasets -or $importFinal.datasets.Count -lt 1) {
    throw 'Dataset import succeeded but no dataset id was returned.'
}
$datasetId = $importFinal.datasets[0].id
Write-Success "Dataset published: id=$datasetId."

# ---------------------------------------------------------------------------
# Step 4: Take ownership + bind data-source parameters
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 4: Take ownership + set dataset parameters'
Write-Info '============================================================'

# 4a — Take ownership so the SP can manage credentials and parameters.
Write-Info '4a: dataset Default.TakeOver...'
try {
    $null = Invoke-PowerBIRequest -Method POST `
        -Path "/groups/$workspaceId/datasets/$datasetId/Default.TakeOver" `
        -Token $pbiToken
    Write-Success 'Dataset ownership transferred to the deploy SP.'
}
catch {
    Write-Warn "TakeOver returned an error — the SP may already own this dataset. Continuing. ($_)"
}

# 4b — Set the SqlServer / SqlDatabase M parameters via Default.UpdateParameters.
#      This is the supported first-class API for changing M-query inputs; the previous
#      placeholder string substitution in the .bim was an implicit contract that broke
#      silently on parameter rename (see holistic review M-4).
Write-Info "4b: setting M parameters SqlServer='$sqlServerFqdn' / SqlDatabase='$sqlDatabase'..."
$updateParamsBody = @{
    updateDetails = @(
        @{ name = 'SqlServer';   newValue = $sqlServerFqdn },
        @{ name = 'SqlDatabase'; newValue = $sqlDatabase }
    )
}
try {
    $null = Invoke-PowerBIRequest -Method POST `
        -Path "/groups/$workspaceId/datasets/$datasetId/Default.UpdateParameters" `
        -Body $updateParamsBody -Token $pbiToken
    Write-Success 'M parameters SqlServer + SqlDatabase set on the dataset.'
}
catch {
    Write-Err "UpdateParameters failed: $_"
    throw
}

# 4c — Verify the dataset's data source list (sanity check).
Write-Info '4c: verifying data sources on the dataset...'
$dsList = Invoke-PowerBIRequest -Method GET `
    -Path "/groups/$workspaceId/datasets/$datasetId/datasources" `
    -Token $pbiToken
if ($dsList -and $dsList.value) {
    foreach ($ds in $dsList.value) {
        Write-Info "  datasource: type=$($ds.datasourceType) server=$($ds.connectionDetails.server) db=$($ds.connectionDetails.database)"
    }
    Write-Success 'Data source verification complete.'
}
else {
    Write-Warn 'No data sources reported by the dataset — the immediate refresh in Step 5 will surface a clearer error.'
}

# Note on credentials: the OAuth-on-behalf-of-SP token used by PowerBI Service for refresh
# is bound to the dataset by Default.TakeOver above. The first refresh in Step 5 is the
# authoritative end-to-end credential check; a failure there with "Login failed" indicates
# the SP is not yet a `tcp_bi_reader` member in SQL (see runbook §4.7 + §10).

# ---------------------------------------------------------------------------
# Step 5: Trigger immediate refresh
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 5: Trigger immediate refresh'
Write-Info '============================================================'

# Surface credential / connectivity errors now rather than at 07:30 RO the next weekday.
# A failed refresh here is operationally far cheaper to diagnose (the operator is still
# in front of the terminal) than at scheduled-refresh time.
Write-Info "Requesting refresh on dataset $datasetId..."
try {
    $null = Invoke-PowerBIRequest -Method POST `
        -Path "/groups/$workspaceId/datasets/$datasetId/refreshes" `
        -Body @{ notifyOption = 'MailOnFailure' } -Token $pbiToken
    Write-Success 'Refresh enqueued. Polling for completion (up to 15 minutes)...'
}
catch {
    Write-Err "POST /refreshes failed — credential binding likely incorrect: $_"
    throw
}

# Poll the refresh history until the latest entry leaves the InProgress state.
$refreshDeadline = (Get-Date).AddMinutes(15)
$lastStatus = $null
while ((Get-Date) -lt $refreshDeadline) {
    Start-Sleep -Seconds 10
    try {
        $hist = Invoke-PowerBIRequest -Method GET `
            -Path "/groups/$workspaceId/datasets/$datasetId/refreshes?`$top=1" `
            -Token $pbiToken
        if ($hist -and $hist.value -and $hist.value.Count -gt 0) {
            $lastStatus = $hist.value[0].status
            Write-Info "  refresh status = $lastStatus"
            if ($lastStatus -ne 'Unknown' -and $lastStatus -ne 'InProgress') { break }
        }
    }
    catch {
        Write-Warn "Refresh poll failed (non-fatal): $_"
    }
}

if ($lastStatus -eq 'Completed') {
    Write-Success 'Initial refresh completed successfully.'
}
elseif ($lastStatus -eq 'Failed') {
    throw "Initial refresh reported Failed. Most common cause: PowerBI SP is not yet a contained user with tcp_bi_reader role in the target database (see runbook §4.7 and §10 troubleshooting entry 'Login failed for user')."
}
else {
    Write-Warn "Refresh did not reach a terminal state within the 15-minute window (last status: $lastStatus). Continuing — verify in PowerBI Service before relying on the dataset."
}

# ---------------------------------------------------------------------------
# Step 6: Configure scheduled refresh
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 6: Configure scheduled refresh'
Write-Info '============================================================'

# Important: "GMT+02:00 Bucharest" is a *display* name in PowerBI Service; the actual
# timezone *id* the REST API expects is "E. Europe Standard Time" (DST-aware).
$refreshSchedule = @{
    value = @{
        days              = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
        times             = @('07:30')
        enabled           = $true
        localTimeZoneId   = 'E. Europe Standard Time'
        notifyOption      = 'MailOnFailure'
    }
}
$null = Invoke-PowerBIRequest -Method PATCH `
    -Path "/groups/$workspaceId/datasets/$datasetId/refreshSchedule" `
    -Body $refreshSchedule -Token $pbiToken
Write-Success "Scheduled refresh set: Mon-Fri 07:30 Europe/Bucharest (id 'E. Europe Standard Time')."

# ---------------------------------------------------------------------------
# Step 7: Publish the report (PBIR)
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 7: Publish report (PBIR)'
Write-Info '============================================================'

$pbixPath = Join-Path $BuildDir 'report.pbix'
$reportStagingDir = Join-Path $BuildDir 'report-staging'

# 7a — Substitute the SWA hostname into the AI Assistant page hyperlink so the published
# report opens the correct cross-origin destination. The PowerBI report links out to a
# new tab (see C-1 in the holistic review) — embedding the SWA in an iframe would be
# blocked by the Etapa 6 hardening (X-Frame-Options: DENY + frame-ancestors 'none').
if (Test-Path $reportStagingDir) { Remove-Item $reportStagingDir -Recurse -Force }
Copy-Item $ReportDir $reportStagingDir -Recurse

$aiPagePath = Join-Path $reportStagingDir 'pages\ai-assistant\page.json'
if ((Test-Path $aiPagePath) -and $swaHostname) {
    $aiPageRaw = Get-Content $aiPagePath -Raw
    $aiPageReady = $aiPageRaw -replace '<SWA_HOSTNAME>', $swaHostname
    Set-Content -Path $aiPagePath -Value $aiPageReady -NoNewline -Encoding utf8
    Write-Success "Substituted <SWA_HOSTNAME> → $swaHostname in AI Assistant page."
}
elseif (-not $swaHostname) {
    Write-Warn 'Skipping SWA hostname substitution — AZURE_STATIC_WEB_APP_HOSTNAME was not set in Step 0.'
}

if (Test-Path $pbixPath) { Remove-Item $pbixPath -Force }

if ($pbiToolsExe) {
    Write-Info 'Compiling PBIR with pbi-tools...'
    & $pbiToolsExe.Source compile $reportStagingDir -outPath $pbixPath
    Assert-LastSuccess 'pbi-tools compile (report)'
}
elseif (Test-Path $pbixPath) {
    Write-Warn "pbi-tools not on PATH — using pre-built $pbixPath."
}
else {
    Write-Warn @"
Skipping report publish:
  - pbi-tools is not on PATH to compile the PBIR sources.
  - No pre-built powerbi/build/report.pbix is available.

The dataset is fully deployed — the report can be (a) added manually in PowerBI Desktop
and uploaded, or (b) published in a follow-up run after installing pbi-tools.

ADR-001 explicitly acknowledges this manual fallback for final visual polish.
"@
    Write-Success 'Dataset deployment complete (report publish skipped).'
    return
}

if (-not (Test-Path $pbixPath)) {
    throw "Expected $pbixPath after compilation, but it does not exist."
}

$reportDisplayName = "$workspaceName — Report"
$reportImportPath = "/groups/$workspaceId/imports?datasetDisplayName=$([System.Web.HttpUtility]::UrlEncode($reportDisplayName))&nameConflict=Overwrite&skipReport=false"

Write-Info "Uploading report to workspace $workspaceId..."
$reportImport = Invoke-PowerBIRequest -Method POST -Path $reportImportPath -FilePath $pbixPath -Token $pbiToken

if (-not $reportImport -or -not $reportImport.id) { throw 'Report import returned no id.' }
$reportImportFinal = Wait-ForImport -WorkspaceId $workspaceId -ImportId $reportImport.id -TimeoutSeconds 600

if (-not $reportImportFinal.reports -or $reportImportFinal.reports.Count -lt 1) {
    throw 'Report import succeeded but no report id was returned.'
}
$reportId = $reportImportFinal.reports[0].id
Write-Success "Report published: id=$reportId."

# Rebind the report to the existing dataset (decouples the report's lifetime from the import bundle).
Write-Info 'Rebinding report to the published dataset...'
$null = Invoke-PowerBIRequest -Method POST `
    -Path "/groups/$workspaceId/reports/$reportId/Rebind" `
    -Body @{ datasetId = $datasetId } -Token $pbiToken
Write-Success 'Report rebound to dataset.'

# ---------------------------------------------------------------------------
# Step 8: Verify
# ---------------------------------------------------------------------------
Write-Info '============================================================'
Write-Info 'Step 8: Verify'
Write-Info '============================================================'

try {
    $refreshes = Invoke-PowerBIRequest -Method GET `
        -Path "/groups/$workspaceId/datasets/$datasetId/refreshes?`$top=1" `
        -Token $pbiToken
    if ($refreshes -and $refreshes.value -and $refreshes.value.Count -gt 0) {
        $last = $refreshes.value[0]
        Write-Info "  last refresh status   : $($last.status)"
        Write-Info "  last refresh requestId: $($last.requestId)"
    }
    else {
        Write-Info '  no refresh history available yet (Step 5 may have skipped on a non-fatal path).'
    }
}
catch {
    Write-Warn "Could not query refresh history (non-fatal): $_"
}

$reportUrl = "https://app.powerbi.com/groups/$workspaceId/reports/$(if ($reportId) { $reportId } else { '<reportId>' })"
Write-Success '============================================================'
Write-Success 'PowerBI deployment complete.'
Write-Success "  Workspace : $workspaceName (id=$workspaceId)"
Write-Success "  Dataset   : id=$datasetId"
if ($reportId) { Write-Success "  Report    : id=$reportId" }
Write-Success "  URL       : $reportUrl"
Write-Success '============================================================'
