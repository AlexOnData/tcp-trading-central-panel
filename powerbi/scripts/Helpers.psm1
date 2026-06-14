#Requires -Version 7.0

<#
.SYNOPSIS
Shared helpers for the PowerBI deploy pipeline.

.DESCRIPTION
Encapsulates the three pieces of plumbing needed by `powerbi/deploy.ps1`:

  1. `Get-PowerBIToken`           — acquires a bearer token for the PowerBI REST API
                                    scoped to `https://analysis.windows.net/powerbi/api`
                                    via the already-logged-in `az` CLI session.
  2. `Invoke-PowerBIRequest`      — thin wrapper around `Invoke-RestMethod` adding the
                                    bearer header and a small retry loop for transient
                                    429 (throttled) / 503 (Service Unavailable) responses.
  3. `Wait-ForImport`             — polls `GET /groups/{wsId}/imports/{importId}` until
                                    `importState` reaches `Succeeded` or `Failed`,
                                    or the supplied timeout elapses.

All functions stream structured `[INFO] / [WARNING] / [ERROR]` lines so they can be
called transparently from `deploy.ps1` and read in CI logs.

Cross-references:
  - docs/decisions/ADR-001-powerbi-deployment.md (primary REST endpoints).
  - docs/design/03_architecture.md §3.3 (BI path), §4.2 (PowerBI service connection).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Module-scope cached bearer token. Refreshed in-place by Invoke-PowerBIRequest on 401,
# so a long-running deploy that crosses the ~1-hour token TTL transparently picks up
# the new token without each caller paying a fresh `az account get-access-token` round-trip.
$script:CachedPowerBIToken = $null

# ---------------------------------------------------------------------------
# Internal logging helpers — mirror the style used in infra/scripts/postprovision.ps1
# ---------------------------------------------------------------------------
function Write-HelperInfo {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Cyan
}

function Write-HelperWarn {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-HelperError {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# Get-PowerBIToken
# ---------------------------------------------------------------------------
function Get-PowerBIToken {
    <#
    .SYNOPSIS
    Acquires a PowerBI REST API bearer token via the active `az` CLI session.

    .DESCRIPTION
    Uses `az account get-access-token --resource https://analysis.windows.net/powerbi/api`
    and returns the `accessToken` string. The token is cached at module scope so repeat
    calls inside the same deploy reuse a single bearer; pass `-ForceRefresh` to discard
    the cache after a 401. `Invoke-PowerBIRequest` invokes this with `-ForceRefresh` when
    a request returns HTTP 401, transparently rolling the token forward for all callers.

    .PARAMETER Cached
    When set, returns the cached token if one exists. The default behaviour is to return
    the cached token if available (this switch is informational; pass `-ForceRefresh` to
    bypass the cache).

    .PARAMETER ForceRefresh
    When set, discards the cached token and acquires a fresh one. Used after a 401.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [switch]$Cached,
        [switch]$ForceRefresh
    )

    if (-not $ForceRefresh -and $script:CachedPowerBIToken) {
        return $script:CachedPowerBIToken
    }

    $resource = 'https://analysis.windows.net/powerbi/api'

    # Prefer SP credentials if POWERBI_CLIENT_ID/_SECRET/_TENANT_ID are set.
    # PowerBI's import / dataset / refresh APIs require an SP-issued token; the
    # user's `az login` bearer is recognised for control-plane (workspaces) but
    # not for data-plane operations such as multipart import.
    $spClientId = $env:POWERBI_CLIENT_ID
    $spClientSecret = $env:POWERBI_CLIENT_SECRET
    $spTenantId = $env:POWERBI_TENANT_ID
    if ($spClientId -and $spClientSecret -and $spTenantId) {
        Write-HelperInfo "Acquiring PowerBI bearer token via client_credentials (SP=$spClientId)..."
        $tokenUrl = "https://login.microsoftonline.com/$spTenantId/oauth2/v2.0/token"
        $body = @{
            client_id     = $spClientId
            client_secret = $spClientSecret
            scope         = "$resource/.default"
            grant_type    = 'client_credentials'
        }
        try {
            $resp = Invoke-RestMethod -Method Post -Uri $tokenUrl -Body $body -ContentType 'application/x-www-form-urlencoded'
        } catch {
            throw "Failed to acquire SP token via client_credentials: $_"
        }
        if (-not $resp.access_token) {
            throw "client_credentials response did not contain an access_token."
        }
        $script:CachedPowerBIToken = $resp.access_token
        return $script:CachedPowerBIToken
    }

    # Fall back to az CLI's current session (user-delegated).
    Write-HelperInfo "Acquiring PowerBI bearer token via az CLI (resource=$resource)..."
    $raw = az account get-access-token --resource $resource -o json 2>$null
    if (-not $? -or -not $raw) {
        throw "Failed to acquire PowerBI access token. Is 'az login' active for the deploy service principal?"
    }
    $parsed = $raw | ConvertFrom-Json
    if (-not $parsed.accessToken) {
        throw "PowerBI access token response did not contain an accessToken field."
    }
    $script:CachedPowerBIToken = $parsed.accessToken
    return $script:CachedPowerBIToken
}

# ---------------------------------------------------------------------------
# Invoke-PowerBIRequest
# ---------------------------------------------------------------------------
function Invoke-PowerBIRequest {
    <#
    .SYNOPSIS
    Calls a PowerBI REST endpoint with auth, retry, and optional multipart upload.

    .PARAMETER Method
    HTTP verb (GET / POST / PATCH / PUT / DELETE).

    .PARAMETER Path
    Endpoint relative to `https://api.powerbi.com/v1.0/myorg` (must start with `/`).

    .PARAMETER Body
    Optional payload — string or hashtable. Hashtables are JSON-serialised.

    .PARAMETER ContentType
    Defaults to `application/json`. Ignored when `-FilePath` is set (a
    `multipart/form-data` boundary is generated automatically).

    .PARAMETER Token
    Optional bearer token. If omitted, the module-scope cached token is used (or a
    fresh one is acquired). A 401 response transparently refreshes the cache for the
    benefit of all subsequent callers.

    .PARAMETER MaxRetries
    Number of transient-retry attempts on 429/503 responses. Defaults to 5.

    .PARAMETER SuppressNotFound
    When set, swallows 404 responses and returns $null — convenient for "does this
    resource exist?" probes.

    .PARAMETER FilePath
    Path to a binary file to upload as `multipart/form-data`. When supplied, the
    body is built via `Invoke-RestMethod -Form @{ file = Get-Item $FilePath }` and
    the same retry / 401-refresh policy as a JSON request applies. Used by the
    dataset and report import calls.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [object]$Body = $null,
        [string]$ContentType = 'application/json',
        [string]$Token = $null,
        [int]$MaxRetries = 5,
        [switch]$SuppressNotFound,
        [string]$FilePath = $null
    )

    if (-not $Token) { $Token = Get-PowerBIToken }

    $baseUrl = 'https://api.powerbi.com/v1.0/myorg'
    $url = if ($Path.StartsWith('http')) { $Path } else { "$baseUrl$Path" }

    $headers = @{
        Authorization = "Bearer $Token"
        Accept        = 'application/json'
    }

    $isMultipart = [string]::IsNullOrEmpty($FilePath) -eq $false
    if ($isMultipart -and -not (Test-Path -LiteralPath $FilePath)) {
        throw "Invoke-PowerBIRequest: FilePath '$FilePath' does not exist."
    }

    $serialisedBody = $null
    if (-not $isMultipart -and $null -ne $Body) {
        if ($Body -is [string]) {
            $serialisedBody = $Body
        }
        elseif ($Body -is [byte[]]) {
            $serialisedBody = $Body
        }
        else {
            $serialisedBody = ($Body | ConvertTo-Json -Depth 12 -Compress)
        }
    }

    $attempt = 0
    while ($true) {
        $attempt++
        try {
            $params = @{
                Method      = $Method
                Uri         = $url
                Headers     = $headers
                ErrorAction = 'Stop'
            }
            if ($isMultipart) {
                # PowerShell 7+ builds the multipart/form-data boundary automatically
                # when `-Form` is used. We refresh the file handle on every attempt so
                # a retry after a 401/transient failure re-reads the stream cleanly.
                $params['Form'] = @{ file = Get-Item -LiteralPath $FilePath }
            }
            else {
                $params['ContentType'] = $ContentType
                if ($null -ne $serialisedBody) { $params['Body'] = $serialisedBody }
            }

            return Invoke-RestMethod @params
        }
        catch {
            $resp = $_.Exception.Response
            $statusCode = if ($resp) { [int]$resp.StatusCode } else { 0 }

            if ($SuppressNotFound -and $statusCode -eq 404) {
                return $null
            }

            $transient = @(429, 500, 502, 503, 504) -contains $statusCode
            if ($transient -and $attempt -lt $MaxRetries) {
                $delay = [Math]::Min(60, [Math]::Pow(2, $attempt))
                Write-HelperWarn "PowerBI $Method $Path returned HTTP $statusCode (attempt $attempt/$MaxRetries) — retrying in ${delay}s."
                Start-Sleep -Seconds $delay
                continue
            }

            # Re-acquire token once on 401 (likely expiry) and retry the request immediately.
            # Get-PowerBIToken -ForceRefresh updates the module-scope cache so subsequent
            # callers (including raw multipart paths in deploy.ps1) inherit the new token.
            if ($statusCode -eq 401 -and $attempt -lt $MaxRetries) {
                Write-HelperWarn "PowerBI returned HTTP 401 — refreshing bearer token and retrying."
                $Token = Get-PowerBIToken -ForceRefresh
                $headers['Authorization'] = "Bearer $Token"
                continue
            }

            $bodyText = ''
            try {
                if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                    $bodyText = $_.ErrorDetails.Message
                }
            }
            catch { $bodyText = '' }
            Write-HelperError "PowerBI $Method $Path failed: HTTP $statusCode — $bodyText"
            throw
        }
    }
}

# ---------------------------------------------------------------------------
# Wait-ForImport
# ---------------------------------------------------------------------------
function Wait-ForImport {
    <#
    .SYNOPSIS
    Polls an import operation until it succeeds, fails, or times out.

    .DESCRIPTION
    Calls `GET /groups/{WorkspaceId}/imports/{ImportId}` every `PollSeconds`
    until `importState` is one of `Succeeded` / `Failed` / `Unknown`, or
    `TimeoutSeconds` is exceeded. Returns the final response object.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$WorkspaceId,
        [Parameter(Mandatory)][string]$ImportId,
        [int]$TimeoutSeconds = 600,
        [int]$PollSeconds = 5
    )

    Write-HelperInfo "Polling import $ImportId in workspace $WorkspaceId (timeout=${TimeoutSeconds}s)..."
    $start = Get-Date
    while ($true) {
        $resp = Invoke-PowerBIRequest -Method GET -Path "/groups/$WorkspaceId/imports/$ImportId"
        $state = $resp.importState
        Write-HelperInfo "  import state = $state"

        if ($state -eq 'Succeeded') { return $resp }
        if ($state -eq 'Failed') {
            $detail = try { ($resp | ConvertTo-Json -Depth 8 -Compress) } catch { '<no detail>' }
            throw "PowerBI import $ImportId failed: $detail"
        }
        if ($state -eq 'Unknown') {
            throw "PowerBI import $ImportId entered the Unknown state — aborting."
        }

        $elapsed = ((Get-Date) - $start).TotalSeconds
        if ($elapsed -ge $TimeoutSeconds) {
            throw "PowerBI import $ImportId did not complete within ${TimeoutSeconds}s."
        }
        Start-Sleep -Seconds $PollSeconds
    }
}

Export-ModuleMember -Function 'Get-PowerBIToken', 'Invoke-PowerBIRequest', 'Wait-ForImport'
