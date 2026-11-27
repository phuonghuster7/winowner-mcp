#requires -Version 5.1
<#
Isolated upgrade acceptance: simulates a v1.1.0 task/root layout using
current release files. Tests validated task ownership and side-by-side upgrade.
No production directories or default port are touched.
#>
[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port=18796)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$base=Join-Path $env:TEMP ('winowner-upgrade-smoke-'+[Guid]::NewGuid().ToString('N'))
$legacy=Join-Path $base 'v1.1.0'
$current=Join-Path $base 'v1.2.0'
$zip=Join-Path $root 'dist\winowner-mcp-v1.2.0.zip'
$taskName="WinOwnerMCP-$Port"
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw "Cannot test occupied task $taskName"}
if(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue){throw 'Cannot test occupied port'}
New-Item -ItemType Directory -Path $legacy -Force | Out-Null
# The runner's TEMP path can be an 8.3 alias (RUNNER~1).
# Compare task action paths only after resolving the existing test root.
$canonicalBase=(Get-Item -LiteralPath $base).FullName
$base=$canonicalBase
$legacy=Join-Path $base 'v1.1.0'
$current=Join-Path $base 'v1.2.0'
$passed=$false
try{
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Build-ReleasePackage.ps1') -ProjectRoot $root -Version v1.2.0
    if($LASTEXITCODE -ne 0){throw 'Failed to package isolated release'}
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($zip,$legacy)
    if(-not(Test-Path -LiteralPath (Join-Path $legacy 'Install-WinOwner.cmd') -PathType Leaf)){
        throw 'Extracted isolated legacy fixture lacks the installer'
    }
    & (Join-Path $legacy 'Install-WinOwner.cmd') -NoPythonInstall -NoOpenGuide -Port $Port
    if($LASTEXITCODE -ne 0){throw 'Could not start simulated old local task'}
    $before=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if($before.profile -ne 'read_only'){throw 'Old task precondition was not read-only'}
    $digest=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
    $marker=Join-Path $legacy 'upgrade-test-settings.txt'
    [IO.File]::WriteAllText($marker,'isolated fixture: preserve previous settings')
    $invalidDigest = if($digest.StartsWith('0')) { ('1' * 64) } else { ('0' * 64) }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'go.ps1') -ArchivePath $zip -ExpectedArchiveSha256 $invalidDigest -InstallRoot $current -LegacyInstallRoot $legacy -UpgradeConfirmed -NoPythonInstall -Port $Port
    if($LASTEXITCODE -eq 0 -or (Test-Path -LiteralPath $current)){throw 'Digest mismatch was not rejected before installation'}
    $unchanged=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if($unchanged.profile -ne 'read_only'){throw 'Previous service changed after invalid digest'}
    Write-Host 'DIGEST_MISMATCH_REJECTED'
    # An isolated broken installer simulates failure after the old service stops.
    $broken=Join-Path $base 'broken-install'
    New-Item -ItemType Directory -Path $broken -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $broken 'Install-WinOwner.cmd'),"@echo off" + [Environment]::NewLine + "exit /b 9" + [Environment]::NewLine,[Text.Encoding]::ASCII)
    [IO.File]::WriteAllText((Join-Path $broken 'Check-WinOwner.cmd'),"@echo off" + [Environment]::NewLine + "exit /b 9" + [Environment]::NewLine,[Text.Encoding]::ASCII)
    $failureOutput=(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'go.ps1') -InstallRoot $broken -LegacyInstallRoot $legacy -UpgradeConfirmed -NoPythonInstall -Port $Port | Out-String)
    $failureExit=$LASTEXITCODE
    Write-Host $failureOutput
    if($failureExit -eq 0 -or $failureOutput -notmatch 'ROLLBACK_VERIFIED'){throw 'Failed upgrade did not verify recovery'}
    $restoredTask=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if(@($restoredTask.Actions)[0].Arguments.IndexOf((Join-Path $legacy 'scripts\Run-WinOwnerMcp.py'),[StringComparison]::OrdinalIgnoreCase) -lt 0){throw 'Rollback task did not return to previous root'}
    if([IO.File]::ReadAllText($marker) -ne 'isolated fixture: preserve previous settings'){throw 'Rollback modified previous settings'}
    Write-Host 'FAILED_UPGRADE_ROLLBACK_PASS'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'go.ps1') -ArchivePath $zip -ExpectedArchiveSha256 $digest -InstallRoot $current -LegacyInstallRoot $legacy -UpgradeConfirmed -NoPythonInstall -Port $Port
    if($LASTEXITCODE -ne 0){throw 'Upgrader returned nonzero'}
    $after=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if($after.profile -ne 'read_only' -or $after.require_autonomous_auth -ne $true){throw 'New local service violates security profile'}
    $newTask=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if(@($newTask.Actions)[0].Arguments.IndexOf((Join-Path $current 'scripts\Run-WinOwnerMcp.py'),[StringComparison]::OrdinalIgnoreCase) -lt 0){
        throw 'Upgrade task did not switch to new version root'
    }
    if(-not(Test-Path (Join-Path $legacy 'runtime\secrets\gateway_caller.token'))){throw 'Old private credentials were erased'}
    if([IO.File]::ReadAllText($marker) -ne 'isolated fixture: preserve previous settings'){throw 'Upgrade modified previous settings'}
    $newMarker=Join-Path $current 'repair-test-settings.txt'
    [IO.File]::WriteAllText($newMarker,'isolated fixture: preserve current settings')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'go.ps1') -InstallRoot $current -LegacyInstallRoot (Join-Path $base 'no-legacy') -NoPythonInstall -Port $Port
    if($LASTEXITCODE -ne 0){throw 'Same-version repair failed'}
    if([IO.File]::ReadAllText($newMarker) -ne 'isolated fixture: preserve current settings'){throw 'Repair modified current settings'}
    Write-Host 'SAME_VERSION_REPAIR_PASS'
    Write-Host 'SIDE_BY_SIDE_UPGRADE_PASS: new owner-bound task verified, old secrets retained'
    $passed=$true
}finally{
    foreach($install in @($current,$legacy)){
        if(Test-Path (Join-Path $install 'Uninstall-WinOwner.cmd')){
            $task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if($task -and @($task.Actions)[0].Arguments -like "*$install*"){
                & (Join-Path $install 'Uninstall-WinOwner.cmd') -Port $Port
                if($LASTEXITCODE -ne 0){throw 'Failed to stop isolated test task'}
            }
        }
    }
    if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Upgrade test left task behind'}
    if(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue){throw 'Upgrade test left listener behind'}
    Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
}
if(-not $passed){throw 'Upgrade acceptance incomplete'}