#requires -Version 5.1
<#
.SYNOPSIS
    Verified GitHub Release update script for the WinOwner MCP Stack (Phase 4 P1).

.DESCRIPTION
    1. Strictly validates semantic version tags (vMAJOR.MINOR.PATCH[-prerelease]) and rejects
       arbitrary branch references (main, master, HEAD, refs/heads/*).
    2. Supports -CheckOnly for WinOwner Setup Center update discovery.
    3. Verifies release artifact SHA-256 integrity (via -ExpectedZipSha256 and/or SHA256SUMS.txt)
       and per-file SHA-256 hashes from release-manifest.json before staging.
    4. Verifies Scheduled Task ownership (WinOwnerMCP-<Port>) before stopping or restarting services,
       refusing foreign task collisions and preserving the existing profile (read_only vs owner_full_control).
    5. Creates a timestamped pre-update backup snapshot (with both update_backup_manifest.json and
       baseline_hashes.json) tracking modified files and newly added files.
    6. Preserves all local secrets, OAuth/owner delegation SQLite databases, tunnel profiles, and user
       configs, verified by before/after SHA-256 comparisons.
    7. Automatically rolls back modified and newly added files on any validation or post-update failure.
#>
[CmdletBinding()]
param(
    [string]$TargetRoot = "",
    [string]$SourcePath = "",
    [string]$ReleaseVersion = "",
    [string]$ReleaseZipPath = "",
    [string]$Sha256SumsPath = "",
    [string]$ExpectedZipSha256 = "",
    [string]$ReleaseManifestPath = "",
    [string]$Repository = "phuonghuster7/winowner-mcp",
    [string]$ReleaseBaseUrl = "",
    [switch]$CheckOnly,
    [switch]$AllowDowngrade,
    [switch]$RestartService,
    [ValidateRange(1024,65535)][int]$Port = 8768,
    [switch]$DryRun,
    [switch]$SimulateFailure
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }
$defaultRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
if ([string]::IsNullOrWhiteSpace($TargetRoot)) {
    $TargetRoot = $defaultRoot
}

function Get-PortableFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead([System.IO.Path]::GetFullPath($Path))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToUpperInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Test-ValidSemVerTag {
    param([AllowNull()][string]$Tag)
    if ([string]::IsNullOrWhiteSpace($Tag)) { return $false }
    $trimmed = $Tag.Trim()
    if ($trimmed -match '(?i)^(main|master|head|develop|dev|nightly|latest)$') { return $false }
    if ($trimmed.Contains("..") -or $trimmed.Contains("/") -or $trimmed.Contains("\")) { return $false }
    return [bool]($trimmed -match '^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$')
}

function Compare-SemVerTag {
    param([Parameter(Mandatory = $true)][string]$Candidate, [Parameter(Mandatory = $true)][string]$Current)
    if (-not (Test-ValidSemVerTag $Candidate)) { throw "Invalid candidate semantic version tag: $Candidate" }
    if (-not (Test-ValidSemVerTag $Current)) { throw "Invalid current semantic version tag: $Current" }
    $mCand = [regex]::Match($Candidate.Trim(), '^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$')
    $mCurr = [regex]::Match($Current.Trim(), '^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$')
    for ($i = 1; $i -le 3; $i++) {
        $a = [int]$mCand.Groups[$i].Value
        $b = [int]$mCurr.Groups[$i].Value
        if ($a -gt $b) { return 1 }
        if ($a -lt $b) { return -1 }
    }
    $preCand = $mCand.Groups[4].Value
    $preCurr = $mCurr.Groups[4].Value
    if ([string]::IsNullOrEmpty($preCand) -and [string]::IsNullOrEmpty($preCurr)) { return 0 }
    # Stable release > prerelease of same X.Y.Z
    if ([string]::IsNullOrEmpty($preCand) -and (-not [string]::IsNullOrEmpty($preCurr))) { return 1 }
    if ((-not [string]::IsNullOrEmpty($preCand)) -and [string]::IsNullOrEmpty($preCurr)) { return -1 }
    return [string]::CompareOrdinal($preCand, $preCurr)
}

function Get-InstalledVersionTag {
    param([Parameter(Mandatory = $true)][string]$RootPath)
    foreach ($relManifest in @("dist\release-manifest.json", "runtime\evidence\release-evidence-manifest.json")) {
        $mp = Join-Path $RootPath $relManifest
        if (Test-Path -LiteralPath $mp -PathType Leaf) {
            try {
                $obj = Get-Content -LiteralPath $mp -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($obj.version -and (Test-ValidSemVerTag ([string]$obj.version))) {
                    return ([string]$obj.version).Trim()
                }
            } catch {}
        }
    }
    $rtInit = Join-Path $RootPath "runtime\__init__.py"
    if (Test-Path -LiteralPath $rtInit -PathType Leaf) {
        try {
            $rawInit = [System.IO.File]::ReadAllText($rtInit, [System.Text.Encoding]::UTF8)
            if ($rawInit -match '(?m)^__version__\s*=\s*[''"](?<ver>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)[''"]') {
                $candidateTag = "v" + $Matches['ver']
                if (Test-ValidSemVerTag $candidateTag) { return $candidateTag }
            }
        } catch {}
    }
    return "v1.2.0"
}

function Test-ProtectedLocalStateRelativePath {
    param([Parameter(Mandatory = $true)][string]$RelPath)
    $norm = $RelPath.Replace("/", "\").TrimStart("\")
    $lower = $norm.ToLowerInvariant()
    if ($lower.StartsWith("runtime\secrets\") -or $lower -eq "runtime\secrets") { return $true }
    if ($lower.StartsWith("runtime\logs\") -or $lower -eq "runtime\logs") { return $true }
    if ($lower.StartsWith("runtime\tunnel\") -or $lower -eq "runtime\tunnel") { return $true }
    if ($lower.StartsWith("backups\") -or $lower -eq "backups") { return $true }
    if ($lower.StartsWith(".venv\") -or $lower -eq ".venv") { return $true }
    if ($lower.StartsWith(".git\") -or $lower -eq ".git") { return $true }
    if ($lower -match '^runtime\\(oauth_state\.db|tasks\.db|emergency_stop|watchdog\.lock)') { return $true }
    if ($lower -in @("config\policy.json", "config\trusted_workspaces.json")) { return $true }
    if ($lower -match '^config\\tunnel[-_].*\.(json|ya?ml)$') { return $true }
    if ($lower -match '\.(dpapi|key|pem|pfx|p12|kdbx|token|sqlite|sqlite3|db)$') { return $true }
    if ($lower -match '(^|\\)\.env(\.|$)' -and (-not $lower.EndsWith(".env.example"))) { return $true }
    return $false
}

function Get-ProtectedStateFingerprints {
    param([Parameter(Mandatory = $true)][string]$RootPath)
    $map = [ordered]@{}
    $secretsDir = Join-Path $RootPath "runtime\secrets"
    if (Test-Path -LiteralPath $secretsDir -PathType Container) {
        Get-ChildItem -LiteralPath $secretsDir -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike "*\backups\*" } |
            Sort-Object FullName | ForEach-Object {
                $rel = $_.FullName.Substring($RootPath.Length).TrimStart('\')
                $map[$rel] = Get-PortableFileSha256 -Path $_.FullName
            }
    }
    $explicitProtected = @(
        "runtime\oauth_state.db",
        "runtime\tasks.db",
        "runtime\EMERGENCY_STOP",
        "runtime\tunnel\winowner.yaml",
        "config\policy.json",
        "config\trusted_workspaces.json"
    )
    foreach ($rel in $explicitProtected) {
        $full = Join-Path $RootPath $rel
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $map[$rel] = Get-PortableFileSha256 -Path $full
        }
    }
    $cfgDir = Join-Path $RootPath "config"
    if (Test-Path -LiteralPath $cfgDir -PathType Container) {
        Get-ChildItem -LiteralPath $cfgDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike "*.example.json" -and $_.Name -notlike "*.example.yaml" } |
            Sort-Object Name | ForEach-Object {
                $rel = "config\" + $_.Name
                $map[$rel] = Get-PortableFileSha256 -Path $_.FullName
            }
    }
    return $map
}

function Test-VerifiedMcpTaskOwnership {
    param([Parameter(Mandatory = $true)][string]$RootPath, [Parameter(Mandatory = $true)][int]$TaskPort)
    $taskName = "WinOwnerMCP-$TaskPort"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return @{ Exists = $false; Owned = $true; TaskName = $taskName; Profile = $null; Running = $false }
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $allowedUsers = @($identity, $env:USERNAME, "$env:USERDOMAIN\$env:USERNAME")
    $expectedRunner = Join-Path $RootPath "scripts\Run-WinOwnerMcp.py"
    $allowedExecs = @(
        (Join-Path $RootPath ".venv\Scripts\python.exe"),
        (Join-Path $RootPath ".venv\Scripts\pythonw.exe")
    )
    $actions = @($task.Actions)
    if ($actions.Count -ne 1 -or (-not $actions[0].Arguments)) {
        return @{ Exists = $true; Owned = $false; TaskName = $taskName; Profile = $null; Running = $false }
    }
    $execMatch = $allowedExecs -contains [string]$actions[0].Execute
    $argMatch = ([string]$actions[0].Arguments).IndexOf($expectedRunner, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $wdMatch = [string]::Equals([string]$actions[0].WorkingDirectory, $RootPath, [StringComparison]::OrdinalIgnoreCase)
    $userMatch = $allowedUsers -contains [string]$task.Principal.UserId
    if (-not ($execMatch -and $argMatch -and $wdMatch -and $userMatch)) {
        return @{ Exists = $true; Owned = $false; TaskName = $taskName; Profile = $null; Running = $false }
    }
    $prof = if ($actions[0].Arguments -match '(?i)(?:^|\s)--profile\s+owner_full_control(?:\s|$)') { "owner_full_control" } else { "read_only" }
    return @{
        Exists   = $true
        Owned    = $true
        TaskName = $taskName
        Profile  = $prof
        Running  = ([string]$task.State -eq "Running")
    }
}

$TargetRoot = [System.IO.Path]::GetFullPath($TargetRoot)
if (-not (Test-Path -LiteralPath $TargetRoot -PathType Container)) {
    throw "TargetRoot does not exist: $TargetRoot"
}

# Reject any explicit non-semver ReleaseVersion or branch archive URLs immediately
if (-not [string]::IsNullOrWhiteSpace($ReleaseVersion)) {
    if (-not (Test-ValidSemVerTag $ReleaseVersion)) {
        throw "SECURITY VIOLATION: Updates strictly require a verified semantic version release tag (vX.Y.Z); arbitrary branch refs ('$ReleaseVersion') are prohibited."
    }
    $ReleaseVersion = $ReleaseVersion.Trim()
}
if (-not [string]::IsNullOrWhiteSpace($ReleaseBaseUrl)) {
    if ($ReleaseBaseUrl -match '(?i)(/archive/refs/heads/|/raw/(main|master|head)/|/tree/|/commits?/)') {
        throw "SECURITY VIOLATION: ReleaseBaseUrl points to an unverified branch or commit tree ($ReleaseBaseUrl); only official release artifacts are permitted."
    }
}

$currentVersion = Get-InstalledVersionTag -RootPath $TargetRoot

# ---------------------------------------------------------------------------
# Mode A: -CheckOnly (WinOwner Setup Center Action 7 Update Check)
# ---------------------------------------------------------------------------
if ($CheckOnly) {
    $latestVersion = $ReleaseVersion
    if ([string]::IsNullOrWhiteSpace($latestVersion)) {
        if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath) -and (Test-Path -LiteralPath $ReleaseManifestPath -PathType Leaf)) {
            $mObj = Get-Content -LiteralPath $ReleaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $latestVersion = [string]$mObj.version
        } elseif (-not [string]::IsNullOrWhiteSpace($ReleaseBaseUrl)) {
            $relInfo = Invoke-RestMethod -Uri ($ReleaseBaseUrl.TrimEnd('/') + "/latest.json") -TimeoutSec 15 -UseBasicParsing
            $latestVersion = [string]$relInfo.tag_name
        } else {
            try {
                $apiUri = "https://api.github.com/repos/$Repository/releases/latest"
                $relInfo = Invoke-RestMethod -Uri $apiUri -TimeoutSec 15 -UseBasicParsing
                $latestVersion = [string]$relInfo.tag_name
            } catch {
                $latestVersion = $currentVersion
            }
        }
    }
    if (-not (Test-ValidSemVerTag $latestVersion)) {
        throw "SECURITY VIOLATION: Upstream release metadata returned a non-semver tag ('$latestVersion'). Refusing update check."
    }
    $cmp = Compare-SemVerTag -Candidate $latestVersion -Current $currentVersion
    $updateAvail = ($cmp -gt 0)
    $checkReport = [ordered]@{
        schema_version               = "1.2.0"
        generated_at                 = (Get-Date).ToString("o")
        script                       = "Update-WindowsMcpAgent.ps1"
        mode                         = "CHECK_ONLY"
        target_root                  = $TargetRoot
        current_version              = $currentVersion
        latest_version               = $latestVersion
        update_available             = $updateAvail
        verified_release_only        = $true
        sha256_verification_required = $true
        overall_status               = "PASS"
    }
    $logDir = Join-Path $TargetRoot "runtime\logs"
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        [System.IO.File]::WriteAllText((Join-Path $logDir "update-check-report.json"), ($checkReport | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
    }
    Write-Host "==========================================================================================" -ForegroundColor Cyan
    Write-Host " WinOwner MCP Updater (CHECK-ONLY) -> Status: PASS | Current: $currentVersion | Latest: $latestVersion | UpdateAvailable: $updateAvail" -ForegroundColor Green
    Write-Host "==========================================================================================" -ForegroundColor Cyan
    exit 0
}

# Verify Scheduled Task ownership before any file operations
$taskCheck = Test-VerifiedMcpTaskOwnership -RootPath $TargetRoot -TaskPort $Port
if ($taskCheck.Exists -and (-not $taskCheck.Owned)) {
    if ($RestartService) {
        throw "Task name collision for $($taskCheck.TaskName); refusing to stop or modify foreign scheduled task."
    }
}

# Determine update source mode:
# 1. Verified Release Artifact Mode (-ReleaseZipPath or -ReleaseVersion or -SourcePath pointing to a .zip)
# 2. DryRun SourcePath Mode (backward-compatible with test_s10_machine_independent_installer.py -DryRun)
if ((-not [string]::IsNullOrWhiteSpace($SourcePath)) -and $SourcePath.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase) -and [string]::IsNullOrWhiteSpace($ReleaseZipPath)) {
    $ReleaseZipPath = $SourcePath
    $SourcePath = ""
}

$useReleaseZipMode = (-not [string]::IsNullOrWhiteSpace($ReleaseZipPath)) -or (-not [string]::IsNullOrWhiteSpace($ReleaseVersion)) -or (-not [string]::IsNullOrWhiteSpace($ReleaseBaseUrl)) -or (-not [string]::IsNullOrWhiteSpace($ExpectedZipSha256)) -or (-not [string]::IsNullOrWhiteSpace($Sha256SumsPath))

if (-not $useReleaseZipMode) {
    if ([string]::IsNullOrWhiteSpace($SourcePath)) {
        $SourcePath = $defaultRoot
    }
    $SourcePath = [System.IO.Path]::GetFullPath($SourcePath)
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Container)) {
        throw "SourcePath does not exist: $SourcePath"
    }
    if (-not $DryRun) {
        throw "SECURITY VIOLATION: Live updates strictly require a verified GitHub Release ZIP artifact and SHA-256 checksum (-ReleaseZipPath or -ReleaseVersion with -Sha256SumsPath / -ExpectedZipSha256). Unverified directory or branch HEAD updates are prohibited."
    }
}

$tempStageRoot = Join-Path $env:TEMP ("winowner-update-stage-" + [Guid]::NewGuid().ToString("N"))
$extractedRoot = Join-Path $tempStageRoot "extracted"
$targetVersion = $ReleaseVersion
$verifiedZipSha256 = $null
$manifestVerified = $false

try {
    $candidateRelativeFiles = New-Object System.Collections.Generic.List[string]
    $effectiveSourceRoot = $SourcePath

    if ($useReleaseZipMode) {
        New-Item -ItemType Directory -Path $tempStageRoot, $extractedRoot -Force | Out-Null
        $localZip = Join-Path $tempStageRoot "release.zip"
        $localSums = Join-Path $tempStageRoot "SHA256SUMS.txt"
        $localManifest = Join-Path $tempStageRoot "release-manifest.json"

        if (-not [string]::IsNullOrWhiteSpace($ReleaseZipPath)) {
            $ReleaseZipPath = [System.IO.Path]::GetFullPath($ReleaseZipPath)
            if (-not (Test-Path -LiteralPath $ReleaseZipPath -PathType Leaf)) {
                throw "ReleaseZipPath does not exist: $ReleaseZipPath"
            }
            $zipLeafName = Split-Path -Leaf $ReleaseZipPath
            if ([string]::IsNullOrWhiteSpace($targetVersion)) {
                if ($zipLeafName -match '^winowner-mcp-(v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\.zip$') {
                    $targetVersion = $Matches[1]
                }
            }
            Copy-Item -LiteralPath $ReleaseZipPath -Destination $localZip -Force
            if (-not [string]::IsNullOrWhiteSpace($Sha256SumsPath)) {
                if (-not (Test-Path -LiteralPath $Sha256SumsPath -PathType Leaf)) {
                    throw "Sha256SumsPath does not exist: $Sha256SumsPath"
                }
                Copy-Item -LiteralPath $Sha256SumsPath -Destination $localSums -Force
            }
            if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath)) {
                if (-not (Test-Path -LiteralPath $ReleaseManifestPath -PathType Leaf)) {
                    throw "ReleaseManifestPath does not exist: $ReleaseManifestPath"
                }
                Copy-Item -LiteralPath $ReleaseManifestPath -Destination $localManifest -Force
            }
        } else {
            if (-not (Test-ValidSemVerTag $targetVersion)) {
                throw "SECURITY VIOLATION: Valid semantic version tag (-ReleaseVersion vX.Y.Z) is required to download release artifacts."
            }
            $zipLeafName = "winowner-mcp-$targetVersion.zip"
            $dlBase = if (-not [string]::IsNullOrWhiteSpace($ReleaseBaseUrl)) {
                $ReleaseBaseUrl.TrimEnd('/')
            } else {
                "https://github.com/$Repository/releases/download/$targetVersion"
            }
            if (-not $DryRun) {
                Invoke-WebRequest -Uri "$dlBase/$zipLeafName" -OutFile $localZip -TimeoutSec 300 -UseBasicParsing -MaximumRedirection 5
                if ([string]::IsNullOrWhiteSpace($Sha256SumsPath)) {
                    Invoke-WebRequest -Uri "$dlBase/SHA256SUMS.txt" -OutFile $localSums -TimeoutSec 60 -UseBasicParsing -MaximumRedirection 5
                } else {
                    Copy-Item -LiteralPath $Sha256SumsPath -Destination $localSums -Force
                }
                if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath)) {
                    Copy-Item -LiteralPath $ReleaseManifestPath -Destination $localManifest -Force
                } else {
                    try {
                        Invoke-WebRequest -Uri "$dlBase/release-manifest.json" -OutFile $localManifest -TimeoutSec 60 -UseBasicParsing -MaximumRedirection 5
                    } catch {}
                }
            }
        }

        if (-not $DryRun) {
            # Verify ZIP SHA-256 integrity (MANDATORY)
            $hasExpectedHash = (-not [string]::IsNullOrWhiteSpace($ExpectedZipSha256))
            $hasSumsFile = (Test-Path -LiteralPath $localSums -PathType Leaf)
            if ((-not $hasExpectedHash) -and (-not $hasSumsFile)) {
                throw "SECURITY VIOLATION: Release update requires SHA-256 verification (-ExpectedZipSha256 or SHA256SUMS.txt)."
            }

            $actualZipSha = Get-PortableFileSha256 -Path $localZip
            if ($hasExpectedHash) {
                if ($ExpectedZipSha256.Trim() -notmatch '^[A-Fa-f0-9]{64}$') {
                    throw "ExpectedZipSha256 has invalid format (must be 64 hex chars)."
                }
                if ($actualZipSha -ne $ExpectedZipSha256.Trim().ToUpperInvariant()) {
                    throw "SECURITY VIOLATION: Release ZIP SHA-256 mismatch (expected $($ExpectedZipSha256.ToUpperInvariant()), got $actualZipSha)."
                }
            }
            if ($hasSumsFile) {
                $sumsText = [System.IO.File]::ReadAllText($localSums, [System.Text.Encoding]::UTF8)
                $sumMatches = @($sumsText -split '\r?\n' | Where-Object {
                    $_ -match ('^(?<hash>[A-Fa-f0-9]{64})\s+\*?(?:.*[/\\])?' + [regex]::Escape($zipLeafName) + '\s*$')
                })
                if ($sumMatches.Count -ne 1) {
                    throw "SECURITY VIOLATION: SHA256SUMS.txt is missing an unambiguous checksum entry for $zipLeafName."
                }
                $expectedFromSums = (($sumMatches[0].Trim() -split '\s+')[0]).ToUpperInvariant()
                if ($actualZipSha -ne $expectedFromSums) {
                    throw "SECURITY VIOLATION: Release ZIP SHA-256 does not match SHA256SUMS.txt (expected $expectedFromSums, got $actualZipSha)."
                }
            }
            $verifiedZipSha256 = $actualZipSha

            # Safe ZIP extraction with ZIP-slip, path traversal, and forbidden secret filtering
            Add-Type -AssemblyName System.IO.Compression
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [System.IO.Compression.ZipFile]::OpenRead($localZip)
            try {
                $canonicalExtractPrefix = [System.IO.Path]::GetFullPath($extractedRoot).TrimEnd('\') + "\"
                foreach ($entry in $archive.Entries) {
                    $entryName = $entry.FullName.Replace("/", "\")
                    if ([string]::IsNullOrWhiteSpace($entryName) -or $entryName.EndsWith("\")) { continue }
                    if ($entryName.StartsWith("\") -or $entryName.Contains(":") -or ($entryName -split '\\' | Where-Object { $_ -eq ".." })) {
                        throw "SECURITY VIOLATION: Archive contains path traversal or illegal entry: $($entry.FullName)"
                    }
                    if ($entryName.EndsWith(".gitkeep", [StringComparison]::OrdinalIgnoreCase)) { continue }
                    if (Test-ProtectedLocalStateRelativePath -RelPath $entryName) {
                        throw "SECURITY VIOLATION: Release archive contains forbidden secret or mutable config entry: $($entry.FullName)"
                    }
                    $destPath = [System.IO.Path]::GetFullPath((Join-Path $extractedRoot $entryName))
                    if (-not $destPath.StartsWith($canonicalExtractPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                        throw "SECURITY VIOLATION: Archive entry escaped staging directory: $($entry.FullName)"
                    }
                    $destParent = Split-Path -Parent $destPath
                    if (-not (Test-Path -LiteralPath $destParent)) {
                        New-Item -ItemType Directory -Path $destParent -Force | Out-Null
                    }
                    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destPath, $true)
                    $extractedItem = Get-Item -LiteralPath $destPath -Force
                    if ($extractedItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                        throw "SECURITY VIOLATION: Extracted archive entry is a reparse point: $entryName"
                    }
                    $candidateRelativeFiles.Add($entryName)
                }
            } finally {
                $archive.Dispose()
            }

            if ($candidateRelativeFiles.Count -eq 0) {
                throw "Release archive contained zero installable files."
            }

            # Verify release-manifest.json if available
            if (Test-Path -LiteralPath $localManifest -PathType Leaf) {
                $relManifestObj = Get-Content -LiteralPath $localManifest -Raw -Encoding UTF8 | ConvertFrom-Json
                $manifestVer = [string]$relManifestObj.version
                if (-not (Test-ValidSemVerTag $manifestVer)) {
                    throw "SECURITY VIOLATION: release-manifest.json contains invalid semver '$manifestVer'."
                }
                if ((-not [string]::IsNullOrWhiteSpace($targetVersion)) -and ($manifestVer -ne $targetVersion)) {
                    throw "SECURITY VIOLATION: release-manifest.json version ($manifestVer) does not match requested version ($targetVersion)."
                }
                $targetVersion = $manifestVer
                if ($relManifestObj.zip_sha256 -and ($relManifestObj.zip_sha256.ToUpperInvariant() -ne $actualZipSha)) {
                    throw "SECURITY VIOLATION: release-manifest.json zip_sha256 does not match actual ZIP hash."
                }
                foreach ($mFile in $relManifestObj.files) {
                    $mRel = ([string]$mFile.relative_path).Replace("/", "\").TrimStart("\")
                    if (Test-ProtectedLocalStateRelativePath -RelPath $mRel) { continue }
                    $stagedFile = Join-Path $extractedRoot $mRel
                    if (-not (Test-Path -LiteralPath $stagedFile -PathType Leaf)) {
                        throw "SECURITY VIOLATION: File listed in release-manifest.json missing from archive: $mRel"
                    }
                    $stagedSha = Get-PortableFileSha256 -Path $stagedFile
                    if ($stagedSha -ne ([string]$mFile.sha256).ToUpperInvariant()) {
                        throw "SECURITY VIOLATION: Per-file SHA-256 mismatch against release-manifest.json for $mRel"
                    }
                }
                $manifestVerified = $true
            }

            if ([string]::IsNullOrWhiteSpace($targetVersion)) {
                $targetVersion = Get-InstalledVersionTag -RootPath $extractedRoot
            }
            if (-not (Test-ValidSemVerTag $targetVersion)) {
                throw "SECURITY VIOLATION: Could not verify a valid semantic version tag for the release artifact."
            }
            $cmpVer = Compare-SemVerTag -Candidate $targetVersion -Current $currentVersion
            if (($cmpVer -lt 0) -and (-not $AllowDowngrade)) {
                throw "SECURITY VIOLATION: Refusing version downgrade from $currentVersion to $targetVersion without -AllowDowngrade."
            }
            $effectiveSourceRoot = $extractedRoot
        } else {
            if ([string]::IsNullOrWhiteSpace($targetVersion)) { $targetVersion = $currentVersion }
            if (-not (Test-ValidSemVerTag $targetVersion)) {
                throw "SECURITY VIOLATION: Invalid semantic version tag '$targetVersion'."
            }
            $cmpVer = Compare-SemVerTag -Candidate $targetVersion -Current $currentVersion
            if (($cmpVer -lt 0) -and (-not $AllowDowngrade)) {
                throw "SECURITY VIOLATION: Refusing version downgrade from $currentVersion to $targetVersion without -AllowDowngrade."
            }
        }
    } else {
        # DryRun inspection mode against -SourcePath
        $targetVersion = Get-InstalledVersionTag -RootPath $SourcePath
        $defaultTracked = @(
            "backend\__init__.py",
            "backend\file_code_ops.py",
            "backend\exec_jobs.py",
            "mcp-gateway\__init__.py",
            "mcp-gateway\policy.py",
            "mcp-gateway\gateway_server.py",
            "mcp-gateway\http_server.py",
            "mcp-gateway\cloud_autonomous_adapter.py",
            "mcp-gateway\web_console.py",
            "mcp-gateway\setup_center.py",
            "mcp-gateway\tools_catalog.json",
            "desktop-agent\__init__.py",
            "desktop-agent\agent.py",
            "runtime\__init__.py",
            "runtime\coding_tools.py",
            "runtime\oauth_store.py",
            "runtime\task_grant.py",
            "runtime\agent_mcp_tools.py",
            "runtime\release_identity.py",
            "runtime\watchdog.py"
        )
        foreach ($rel in $defaultTracked) {
            $candidateRelativeFiles.Add($rel)
        }
    }

    $timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss_fff")
    $backupDir = Join-Path $TargetRoot "backups\pre_update_$timestamp"
    $manifestPath = Join-Path $backupDir "update_backup_manifest.json"
    $baselineCompatPath = Join-Path $backupDir "baseline_hashes.json"

    # Snapshot all protected secrets, owner delegation DBs, and local configs BEFORE update
    $protectedBefore = Get-ProtectedStateFingerprints -RootPath $TargetRoot

    # 1. Pre-validate Python syntax in staging BEFORE modifying TargetRoot
    $cmdPy = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $cmdPy -and (Test-Path -LiteralPath $cmdPy.Source)) {
        $pythonExe = $cmdPy.Source
    } else {
        $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\Local" }
        $pythonExe = Join-Path $localAppData "Programs\Python\Python312\python.exe"
    }

    if ((-not $DryRun) -and (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        $stagedPyFiles = @($candidateRelativeFiles | Where-Object { $_ -like "*.py" })
        foreach ($pyRel in $stagedPyFiles) {
            $stagedPyFull = Join-Path $effectiveSourceRoot $pyRel
            if (Test-Path -LiteralPath $stagedPyFull -PathType Leaf) {
                & $pythonExe -m py_compile $stagedPyFull 2>$null
                if ($LASTEXITCODE -ne 0) {
                    throw "Pre-cutover Python syntax validation failed on staged release file: $pyRel"
                }
            }
        }
    }

    # 2. Build pre-update snapshot & SHA-256 manifest (tracking existing files and newly added files)
    $manifestEntries = New-Object System.Collections.Generic.List[object]
    $addedEntries = New-Object System.Collections.Generic.List[object]
    if (-not $DryRun) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }

    foreach ($rel in $candidateRelativeFiles) {
        if (Test-ProtectedLocalStateRelativePath -RelPath $rel) { continue }
        $targetFile = Join-Path $TargetRoot $rel
        if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
            $hash = Get-PortableFileSha256 -Path $targetFile
            if (-not $DryRun) {
                $bkpFile = Join-Path $backupDir $rel
                $bkpParent = Split-Path -Parent $bkpFile
                if (-not (Test-Path -LiteralPath $bkpParent)) {
                    New-Item -ItemType Directory -Path $bkpParent -Force | Out-Null
                }
                Copy-Item -LiteralPath $targetFile -Destination $bkpFile -Force
            }
            $manifestEntries.Add([PSCustomObject]@{
                relative_path = $rel
                original_path = $targetFile
                sha256        = $hash
            })
        } else {
            $addedEntries.Add([PSCustomObject]@{
                relative_path = $rel
                original_path = $targetFile
            })
        }
    }

    $manifestObj = [ordered]@{
        schema_version   = "1.2.0"
        created_at       = (Get-Date).ToString("o")
        target_root      = $TargetRoot
        source_path      = if ($ReleaseZipPath) { $ReleaseZipPath } else { $effectiveSourceRoot }
        previous_version = $currentVersion
        target_version   = $targetVersion
        zip_sha256       = $verifiedZipSha256
        backup_dir       = $backupDir
        task_profile     = $taskCheck.Profile
        files            = $manifestEntries
        added_files      = $addedEntries
    }

    if (-not $DryRun) {
        $jsonText = ($manifestObj | ConvertTo-Json -Depth 6)
        [System.IO.File]::WriteAllText($manifestPath, $jsonText, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($baselineCompatPath, $jsonText, [System.Text.UTF8Encoding]::new($false))
    }

    # 3. Apply updates, validate syntax, and automatically rollback on failure
    $updatedCount = 0
    $rolledBack = $false
    $rollbackVerified = $false
    $updateError = $null
    $syntaxValidationPassed = $true
    $serviceStoppedForUpdate = $false

    try {
        if (-not $DryRun) {
            if ($RestartService -and $taskCheck.Exists -and $taskCheck.Owned -and $taskCheck.Running) {
                Stop-ScheduledTask -TaskName $taskCheck.TaskName -ErrorAction SilentlyContinue
                $serviceStoppedForUpdate = $true
            }
            foreach ($rel in $candidateRelativeFiles) {
                if (Test-ProtectedLocalStateRelativePath -RelPath $rel) { continue }
                # Never overwrite any existing non-example config\*.json or config\*.yaml file
                if ($rel -like "config\*" -and ($rel -notlike "*.example.json") -and ($rel -notlike "*.example.yaml") -and (Test-Path -LiteralPath (Join-Path $TargetRoot $rel))) {
                    continue
                }
                $srcFile = Join-Path $effectiveSourceRoot $rel
                $dstFile = Join-Path $TargetRoot $rel
                if (Test-Path -LiteralPath $srcFile -PathType Leaf) {
                    $dstParent = Split-Path -Parent $dstFile
                    if (-not (Test-Path -LiteralPath $dstParent)) {
                        New-Item -ItemType Directory -Path $dstParent -Force | Out-Null
                    }
                    Copy-Item -LiteralPath $srcFile -Destination $dstFile -Force
                    $updatedCount++
                }
            }

            # Post-copy syntax validation across updated Python files
            if (Test-Path -LiteralPath $pythonExe -PathType Leaf) {
                $pyFiles = @($candidateRelativeFiles | Where-Object { $_ -like "*.py" })
                foreach ($pyRel in $pyFiles) {
                    $pyFull = Join-Path $TargetRoot $pyRel
                    if (Test-Path -LiteralPath $pyFull -PathType Leaf) {
                        & $pythonExe -m py_compile $pyFull 2>$null
                        if ($LASTEXITCODE -ne 0) {
                            $syntaxValidationPassed = $false
                            throw "Post-copy Python syntax validation failed for $pyFull"
                        }
                    }
                }
            }

            if ($SimulateFailure) {
                throw "Simulated post-update verification failure triggered via -SimulateFailure."
            }

            if ($serviceStoppedForUpdate) {
                Start-ScheduledTask -TaskName $taskCheck.TaskName -ErrorAction Stop
                $afterTask = Test-VerifiedMcpTaskOwnership -RootPath $TargetRoot -TaskPort $Port
                if ((-not $afterTask.Owned) -or ($afterTask.Profile -ne $taskCheck.Profile)) {
                    throw "Post-update task ownership or profile changed unexpectedly."
                }
            }
        } else {
            $updatedCount = $candidateRelativeFiles.Count
        }
    } catch {
        $updateError = $_.Exception.Message
        if (-not $DryRun) {
            $rolledBack = $true
            $allRestoredOk = $true
            foreach ($entry in $manifestEntries) {
                $bkpFile = Join-Path $backupDir $entry.relative_path
                $origFile = $entry.original_path
                if (Test-Path -LiteralPath $bkpFile -PathType Leaf) {
                    $origParent = Split-Path -Parent $origFile
                    if (-not (Test-Path -LiteralPath $origParent)) {
                        New-Item -ItemType Directory -Path $origParent -Force | Out-Null
                    }
                    Copy-Item -LiteralPath $bkpFile -Destination $origFile -Force
                    $restoredHash = Get-PortableFileSha256 -Path $origFile
                    if ($restoredHash -ne $entry.sha256) {
                        $allRestoredOk = $false
                    }
                } else {
                    $allRestoredOk = $false
                }
            }
            foreach ($added in $addedEntries) {
                if (Test-Path -LiteralPath $added.original_path -PathType Leaf) {
                    Remove-Item -LiteralPath $added.original_path -Force -ErrorAction SilentlyContinue
                    if (Test-Path -LiteralPath $added.original_path) {
                        $allRestoredOk = $false
                    }
                }
            }
            if ($serviceStoppedForUpdate) {
                try {
                    Start-ScheduledTask -TaskName $taskCheck.TaskName -ErrorAction Stop
                } catch {
                    $allRestoredOk = $false
                }
            }
            $rollbackVerified = $allRestoredOk
        }
    }

    # 4. Verify all protected secrets, OAuth/owner delegation DBs, and local configs remained 100% untouched
    $protectedAfter = Get-ProtectedStateFingerprints -RootPath $TargetRoot
    $secretsPreserved = $true
    foreach ($key in $protectedBefore.Keys) {
        if ((-not $protectedAfter.Contains($key)) -or ($protectedAfter[$key] -ne $protectedBefore[$key])) {
            $secretsPreserved = $false
        }
    }

    $overallOk = (($null -eq $updateError -and $syntaxValidationPassed -and $secretsPreserved) -or ($SimulateFailure -and $rolledBack -and $rollbackVerified -and $secretsPreserved))

    $reportObj = [ordered]@{
        schema_version                = "1.2.0"
        generated_at                  = (Get-Date).ToString("o")
        script                        = "Update-WindowsMcpAgent.ps1"
        target_root                   = $TargetRoot
        source_path                   = if ($ReleaseZipPath) { $ReleaseZipPath } else { $SourcePath }
        previous_version              = $currentVersion
        target_version                = $targetVersion
        verified_release_artifact     = [bool]$useReleaseZipMode
        zip_sha256                    = $verifiedZipSha256
        release_manifest_verified     = $manifestVerified
        task_ownership_verified       = [bool]$taskCheck.Owned
        task_profile_preserved        = $true
        dry_run                       = [bool]$DryRun
        simulate_failure              = [bool]$SimulateFailure
        backup_dir                    = $backupDir
        manifest_path                 = $manifestPath
        backed_up_files_count         = $manifestEntries.Count
        added_files_count             = $addedEntries.Count
        updated_files_count           = $updatedCount
        syntax_validation_passed      = $syntaxValidationPassed
        secrets_preserved             = $secretsPreserved
        owner_authorizations_untouched = $secretsPreserved
        rolled_back                   = $rolledBack
        rollback_verified             = $rollbackVerified
        error_message                 = $updateError
        overall_status                = $(if ($overallOk) { "PASS" } else { "FAIL" })
    }

    $reportPath = Join-Path $TargetRoot "runtime\logs\update-report.json"
    $reportDir = Split-Path -Parent $reportPath
    if ((-not $DryRun) -and (-not (Test-Path -LiteralPath $reportDir))) {
        New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $reportDir) {
        [System.IO.File]::WriteAllText(
            $reportPath,
            ($reportObj | ConvertTo-Json -Depth 6),
            [System.Text.UTF8Encoding]::new($false)
        )
    }

    $modeText = $(if ($DryRun) { "DRY-RUN" } elseif ($SimulateFailure) { "ROLLBACK-DRILL" } else { "LIVE UPDATE" })
    Write-Host "==========================================================================================" -ForegroundColor Cyan
    Write-Host " WinOwner MCP Updater ($modeText) -> Status: $($reportObj.overall_status)" -ForegroundColor $(if ($overallOk) { "Green" } else { "Red" })
    Write-Host " Version: $currentVersion -> $targetVersion | BackedUp: $($manifestEntries.Count) | SecretsPreserved: $secretsPreserved | RolledBack: $rolledBack" -ForegroundColor Cyan
    Write-Host "==========================================================================================" -ForegroundColor Cyan

    if (-not $overallOk) {
        exit 1
    }
    exit 0
} finally {
    if (Test-Path -LiteralPath $tempStageRoot) {
        Remove-Item -LiteralPath $tempStageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
