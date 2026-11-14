#requires -Version 5.1
<#
.SYNOPSIS
  WinOwner MCP one-command Windows installer.
.DESCRIPTION
  Download only this project's HTTPS GitHub source archive, extract into
  the current user's LocalAppData and invoke the existing authenticated
  owner-profile installer. No elevated Windows rights or credentials required
  for LOCAL install. ChatGPT tunnel/OAuth pairing remains account-controlled.
#>
[CmdletBinding()]
param(
    [string]$Ref = 'v1.2.0',
    [ValidateRange(1024,65535)][int]$Port = 8768,
    [string]$InstallRoot = '',
    [string]$ArchivePath = '',
    [ValidatePattern('^(?:[A-Fa-f0-9]{64})?$')][string]$ExpectedArchiveSha256 = '',
    [switch]$NoPythonInstall,
    [switch]$OwnerFullControl,
    [string]$LegacyInstallRoot = '',
    [switch]$UpgradeConfirmed,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$project = 'phuonghuster7/winowner-mcp'
$display = 'WinOwner MCP v1.2.0'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows 10/11 is required.'
}
if ($Ref -notmatch '^[a-zA-Z0-9_./-]+$' -or $Ref.Contains('..') -or $Ref.StartsWith('/')) {
    throw 'Invalid repository reference.'
}
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA 'WinOwnerMCP\v1.2.0'
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$archiveUrl = "https://github.com/$project/archive/refs/tags/$Ref.zip"
$tempDir = ''
Write-Host ''
Write-Host "==== $display - ONLINE SETUP ====" -ForegroundColor Cyan
Write-Host "Install folder: $InstallRoot"
Write-Host 'The installer defaults to READ ONLY. Full Control requires explicit owner approval.'
Write-Host 'ChatGPT Authentication None is possible with an authenticated tunnel; owner tools still need owner authorization.'
if ($env:WINOWNER_BOOTSTRAP_DRYRUN -eq '1') { $DryRun = $true }
if ($DryRun) {
    Write-Host "DRY_RUN_PASS: download=$archiveUrl"
    exit 0
}
$legacyStopped = $false
$legacyMode = ''
try {
    $setupFile = Join-Path $InstallRoot 'Install-WinOwner.cmd'
    if (Test-Path -LiteralPath $InstallRoot) {
        if (-not (Test-Path -LiteralPath $setupFile -PathType Leaf)) {
            throw "Install folder is occupied by another directory: $InstallRoot. Refusing overwrite."
        }
        Write-Host 'Existing WinOwner folder found; checking and repairing without deleting user settings.'
    } else {
        $tempDir = Join-Path $env:TEMP ("winowner-download-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
        $zip = Join-Path $tempDir 'source.zip'
        Write-Host 'Downloading trusted source archive from GitHub...'
        if ($ArchivePath) {
            # For offline CI/owner integration tests; the one-line public command always downloads HTTPS.
            Copy-Item -LiteralPath $ArchivePath -Destination $zip -ErrorAction Stop
        } else {
            Invoke-WebRequest -Uri $archiveUrl -OutFile $zip -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 120
        }
        if ((Get-Item -LiteralPath $zip).Length -lt 30000) {
            throw 'Downloaded archive is unexpectedly small.'
        }
        if ($ExpectedArchiveSha256) {
            $actualSha256 = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
            if ($actualSha256 -ne $ExpectedArchiveSha256) {
                throw 'Archive SHA-256 mismatch. Installation files and existing services were not changed.'
            }
            Write-Host 'ARCHIVE_SHA256_PASS'
        } else {
            Write-Warning 'Archive digest not independently supplied. HTTPS and a pinned tag alone do not verify release integrity.'
        }
        $unpack = Join-Path $tempDir 'unpacked'
        Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
        # GitHub source archives have one wrapper folder; release ZIPs contain flat root entries.
        $roots = @((Get-Item -LiteralPath $unpack)) + @(
            Get-ChildItem -LiteralPath $unpack -Directory
        )
        $candidates = @($roots |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'scripts\Setup-WinOwner.ps1') -PathType Leaf })
        if ($candidates.Count -ne 1) {
            throw 'The downloaded archive has no unique, complete WinOwner installer.'
        }
        foreach ($required in @(
            'Install-WinOwner.cmd', 'Check-WinOwner.cmd', 'Uninstall-WinOwner.cmd',
            'scripts\Bootstrap-WindowsMcp.ps1', 'config\policy.example.json',
            'requirements.txt', 'docs\START_HERE.vi.md'
        )) {
            if (-not (Test-Path -LiteralPath (Join-Path $candidates[0].FullName $required) -PathType Leaf)) {
                throw "Missing required release content: $required"
            }
        }
        $parent = Split-Path -Path $InstallRoot -Parent
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        # Move only after validating the complete archive. Never overwrite an existing install.
        if (Test-Path -LiteralPath $InstallRoot) {
            throw 'Install folder appeared while downloading; refusing overwrite.'
        }
        Move-Item -LiteralPath $candidates[0].FullName -Destination $InstallRoot -ErrorAction Stop
    }
    Write-Host ''
    # v1.1.0 -> v1.2.0: keep the old install and secrets for rollback.
    # Never stop another task; only the task whose action matches the known old root.
    $legacyRoot = if ($LegacyInstallRoot) { [IO.Path]::GetFullPath($LegacyInstallRoot) } else { Join-Path $env:LOCALAPPDATA 'WinOwnerMCP\v1.1.0' }
    if (-not [string]::Equals($InstallRoot,$legacyRoot,[StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath (Join-Path $legacyRoot 'Uninstall-WinOwner.cmd') -PathType Leaf)) {
        $legacyTask = Get-ScheduledTask -TaskName "WinOwnerMCP-$Port" -ErrorAction SilentlyContinue
        if ($legacyTask) {
            $legacyAction = @($legacyTask.Actions)[0]
            $legacyRunner = Join-Path $legacyRoot 'scripts\Run-WinOwnerMcp.py'
            if ($legacyAction.Arguments -and
                $legacyAction.Arguments.IndexOf($legacyRunner,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                $legacyTask.Principal.UserId -in @([Security.Principal.WindowsIdentity]::GetCurrent().Name,$env:USERNAME,("$env:USERDOMAIN\$env:USERNAME"))) {
                Write-Warning 'WinOwner v1.1.0 is active. Upgrading temporarily stops remote ChatGPT access.'
                Write-Host 'Old files and private credentials are preserved; the new tunnel may need reconnecting.'
                $answer=if($UpgradeConfirmed){'UPGRADE'}else{(Read-Host 'Type UPGRADE to continue').Trim()}
                if ($answer -cne 'UPGRADE') { throw 'Upgrade declined; old service left unchanged.' }
                $legacyMode=if ($legacyAction.Arguments -match '--profile\s+read_only') { 'read_only' } else { 'owner_full_control' }
                & (Join-Path $legacyRoot 'Uninstall-WinOwner.cmd') -Port $Port
                if ($LASTEXITCODE -ne 0) { throw 'Failed to safely stop old service. New install was not started.' }
                $legacyStopped=$true
            } else {
                throw 'Existing MCP task does not belong to the verified old version. Refusing upgrade.'
            }
        }
    }
    Write-Host 'Installing and checking Windows MCP...'
    $checkFile = Join-Path $InstallRoot 'Check-WinOwner.cmd'
    if (-not (Test-Path -LiteralPath $checkFile -PathType Leaf)) {
        throw 'Local check script missing; refusing to report successful installation.'
    }
    $installerArgs = @('-NoOpenGuide','-Port',[string]$Port)
    if ($NoPythonInstall) { $installerArgs += '-NoPythonInstall' }
    if ($OwnerFullControl) { $installerArgs += '-OwnerFullControl' }
    & $setupFile @installerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Local installer reported exit code $LASTEXITCODE. Check WinOwner-SETUP-RESULT.txt in $InstallRoot."
    }
    $resultPath = Join-Path $InstallRoot 'WinOwner-SETUP-RESULT.txt'
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        throw 'Setup status file was not created.'
    }
    $statusText = [IO.File]::ReadAllText($resultPath,[Text.Encoding]::UTF8)
    if (-not $statusText.Contains('LOCAL_INSTALL_PASS')) {
        throw 'The local installation did not report success.'
    }
    & $checkFile -Port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Post-install local health/auth verification failed.' }
    Write-Host ''
    Write-Host '[THANH CONG] WinOwner MCP da cai dat tren Windows.' -ForegroundColor Green
    Write-Host "Kiem tra: $InstallRoot\Check-WinOwner.cmd"
    Write-Host "Huong dan: $InstallRoot\docs\START_HERE.vi.md"
    Write-Host 'ChatGPT: chua ket noi; can tunnel bao ve va quyen owner cho cac tool dac quyen.'
} catch {
    if ($legacyStopped) {
        Write-Warning 'New install failed. Attempting verified rollback to previous version.'
        try {
            $newTask=Get-ScheduledTask -TaskName "WinOwnerMCP-$Port" -ErrorAction SilentlyContinue
            if ($newTask) {
                $actions=@($newTask.Actions)
                $expectedRunner=Join-Path $InstallRoot 'scripts\Run-WinOwnerMcp.py'
                $expectedArgs='^-u\s+"'+[regex]::Escape($expectedRunner)+'"\s+--port\s+'+$Port+'\s+--profile\s+(read_only|owner_full_control)$'
                if ($actions.Count -ne 1 -or $actions[0].Arguments -notmatch $expectedArgs -or
                    $actions[0].Execute -notin @((Join-Path $InstallRoot '.venv\Scripts\python.exe'),(Join-Path $InstallRoot '.venv\Scripts\pythonw.exe')) -or
                    $newTask.Principal.UserId -notin @([Security.Principal.WindowsIdentity]::GetCurrent().Name,$env:USERNAME,("$env:USERDOMAIN\$env:USERNAME"))) {
                    throw 'Rollback refused to stop an unverified task.'
                }
                & (Join-Path $InstallRoot 'Uninstall-WinOwner.cmd') -Port $Port
                if ($LASTEXITCODE -ne 0) { throw 'New service removal failed.' }
            }
            $oldArgs=@('-NoPythonInstall','-NoOpenGuide','-Port',[string]$Port)
            if ($legacyMode -eq 'read_only') { $oldArgs += '-ReadOnly' }
            & (Join-Path $legacyRoot 'Install-WinOwner.cmd') @oldArgs
            if ($LASTEXITCODE -ne 0) { throw 'Previous service startup failed.' }
            & (Join-Path $legacyRoot 'Check-WinOwner.cmd') -Port $Port
            if ($LASTEXITCODE -ne 0) { throw 'Previous service failed health/auth checks.' }
            $restored=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
            if ($restored.profile -ne $legacyMode -or $restored.require_autonomous_auth -ne $true -or
                $restored.allow_loopback_tunnel_identity -ne $false) {
                throw 'Previous service profile or authentication changed.'
            }
            Write-Host 'ROLLBACK_VERIFIED: previous local service restored; remote pairing not verified.'
        } catch { Write-Warning 'ROLLBACK_INCOMPLETE: previous files remain for manual recovery; check local service before reconnecting.' }
    }
    Write-Host ('[THAT BAI] ' + $_.Exception.Message) -ForegroundColor Red
    if ($env:WINOWNER_DEBUG -eq '1') { Write-Host ('DEBUG: ' + $_.ScriptStackTrace) }
    Write-Host 'Khong tu cap quyen, khong xoa file/dich vu da co.'
    exit 1
} finally {
    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}