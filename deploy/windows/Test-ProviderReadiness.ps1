[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\TradeAgent",
    [string]$EnvironmentFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($EnvironmentFile)) {
    $EnvironmentFile = Join-Path $ProjectRoot ".env"
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$smoke = Join-Path $ProjectRoot "deploy\provider_smoke.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Backend Python was not found: $python. Run Bootstrap-TradeAgent.ps1 first."
}
if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
    throw "Environment file was not found: $EnvironmentFile"
}

# Pydantic reads the selected protected dotenv file itself. Do not import it
# into this PowerShell process or print its values.
& $python $smoke --env-file $EnvironmentFile
exit $LASTEXITCODE
