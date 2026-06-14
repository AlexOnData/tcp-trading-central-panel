# Convert TMDL folder to .bim by directly invoking the
# Microsoft.AnalysisServices.Tabular SDK shipped with TabularEditor 2.
# The SDK's TmdlSerializer is more permissive than TE2's CLI strict parser.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$teDir = 'C:\Users\marce\bin\TabularEditor'
$modelDir = 'D:\TCP\TCP_TradingCentralPanel\powerbi\model'
$bimOut = 'D:\TCP\TCP_TradingCentralPanel\powerbi\build\dataset.bim'

# Ensure build dir exists
$buildDir = Split-Path $bimOut -Parent
if (-not (Test-Path $buildDir)) { New-Item -ItemType Directory -Path $buildDir | Out-Null }

# Load the SDK DLL
$dllPath = Join-Path $teDir 'Microsoft.AnalysisServices.Tabular.dll'
if (-not (Test-Path $dllPath)) { throw "SDK DLL not found at $dllPath" }
Add-Type -Path $dllPath
Write-Host "Loaded SDK: $dllPath"

Write-Host ""
Write-Host "=== Deserialize TMDL folder -> Database object ==="
try {
    $db = [Microsoft.AnalysisServices.Tabular.TmdlSerializer]::DeserializeDatabaseFromFolder($modelDir)
    Write-Host "Database loaded: name=$($db.Name), compatLevel=$($db.CompatibilityLevel), tables=$($db.Model.Tables.Count)"
} catch {
    Write-Host "TMDL deserialize FAILED: $_"
    Write-Host "Inner: $($_.Exception.InnerException.Message)"
    throw
}

Write-Host ""
Write-Host "=== Serialize Database -> .bim JSON ==="
$serializeOptions = New-Object Microsoft.AnalysisServices.Tabular.SerializeOptions
$bimJson = [Microsoft.AnalysisServices.Tabular.JsonSerializer]::SerializeDatabase($db, $serializeOptions)
Write-Host ".bim JSON length: $($bimJson.Length) chars"

Write-Host ""
Write-Host "=== Save to $bimOut ==="
[System.IO.File]::WriteAllText($bimOut, $bimJson, [System.Text.UTF8Encoding]::new($false))
Write-Host "Saved: $((Get-Item $bimOut).Length) bytes"

Write-Host ""
Write-Host "=== Sanity check first 300 chars ==="
($bimJson.Substring(0, [Math]::Min(300, $bimJson.Length)))
