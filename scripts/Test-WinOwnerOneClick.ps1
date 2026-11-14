#requires -Version 5.1
<#
Integration gate for the beginner one-click installer.
Runs on a disposable checkout/Windows CI, never on a production WinOwner root.
#>
[CmdletBinding()]
param([ValidateRange(1024, 65535)][int]$Port = 18797)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$target = Join-Path $env:TEMP ('winowner-oneclick-'+$Port+'-'+[Guid]::NewGuid().ToString('N'))
$installer = Join-Path $target 'Install-WinOwner.cmd'
$bootstrap = Join-Path $target 'scripts\Bootstrap-WindowsMcp.ps1'
$taskName = "WinOwnerMCP-$Port"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "Refusing test: task $taskName already exists."
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Refusing test: port $Port already occupied."
}
$ok = $false
try {
    $zip = Join-Path $root 'dist\winowner-mcp-v1.2.0.zip'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Build-ReleasePackage.ps1') -ProjectRoot $root -Version v1.2.0
    if ($LASTEXITCODE -ne 0) { throw 'Release builder failed in one-click test' }
    # Use the same .NET ZIP implementation as the release builder:
    # CI Windows PowerShell may lack the Archive auto-import cmdlets.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($zip,$target)
    if(-not(Test-Path -LiteralPath $installer -PathType Leaf)){
        $listing=@(Get-ChildItem -LiteralPath $target -Name -ErrorAction SilentlyContinue) -join ','
        throw "Extracted Windows release missing Install-WinOwner.cmd (entries: $listing)"
    }
    & $installer -NoPythonInstall -NoOpenGuide -OwnerFullControl -Port $Port
    if ($LASTEXITCODE -ne 0) { throw "One-click launcher exit: $LASTEXITCODE" }
    $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($h.status -ne "ok" -or $h.profile -ne "owner_full_control" -or
        $h.require_autonomous_auth -ne $true -or $h.allow_loopback_tunnel_identity -ne $false) {
        throw "Unexpected profile or auth boundary."
    }
    $resultPath = Join-Path $target "WinOwner-SETUP-RESULT.txt"
    if (-not (Test-Path -LiteralPath $resultPath)) {
        throw "Beginner-friendly setup status file is missing."
    }
    $resultText = Get-Content -LiteralPath $resultPath -Raw
    if ($resultText -notmatch "LOCAL_INSTALL_PASS" -or $resultText -notmatch "owner_full_control") {
        throw "Setup result file did not confirm the authenticated owner profile."
    }
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if ($task.State -ne "Running") { throw "Owner MCP task was not running." }
    $report = Get-Content (Join-Path $target "runtime\logs\bootstrap-report.json") -Raw | ConvertFrom-Json
    if ($report.profile -ne "owner_full_control" -or $report.owner_global_filesystem_enabled -ne $true -or
        $report.full_control_requires_verified_owner -ne $true -or $report.remote_oauth_paired -ne $false) {
        throw "Owner-mode report did not match the expected authorization state."
    }
    try {
        $body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
        $null = Invoke-WebRequest "http://127.0.0.1:$Port/mcp" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 5
        throw "Anonymous MCP tool discovery was incorrectly accepted."
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 401) { throw }
    }
    Write-Host "ONECLICK_OWNER_FULL_PROFILE_PASS: task, profile, and anonymous-denial verified."
    $ok = $true
} finally {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -Remove -Port $Port
        if ($LASTEXITCODE -ne 0) { throw "Test cleanup failed." }
    }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "Test left a scheduled task behind."
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Test left a listener behind."
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
    }
    Write-Host "ONECLICK_CLEANUP_PASS: no task, no listener."
}
if (-not $ok) { throw "One-click verification did not complete." }