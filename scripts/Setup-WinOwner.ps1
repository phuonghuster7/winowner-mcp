#requires -Version 5.1
<#
.SYNOPSIS
    Beginner front-end for Windows MCP: owner-full-control after verified OAuth.
.DESCRIPTION
    Does not bypass owner OAuth, tunnel association or the mandatory grant.
    Uses the verified bootstrap as a child
    process so that its exit codes are preserved.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoPythonInstall,
    [switch]$NoOpenGuide,
    [switch]$ReadOnly,
    [ValidateRange(1024, 65535)][int]$Port = 8768
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$bootstrap = Join-Path $PSScriptRoot "Bootstrap-WindowsMcp.ps1"
$guide = Join-Path $root "docs\START_HERE.vi.md"
$powershellExe = Join-Path $PSHOME "powershell.exe"

function Write-SetupResult([string]$state, [string]$mode) {
    # No secrets or OAuth values are written to this human-readable summary.
    $resultPath = Join-Path $root "WinOwner-SETUP-RESULT.txt"
    $lines = @(
        "WinOwner MCP v1.1.0",
        "Windows install status: $state",
        "Local profile: $mode",
        "ChatGPT connected: NOT YET (owner OAuth required)",
        "Local MCP: http://127.0.0.1:$Port/mcp",
        "Next steps: docs\START_HERE.vi.md",
        "This file contains no keys or authentication tokens."
    )
    try {
        [IO.File]::WriteAllText($resultPath, ($lines -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warning "Could not save the local setup result; check the installer console."
    }
}
function Stop-Setup([string]$message, [int]$exitCode = 1) {
    Write-Host "[WinOwner] ERROR: $message" -ForegroundColor Red
    Write-SetupResult "FAILED ($exitCode)" "UNVERIFIED"
    Write-Host "[WinOwner] Guide: $guide"
    exit $exitCode
}
if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
    Stop-Setup "The package is incomplete: scripts\Bootstrap-WindowsMcp.ps1 missing." 2
}
if (-not (Test-Path -LiteralPath $guide -PathType Leaf)) {
    Stop-Setup "The package is incomplete: docs\START_HERE.vi.md missing." 2
}
if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) {
    Stop-Setup "Windows PowerShell executable was not found." 2
}

Write-Host "==========================================================="
Write-Host " WinOwner MCP - LOCAL setup"
$profile = if ($ReadOnly) { 'read_only' } else { 'owner_full_control' }
Write-Host " Mode: $(if ($DryRun) { 'CHECK ONLY' } else { 'OWNER FULL CONTROL' }) | Port: $Port"
Write-Host "==========================================================="
Write-Host "Full control is enabled for a verified, authorized owner only."
Write-Host "Unauthenticated/non-owner clients remain blocked; ChatGPT OAuth pairing is required."
Write-Host "It uses the current Windows account; no Administrator prompt is required."
Write-Host ""
$arguments = @("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $bootstrap), "-Port", "$Port")
if ($DryRun) { $arguments += "-DryRun" }
if ($NoPythonInstall) { $arguments += "-NoPythonInstall" }
if (-not $ReadOnly) { $arguments += "-OwnerFullControl" }

try {
    $child = Start-Process -FilePath $powershellExe -ArgumentList $arguments -WorkingDirectory $root -PassThru -Wait -NoNewWindow -ErrorAction Stop
    if ($child.ExitCode -ne 0) {
        Stop-Setup "Bootstrap failed (exit $($child.ExitCode)). No cloud access was enabled. If Python is missing, install Python 3.12 from python.org or use Windows winget, then rerun." $child.ExitCode
    }
    if ($DryRun) {
        Write-Host "[WinOwner] DRY_RUN_PASS: no service/task configured." -ForegroundColor Green
        exit 0
    }
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5 -ErrorAction Stop
    if ($health.status -ne "ok" -or $health.profile -ne $profile -or
        $health.require_autonomous_auth -ne $true -or
        $health.allow_loopback_tunnel_identity -ne $false) {
        Stop-Setup "Local health/auth safety checks failed. Do not configure a tunnel." 5
    }
    $reportPath = Join-Path $root "runtime\logs\bootstrap-report.json"
    if (-not (Test-Path -LiteralPath $reportPath)) {
        Stop-Setup "Local health is OK but the bootstrap report is missing." 5
    }
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.status -ne "LOCAL_INSTALL_PASS" -or $report.remote_oauth_paired -ne $false -or
        $report.profile -ne $profile -or
        $report.full_control_requires_verified_owner -ne $true -or
        $report.owner_global_filesystem_enabled -ne (-not $ReadOnly)) {
        Stop-Setup "Unexpected bootstrap report. Review the local configuration." 5
    }
    Write-Host ""
    Write-Host "LOCAL_INSTALL_PASS: protected owner profile ($profile) on http://127.0.0.1:$Port/mcp" -ForegroundColor Green
    Write-Host "NEXT: ChatGPT connection is NOT active. Follow docs\START_HERE.vi.md." -ForegroundColor Yellow
    Write-Host "NEXT: Add your own tunnel in ChatGPT Plugins, then complete OAuth once." -ForegroundColor Yellow
    Write-Host "No additional windows are launched by this installer."
    Write-SetupResult "LOCAL_INSTALL_PASS" $profile
    exit 0
} catch {
    Stop-Setup $_.Exception.Message 1
}
