# Weekly KNET Reconciliation runner. Invoked by Task Scheduler.
#
# Runs `knet-reconcile weekly`, parses the trailing SUMMARY line, and shows a
# Windows toast notification linking to the freshly written report. Output is
# tee'd to scripts/weekly.log so the most recent run can be inspected even if
# the toast was missed.

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$cli         = Join-Path $projectRoot ".venv\Scripts\knet-reconcile.exe"
$log         = Join-Path $PSScriptRoot "weekly.log"

if (-not (Test-Path $cli)) {
    "$(Get-Date -Format o)  cli not found at $cli" | Out-File -FilePath $log -Append -Encoding utf8
    exit 1
}

Set-Location $projectRoot

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== $timestamp  weekly run start ===" | Out-File -FilePath $log -Append -Encoding utf8

# Run the CLI and capture all output. Tee to log AND keep in memory so we can
# parse the trailing SUMMARY line for the toast.
$lines = & $cli weekly 2>&1 | ForEach-Object {
    $_.ToString() | Tee-Object -FilePath $log -Append
    $_
}
$exitCode = $LASTEXITCODE

$summary = ($lines | Where-Object { $_ -match '^SUMMARY ' } | Select-Object -Last 1)
if (-not $summary -or $exitCode -ne 0) {
    Show-Toast -Title "KNET Reconciliation failed" -Body "exit=$exitCode. Check $log" -ReportPath ""
    exit $exitCode
}

# Parse: SUMMARY missing=4 matched=13 pending=17 orphans=15 total=34 report=C:\...
$kv = @{}
foreach ($pair in ($summary -replace '^SUMMARY ', '' -split '\s+')) {
    if ($pair -match '^([^=]+)=(.*)$') { $kv[$Matches[1]] = $Matches[2] }
}

function _kv_int($map, $key) {
    if ($map.ContainsKey($key)) { return [int]$map[$key] }
    return 0
}
$missing = _kv_int $kv 'missing'
$matched = _kv_int $kv 'matched'
$pending = _kv_int $kv 'pending'
$report  = $kv['report']

if ($missing -gt 0) {
    $title = "KNET: $missing missing - action needed"
} else {
    $title = "KNET: all $matched matched, $pending in transit"
}
$body = "matched=$matched  missing=$missing  pending=$pending`nClick to open the report"

function Show-Toast {
    param([string]$Title, [string]$Body, [string]$ReportPath)

    # Prefer BurntToast if installed (rich toasts, clickable).
    if (Get-Module -ListAvailable -Name BurntToast) {
        Import-Module BurntToast -ErrorAction Stop
        $btnArgs = @{
            Text = @($Title, $Body)
            AppLogo = $null
        }
        if ($ReportPath -and (Test-Path $ReportPath)) {
            # Add a "Open report" button that opens the xlsx.
            $btn = New-BTButton -Content "Open report" -Arguments $ReportPath
            $btnArgs['Button'] = $btn
        }
        New-BurntToastNotification @btnArgs
        return
    }

    # Fallback: balloon tip via WinForms — works on every Windows 10+ without extra modules.
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.BalloonTipTitle = $Title
    $icon.BalloonTipText = $Body
    $icon.Visible = $true
    $icon.ShowBalloonTip(10000)
    Start-Sleep -Seconds 12
    $icon.Dispose()
}

Show-Toast -Title $title -Body $body -ReportPath $report
"=== $timestamp  done: missing=$missing matched=$matched pending=$pending ===" | Out-File -FilePath $log -Append -Encoding utf8
