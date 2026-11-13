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
    [string]$Ref = 'v1.1.1',
    [ValidateRange(1024,65535)][int]$Port = 8768,
    [string]$InstallRoot = '',
    [string]$ArchivePath = '',
    [string]$ExpectedSha256 = '',
    [switch]$AllowUnpinned,
    [switch]$NoPythonInstall,
    [switch]$Update,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Explicit TLS 1.2 for Windows PowerShell 5.1 HTTPS downloads
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# Environment variable overrides allow `irm <url> | iex` to pass options without param binding
if (-not $PSBoundParameters.ContainsKey('Ref') -and $env:WINOWNER_REF) { $Ref = $env:WINOWNER_REF }
if (-not $PSBoundParameters.ContainsKey('Port') -and $env:WINOWNER_PORT) { $Port = [int]$env:WINOWNER_PORT }
if (-not $PSBoundParameters.ContainsKey('InstallRoot') -and $env:WINOWNER_INSTALL_ROOT) { $InstallRoot = $env:WINOWNER_INSTALL_ROOT }
if (-not $PSBoundParameters.ContainsKey('ArchivePath') -and $env:WINOWNER_ARCHIVE_PATH) { $ArchivePath = $env:WINOWNER_ARCHIVE_PATH }
if (-not $PSBoundParameters.ContainsKey('ExpectedSha256') -and $env:WINOWNER_SHA256) { $ExpectedSha256 = $env:WINOWNER_SHA256 }
if (-not $AllowUnpinned -and $env:WINOWNER_ALLOW_UNPINNED -eq '1') { $AllowUnpinned = $true }
if (-not $NoPythonInstall -and $env:WINOWNER_NO_PYTHON_INSTALL -eq '1') { $NoPythonInstall = $true }
if (-not $Update -and $env:WINOWNER_UPDATE -eq '1') { $Update = $true }
if (-not $DryRun -and $env:WINOWNER_DRYRUN -eq '1') { $DryRun = $true }

$project = 'phuonghuster7/winowner-mcp'
$display = 'WinOwner MCP v1.1.1'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows 10/11 is required.'
}
if ($Ref -notmatch '^[a-zA-Z0-9_./-]+$' -or $Ref.Contains('..') -or $Ref.StartsWith('/')) {
    throw 'Invalid repository reference.'
}
$isPinnedTag = ($Ref -match '^v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$')
if (-not $isPinnedTag -and -not $AllowUnpinned) {
    throw "Refusing unpinned ref '$Ref' without -AllowUnpinned (or WINOWNER_ALLOW_UNPINNED=1)."
}
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA 'WinOwnerMCP\v1.1.1'
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$assetName = "winowner-mcp-$Ref.zip"
if ($isPinnedTag) {
    $archiveUrl = "https://github.com/$project/releases/download/$Ref/$assetName"
    $checksumsUrl = "https://github.com/$project/releases/download/$Ref/SHA256SUMS.txt"
} else {
    $archiveUrl = "https://github.com/$project/archive/refs/heads/$Ref.zip"
    $checksumsUrl = ''
}
$tempDir = ''
Write-Host ''
Write-Host "==== $display - ONLINE SETUP ====" -ForegroundColor Cyan
Write-Host "Install folder: $InstallRoot"
Write-Host 'The local installer defaults to safe read_only mode. Run Enable-FullControl.cmd if needed.'
Write-Host 'ChatGPT OAuth and tunnel account authorization are required separately.'
if ($DryRun) {
    Write-Host "DRY_RUN_PASS: download=$archiveUrl checksums=$checksumsUrl pinned=$isPinnedTag"
    exit 0
}
function Find-PreviousInstallRoot([string]$targetRoot, [int]$targetPort) {
    $parent = Split-Path -Path $targetRoot -Parent
    if ((Test-Path -LiteralPath (Join-Path $targetRoot 'scripts\Bootstrap-WindowsMcp.ps1')) -and
        (Test-Path -LiteralPath (Join-Path $targetRoot 'runtime\secrets'))) {
        return $targetRoot
    }
    try {
        $task = Get-ScheduledTask -TaskName "WinOwnerMCP-$targetPort" -ErrorAction SilentlyContinue
        if ($task -and $task.Actions.Count -eq 1 -and $task.Actions[0].WorkingDirectory) {
            $wd = [IO.Path]::GetFullPath($task.Actions[0].WorkingDirectory)
            if (Test-Path -LiteralPath (Join-Path $wd 'scripts\Bootstrap-WindowsMcp.ps1')) {
                return $wd
            }
        }
    } catch {}
    if (Test-Path -LiteralPath $parent -PathType Container) {
        $dirs = Get-ChildItem -LiteralPath $parent -Directory |
            Where-Object {
                $_.FullName -ne $targetRoot -and
                $_.Name -ne 'backups' -and
                (Test-Path -LiteralPath (Join-Path $_.FullName 'scripts\Bootstrap-WindowsMcp.ps1'))
            } |
            Sort-Object LastWriteTimeUtc -Descending
        if ($dirs) { return $dirs[0].FullName }
    }
    return $null
}

function Backup-WinOwnerState([string]$sourceRoot, [string]$backupDir) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $srcSecrets = Join-Path $sourceRoot 'runtime\secrets'
    if (Test-Path -LiteralPath $srcSecrets -PathType Container) {
        Copy-Item -LiteralPath $srcSecrets -Destination (Join-Path $backupDir 'secrets') -Recurse -Force
    }
    $srcPolicy = Join-Path $sourceRoot 'config\policy.json'
    if (Test-Path -LiteralPath $srcPolicy -PathType Leaf) {
        New-Item -ItemType Directory -Path (Join-Path $backupDir 'config') -Force | Out-Null
        Copy-Item -LiteralPath $srcPolicy -Destination (Join-Path $backupDir 'config\policy.json') -Force
    }
}

function Restore-WinOwnerState([string]$backupDir, [string]$destRoot) {
    $bkSecrets = Join-Path $backupDir 'secrets'
    if (Test-Path -LiteralPath $bkSecrets -PathType Container) {
        $dstSecrets = Join-Path $destRoot 'runtime\secrets'
        New-Item -ItemType Directory -Path $dstSecrets -Force | Out-Null
        Copy-Item -Path (Join-Path $bkSecrets '*') -Destination $dstSecrets -Recurse -Force
        $id = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls.exe $dstSecrets /inheritance:r /grant:r "$($id):(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
    }
    $bkPolicy = Join-Path $backupDir 'config\policy.json'
    if (Test-Path -LiteralPath $bkPolicy -PathType Leaf) {
        $dstConfig = Join-Path $destRoot 'config'
        New-Item -ItemType Directory -Path $dstConfig -Force | Out-Null
        Copy-Item -LiteralPath $bkPolicy -Destination (Join-Path $dstConfig 'policy.json') -Force
    }
}

$backupDir = ''
$previousRoot = $null
try {
    $setupFile = Join-Path $InstallRoot 'Install-WinOwner.cmd'
    if ($Update) {
        $previousRoot = Find-PreviousInstallRoot $InstallRoot $Port
        if ($previousRoot) {
            $parent = Split-Path -Path $InstallRoot -Parent
            $backupDir = Join-Path $parent ("backups\" + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))
            Write-Host "Backing up secrets and policy from $previousRoot..."
            Backup-WinOwnerState $previousRoot $backupDir
        }
    }
    $needDownload = (-not (Test-Path -LiteralPath $InstallRoot))
    if ((Test-Path -LiteralPath $InstallRoot) -and -not $Update) {
        if (-not (Test-Path -LiteralPath $setupFile -PathType Leaf)) {
            throw "Install folder is occupied by another directory: $InstallRoot. Refusing overwrite."
        }
        Write-Host 'Existing WinOwner folder found; checking and repairing without deleting user settings.'
    } elseif ($needDownload -or $Update) {
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
        # Verify SHA-256 checksum for pinned release or explicit ExpectedSha256
        $expectedHash = $ExpectedSha256.Trim().ToUpperInvariant()
        if (-not $expectedHash -and -not $ArchivePath -and $checksumsUrl) {
            $sumsPath = Join-Path $tempDir 'SHA256SUMS.txt'
            Invoke-WebRequest -Uri $checksumsUrl -OutFile $sumsPath -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 60
            $sumsLines = Get-Content -LiteralPath $sumsPath -Encoding UTF8
            foreach ($line in $sumsLines) {
                if ($line -match '^\s*([a-fA-F0-9]{64})\s+\*?(.+?)\s*$') {
                    if ($Matches[2] -eq $assetName) {
                        $expectedHash = $Matches[1].ToUpperInvariant()
                        break
                    }
                }
            }
            if (-not $expectedHash) {
                throw "Checksum for $assetName was not found in SHA256SUMS.txt."
            }
        }
        if ($expectedHash) {
            $actualHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToUpperInvariant()
            if ($actualHash -ne $expectedHash) {
                throw "SHA-256 verification failed! Expected $expectedHash, got $actualHash."
            }
            Write-Host "SHA-256 verified: $actualHash"
        } elseif ($isPinnedTag -and -not $ArchivePath) {
            throw 'Missing SHA-256 checksum for pinned release.'
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
        if (Test-Path -LiteralPath $InstallRoot) {
            if (-not (Test-Path -LiteralPath $setupFile -PathType Leaf)) {
                throw "Install folder is occupied by another directory: $InstallRoot. Refusing overwrite."
            }
            $oldArchive = "$InstallRoot.prev-" + [Guid]::NewGuid().ToString('N').Substring(0,8)
            Move-Item -LiteralPath $InstallRoot -Destination $oldArchive -ErrorAction Stop
            try {
                Move-Item -LiteralPath $candidates[0].FullName -Destination $InstallRoot -ErrorAction Stop
            } catch {
                if (-not (Test-Path -LiteralPath $InstallRoot) -and (Test-Path -LiteralPath $oldArchive)) {
                    Move-Item -LiteralPath $oldArchive -Destination $InstallRoot -ErrorAction SilentlyContinue
                }
                throw
            }
            if ($backupDir) { Restore-WinOwnerState $backupDir $InstallRoot }
        } else {
            Move-Item -LiteralPath $candidates[0].FullName -Destination $InstallRoot -ErrorAction Stop
            if ($backupDir) { Restore-WinOwnerState $backupDir $InstallRoot }
        }
    }
    Write-Host ''
    Write-Host 'Installing and checking Windows MCP...'
    $setupPs1 = Join-Path $InstallRoot 'scripts\Setup-WinOwner.ps1'
    $installerArgs = @('-NoOpenGuide','-Port',[string]$Port)
    if ($NoPythonInstall) { $installerArgs += '-NoPythonInstall' }
    if ($Update) { $installerArgs += '-Upgrade' }
    & $setupPs1 @installerArgs
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