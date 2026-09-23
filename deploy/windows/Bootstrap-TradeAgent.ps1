[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\TradeAgent"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it before bootstrapping Trade Agent."
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$connectorProject = Join-Path $ProjectRoot "connector"
$dataDirectory = Join-Path $ProjectRoot "data"
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "uv.lock") -PathType Leaf)) {
    throw "Missing $ProjectRoot\uv.lock. Deploy only a release with its lockfile."
}
if (-not (Test-Path -LiteralPath (Join-Path $connectorProject "uv.lock") -PathType Leaf)) {
    throw "Missing $connectorProject\uv.lock. Deploy only a release with its connector lockfile."
}

Push-Location -LiteralPath $ProjectRoot
try {
    & uv sync --locked --extra postgres
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }

    # The connector uses a distinct environment. --no-install-project is
    # intentional: its code is loaded from the reviewed root src directory by
    # Run-TradeAgent.ps1, while MT5 stays isolated in this environment.
    & uv sync --project $connectorProject --locked --no-install-project
    if ($LASTEXITCODE -ne 0) { throw "Connector dependency installation failed." }

    # The connector journal is a durable part of idempotency recovery. Create
    # its parent before the scheduled task can accept a request.
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
}
finally {
    Pop-Location
}
