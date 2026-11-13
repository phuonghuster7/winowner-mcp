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
    [string]$Ref = 'main',
    [ValidateRange(1024,65535)][int]$Port = 8768,
    [string]$InstallRoot = '',
    [string]$ArchivePath = '',
    [switch]$NoPythonInstall,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$project = 'phuonghuster7/winowner-mcp'
$display = 'WinOwner MCP v1.1.0'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows 10/11 is required.'
}
if ($Ref -notmatch '^[a-zA-Z0-9_./-]+$' -or $Ref.Contains('..') -or $Ref.StartsWith('/')) {
    throw 'Invalid repository reference.'
}
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA 'WinOwnerMCP\v1.1.0'
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$archiveUrl = "https://github.com/$project/archive/refs/heads/$Ref.zip"
$tempDir = ''
Write-Host ''
Write-Host "==== $display - ONLINE SETUP ====" -ForegroundColor Cyan
Write-Host "Install folder: $InstallRoot"
Write-Host 'The local installer uses Owner Full Control ONLY for a verified owner.'
Write-Host 'ChatGPT OAuth and tunnel account authorization are required separately.'
if ($DryRun) {
    Write-Host "DRY_RUN_PASS: download=$archiveUrl"
    exit 0
}
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
    Write-Host 'Installing and checking Windows MCP...'
    $installerArgs = @('-NoOpenGuide','-Port',[string]$Port)
    if ($NoPythonInstall) { $installerArgs += '-NoPythonInstall' }
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
    Write-Host ''
    Write-Host '[THANH CONG] WinOwner MCP da cai dat tren Windows.' -ForegroundColor Green
    Write-Host "Kiem tra: $InstallRoot\Check-WinOwner.cmd"
    Write-Host "Huong dan: $InstallRoot\docs\START_HERE.vi.md"
    Write-Host 'ChatGPT: chua ket noi, can xac thuc tunnel/OAuth cua tai khoan.'
} catch {
    Write-Host ('[THAT BAI] ' + $_.Exception.Message) -ForegroundColor Red
    if ($env:WINOWNER_DEBUG -eq '1') { Write-Host ('DEBUG: ' + $_.ScriptStackTrace) }
    Write-Host 'Khong tu cap quyen, khong xoa file/dich vu da co.'
    exit 1
} finally {
    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}