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
$passed=$false
try{
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Build-ReleasePackage.ps1') -ProjectRoot $root -Version v1.2.0
    if($LASTEXITCODE -ne 0){throw 'Failed to package isolated release'}
    Expand-Archive -LiteralPath $zip -DestinationPath $legacy -Force
    & (Join-Path $legacy 'Install-WinOwner.cmd') -NoPythonInstall -NoOpenGuide -Port $Port
    if($LASTEXITCODE -ne 0){throw 'Could not start simulated old local task'}
    $before=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if($before.profile -ne 'read_only'){throw 'Old task precondition was not read-only'}
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'go.ps1') -ArchivePath $zip -InstallRoot $current -LegacyInstallRoot $legacy -UpgradeConfirmed -NoPythonInstall -Port $Port
    if($LASTEXITCODE -ne 0){throw 'Upgrader returned nonzero'}
    $after=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if($after.profile -ne 'read_only' -or $after.require_autonomous_auth -ne $true){throw 'New local service violates security profile'}
    $newTask=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if(@($newTask.Actions)[0].Arguments.IndexOf((Join-Path $current 'scripts\Run-WinOwnerMcp.py'),[StringComparison]::OrdinalIgnoreCase) -lt 0){
        throw 'Upgrade task did not switch to new version root'
    }
    if(-not(Test-Path (Join-Path $legacy 'runtime\secrets\gateway_caller.token'))){throw 'Old private credentials were erased'}
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