[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet("Backend", "Connector")]
    [string]$Role,

    [string]$ProjectRoot = "C:\TradeAgent",
    [string]$EnvironmentFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($EnvironmentFile)) {
    $EnvironmentFile = Join-Path $ProjectRoot ".env"
}

function Import-TradeAgentEnvironment {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Environment file was not found: $Path"
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }

        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            throw "Invalid environment entry in $Path"
        }

        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1)
        if (-not ($name.StartsWith("TRADE_AGENT_") -or $name.StartsWith("MT5_CONNECTOR_"))) {
            continue
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Import-TradeAgentEnvironment -Path $EnvironmentFile
Set-Location -LiteralPath $ProjectRoot

if ($Role -eq "Backend") {
    $command = Join-Path $ProjectRoot ".venv\Scripts\trade-agent.exe"
    if (-not (Test-Path -LiteralPath $command -PathType Leaf)) {
        throw "Backend executable was not found: $command. Run Bootstrap-TradeAgent.ps1 first."
    }
    & $command
    exit $LASTEXITCODE
}

$python = Join-Path $ProjectRoot "connector\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Connector Python was not found: $python. Run Bootstrap-TradeAgent.ps1 first."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $python -m mt5_connector.main
exit $LASTEXITCODE
