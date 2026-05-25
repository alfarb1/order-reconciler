# One-shot installer: registers a Windows Scheduled Task that runs the KNET
# reconciliation every Monday at 9:00 AM. Re-run to overwrite an existing task.
#
# Usage:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\install-weekly-task.ps1
# Or to uninstall:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\install-weekly-task.ps1 -Uninstall

param(
    [switch]$Uninstall,
    [string]$TaskName = "KNET Weekly Reconciliation",
    [string]$Day = "Monday",
    [string]$Time = "09:00"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runner      = Join-Path $PSScriptRoot "run-weekly.ps1"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Unregistered scheduled task: $TaskName" -ForegroundColor Green
    } else {
        Write-Host "No task named '$TaskName' found." -ForegroundColor Yellow
    }
    exit 0
}

if (-not (Test-Path $runner)) {
    throw "Runner script not found: $runner"
}

# Install BurntToast for richer toasts. Best-effort; the runner falls back to
# WinForms balloon tips if this isn't present.
if (-not (Get-Module -ListAvailable -Name BurntToast)) {
    Write-Host "Installing BurntToast (CurrentUser scope) for rich toast notifications..."
    try {
        Install-Module -Name BurntToast -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop
        Write-Host "BurntToast installed." -ForegroundColor Green
    } catch {
        Write-Warning "Could not install BurntToast ($_). The runner will fall back to balloon tips."
    }
}

# Build the task. Wrap the runner script invocation in pwsh so the task uses
# PowerShell 7+ when available (matches what the user has installed via gh).
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
if ($pwsh) {
    $exec = $pwsh.Source
} else {
    $exec = "powershell.exe"
}
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""

$action  = New-ScheduledTaskAction -Execute $exec -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# Remove any existing copy first so re-running this script updates the schedule
# rather than failing.
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Weekly KNET reconciliation: fetches new Gmail, parses, reconciles, writes report, shows toast." | Out-Null

Write-Host ""
Write-Host "Installed scheduled task '$TaskName'" -ForegroundColor Green
Write-Host "  Schedule:      $Day at $Time"
Write-Host "  Runner:        $runner"
Write-Host "  Project root:  $projectRoot"
Write-Host "  Log file:      $(Join-Path $PSScriptRoot 'weekly.log')"
Write-Host ""
Write-Host "Verify with:  Get-ScheduledTask -TaskName '$TaskName'"
Write-Host "Run it now:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Uninstall:    pwsh -File .\scripts\install-weekly-task.ps1 -Uninstall"
