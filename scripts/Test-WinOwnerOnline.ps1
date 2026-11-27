#requires -Version 5.1
<#
End-to-end one-command installation acceptance on a disposable Windows install.
The archive is produced locally to avoid network flakiness on GitHub-hosted CI.
#>
[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 18793)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$target = Join-Path $env:TEMP ('winowner-online-smoke-' + $Port + '-' + [Guid]::NewGuid().ToString('N'))
$zip = Join-Path $root 'dist\winowner-mcp-v1.2.0.zip'
$taskName = "WinOwnerMCP-$Port"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "Refusing test: $taskName already exists."
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Refusing test: port $Port already in use."
}
$ok = $false
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Build-ReleasePackage.ps1') -ProjectRoot $root -Version v1.2.0
    if ($LASTEXITCODE -ne 0) { throw "Release build failed." }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'go.ps1') -ArchivePath $zip -InstallRoot $target -Port $Port -NoPythonInstall
    if ($LASTEXITCODE -ne 0) { throw "One-command full local install failed." }
    $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($health.profile -ne 'read_only' -or $health.require_autonomous_auth -ne $true -or
        $health.allow_loopback_tunnel_identity -ne $false) {
        throw "Owner profile / authentication checks failed."
    }
    & (Join-Path $target 'Check-WinOwner.cmd') -Port $Port
    if ($LASTEXITCODE -ne 0) { throw "Check-WinOwner.cmd failed." }
    $ok = $true
    Write-Host 'ONE_COMMAND_INSTALL_AND_CHECK_PASS'
} finally {
    if (Test-Path (Join-Path $target 'Uninstall-WinOwner.cmd')) {
        & (Join-Path $target 'Uninstall-WinOwner.cmd') -Port $Port
        if ($LASTEXITCODE -ne 0) { throw "Uninstall failed." }
    }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        throw "Test task remains after uninstall."
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Test listener remains after uninstall."
    }
    Write-Host 'ONE_COMMAND_UNINSTALL_AND_CLEANUP_PASS'
}
if (-not $ok) { throw "One-command verification incomplete." }