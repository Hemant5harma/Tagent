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
$runner = Join-Path $ProjectRoot "deploy\windows\Run-TradeAgent.ps1"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Runner script was not found: $runner"
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType InteractiveToken -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 9999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -StartWhenAvailable -MultipleInstances IgnoreNew
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

foreach ($role in @("Backend", "Connector")) {
    $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -Role $role " +
        "-ProjectRoot `"$ProjectRoot`" -EnvironmentFile `"$EnvironmentFile`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
    $taskName = "TradeAgent-$role"
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
}
